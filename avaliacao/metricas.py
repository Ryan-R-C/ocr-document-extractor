"""Métricas do avaliador, a partir dos resultados da esteira sobre o gabarito.

| Métrica            | Definição                                                                 |
|--------------------|---------------------------------------------------------------------------|
| Erro escapado      | campo crítico errado ou inventado em documento APROVADO ÷ campos críticos  |
|                    | aprovados                                                                 |
| Alucinação         | valor devolvido onde o gabarito diz AUSENTE ou ILEGIVEL (inclui o teste do |
|                    | campo apagado); meta: zero em críticos aprovados                          |
| Acurácia por campo | valor normalizado igual ao do gabarito ÷ campos com valor no gabarito     |
| Taxa de revisão    | documentos em REVISAO ÷ documentos                                        |
| Taxa de 422        | documentos recusados ÷ documentos, por motivo                             |
| Custo por documento| média e p95, em R$                                                        |
| Latência           | p50 e p95, total e por etapa                                              |
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from operator import attrgetter
from typing import Any

from app.api.contrato import StatusDocumento
from app.core.erros import FalhaExtracao
from app.core.pipeline import ResultadoPipeline
from app.schemas.campos import definicao
from avaliacao.gabarito import ItemGabarito

META_ERRO_ESCAPADO = 0.005
META_CUSTO_MEDIO_BRL = 0.15


@dataclass(frozen=True)
class RegistroDocumento:
    arquivo: str
    subtipo: str
    uf: str
    qualidade: str
    variante: bool
    desfecho: str  # APROVADO | REVISAO | RECUSADO | ERRO_PROVIDER | ERRO_REQUISICAO
    motivos: tuple[str, ...]
    subtipo_previsto: str | None
    custo_brl: float
    tempos_ms: dict[str, float]


@dataclass(frozen=True)
class RegistroCampo:
    arquivo: str
    campo: str
    critico: bool
    subtipo: str
    uf: str
    qualidade: str
    variante: bool
    campo_apagado: bool
    esperado_status: str
    doc_aprovado: bool
    devolveu_valor: bool
    correto: bool | None  # None quando o gabarito não tem valor

    @property
    def alucinacao(self) -> bool:
        return self.esperado_status != "LEGIVEL" and self.devolveu_valor

    @property
    def erro_escapado(self) -> bool:
        if not (self.doc_aprovado and self.critico):
            return False
        return self.alucinacao or self.correto is False


@dataclass
class Registros:
    documentos: list[RegistroDocumento] = field(default_factory=list)
    campos: list[RegistroCampo] = field(default_factory=list)


def _desfecho(resultado: ResultadoPipeline) -> tuple[str, tuple[str, ...]]:
    if resultado.resposta is not None:
        return resultado.resposta.status.value, ()
    falha = resultado.falha
    assert falha is not None
    motivos = tuple(m.codigo.value for m in falha.motivos)
    if isinstance(falha, FalhaExtracao):
        return "RECUSADO", motivos
    if falha.codigo.value in {"PROVIDER_INDISPONIVEL", "TEMPO_ESGOTADO"}:
        return "ERRO_PROVIDER", motivos
    return "ERRO_REQUISICAO", motivos


def registrar(registros: Registros, item: ItemGabarito, resultado: ResultadoPipeline) -> None:
    desfecho, motivos = _desfecho(resultado)
    resposta = resultado.resposta
    variante = item.variante_de is not None
    registros.documentos.append(
        RegistroDocumento(
            arquivo=item.arquivo,
            subtipo=item.subtipo.value,
            uf=item.uf.value,
            qualidade=item.qualidade,
            variante=variante,
            desfecho=desfecho,
            motivos=motivos,
            subtipo_previsto=resposta.documento.subtipo.value if resposta else None,
            custo_brl=resultado.rastro.custo_brl,
            tempos_ms=dict(resultado.rastro.tempos_ms),
        )
    )
    aprovado = resposta is not None and resposta.status is StatusDocumento.APROVADO
    for nome, esperado in item.campos.items():
        defn = definicao(item.subtipo, nome)
        assert defn is not None  # garantido pela validação do manifesto
        devolvido = resposta.campos.get(nome) if resposta else None
        devolveu_valor = devolvido is not None and devolvido.valor is not None
        correto: bool | None = None
        if esperado.tem_valor:
            alvo = defn.normalizar(esperado.valor) or (esperado.valor or "").strip().upper()
            correto = devolvido is not None and devolvido.valor_normalizado == alvo
        registros.campos.append(
            RegistroCampo(
                arquivo=item.arquivo,
                campo=nome,
                critico=defn.critico,
                subtipo=item.subtipo.value,
                uf=item.uf.value,
                qualidade=item.qualidade,
                variante=variante,
                campo_apagado=item.campo_apagado == nome,
                esperado_status=esperado.status,
                doc_aprovado=aprovado,
                devolveu_valor=devolveu_valor,
                correto=correto,
            )
        )


def percentil(valores: Sequence[float], p: float) -> float | None:
    """Percentil pelo método do posto mais próximo."""
    if not valores:
        return None
    ordenados = sorted(valores)
    posto = max(1, math.ceil(p / 100 * len(ordenados)))
    return ordenados[posto - 1]


def _taxa(parte: int, todo: int) -> float | None:
    return parte / todo if todo else None


def _metricas_documentos(docs: Sequence[RegistroDocumento]) -> dict[str, Any]:
    n = len(docs)
    desfechos = Counter(d.desfecho for d in docs)
    motivos_422 = Counter(m for d in docs if d.desfecho == "RECUSADO" for m in d.motivos)
    custos = [d.custo_brl for d in docs]
    etapas = sorted({e for d in docs for e in d.tempos_ms})
    latencias = {
        etapa: {
            "p50": percentil([d.tempos_ms[etapa] for d in docs if etapa in d.tempos_ms], 50),
            "p95": percentil([d.tempos_ms[etapa] for d in docs if etapa in d.tempos_ms], 95),
        }
        for etapa in etapas
    }
    return {
        "documentos": n,
        "desfechos": dict(desfechos),
        "taxa_aprovacao": _taxa(desfechos["APROVADO"], n),
        "taxa_revisao": _taxa(desfechos["REVISAO"], n),
        "taxa_422": _taxa(desfechos["RECUSADO"], n),
        "taxa_422_por_motivo": {m: _taxa(c, n) for m, c in sorted(motivos_422.items())},
        "taxa_erro_provider": _taxa(desfechos["ERRO_PROVIDER"], n),
        "subtipo_correto": _taxa(
            sum(1 for d in docs if d.subtipo_previsto == d.subtipo),
            sum(1 for d in docs if d.subtipo_previsto is not None),
        ),
        "custo_brl": {
            "media": sum(custos) / n if n else None,
            "p95": percentil(custos, 95),
            "total": sum(custos),
        },
        "latencia_ms": latencias,
    }


def _metricas_campos(campos: Sequence[RegistroCampo]) -> dict[str, Any]:
    criticos_aprovados = [c for c in campos if c.critico and c.doc_aprovado]
    escapados = sum(1 for c in criticos_aprovados if c.erro_escapado)
    com_valor = [c for c in campos if c.correto is not None]
    sem_valor = [c for c in campos if c.esperado_status != "LEGIVEL"]
    return {
        "campos": len(campos),
        "criticos_aprovados": len(criticos_aprovados),
        "erros_escapados": escapados,
        "taxa_erro_escapado": _taxa(escapados, len(criticos_aprovados)),
        "campos_sem_valor_no_gabarito": len(sem_valor),
        "alucinacoes": sum(1 for c in campos if c.alucinacao),
        "taxa_alucinacao": _taxa(sum(1 for c in sem_valor if c.alucinacao), len(sem_valor)),
        "alucinacoes_criticos_aprovados": sum(1 for c in criticos_aprovados if c.alucinacao),
        "acuracia": _taxa(sum(1 for c in com_valor if c.correto), len(com_valor)),
        "acuracia_criticos": _taxa(
            sum(1 for c in com_valor if c.correto and c.critico),
            sum(1 for c in com_valor if c.critico),
        ),
    }


def _agrupar[T](itens: Iterable[T], chave: Callable[[T], str]) -> dict[str, list[T]]:
    grupos: dict[str, list[T]] = defaultdict(list)
    for item in itens:
        grupos[chave(item)].append(item)
    return dict(sorted(grupos.items()))


def calcular(registros: Registros) -> dict[str, Any]:
    docs = [d for d in registros.documentos if not d.variante]
    campos = [c for c in registros.campos if not c.variante]
    docs_var = [d for d in registros.documentos if d.variante]
    apagados = [c for c in registros.campos if c.campo_apagado]

    def bloco(ds: Sequence[RegistroDocumento], cs: Sequence[RegistroCampo]) -> dict[str, Any]:
        return {**_metricas_documentos(ds), **_metricas_campos(cs)}

    por: dict[str, dict[str, Any]] = {}
    for dimensao in ("subtipo", "uf", "qualidade"):
        ds_g: dict[str, list[RegistroDocumento]] = _agrupar(docs, attrgetter(dimensao))
        cs_g: dict[str, list[RegistroCampo]] = _agrupar(campos, attrgetter(dimensao))
        por[dimensao] = {k: bloco(ds_g.get(k, []), cs_g.get(k, [])) for k in ds_g}
    por["campo"] = {
        k: {
            **_metricas_campos(v),
            "critico": "sim"
            if all(c.critico for c in v)
            else ("não" if not any(c.critico for c in v) else "depende do subtipo"),
        }
        for k, v in _agrupar(campos, lambda c: c.campo).items()
    }

    geral = bloco(docs, campos)
    todos_campos = registros.campos
    geral["alucinacoes_incluindo_campo_apagado"] = sum(1 for c in todos_campos if c.alucinacao)
    geral["alucinacoes_criticos_aprovados_incluindo_campo_apagado"] = sum(
        1 for c in todos_campos if c.alucinacao and c.critico and c.doc_aprovado
    )

    campo_apagado = {
        "variantes": len(docs_var),
        "desfechos": dict(Counter(d.desfecho for d in docs_var)),
        "campos_apagados": len(apagados),
        "alucinacoes": sum(1 for c in apagados if c.alucinacao),
        "alucinacoes_em_aprovados": sum(1 for c in apagados if c.alucinacao and c.doc_aprovado),
        "taxa_alucinacao": _taxa(sum(1 for c in apagados if c.alucinacao), len(apagados)),
    }

    metas = {
        "erro_escapado": _meta(geral["taxa_erro_escapado"], lambda v: v < META_ERRO_ESCAPADO),
        "alucinacao_criticos_aprovados": {
            "valor": geral["alucinacoes_criticos_aprovados_incluindo_campo_apagado"],
            "atingida": geral["alucinacoes_criticos_aprovados_incluindo_campo_apagado"] == 0,
        },
        "custo_medio_brl": _meta(geral["custo_brl"]["media"], lambda v: v < META_CUSTO_MEDIO_BRL),
    }
    erros_escapados = [
        {
            "arquivo": c.arquivo,
            "campo": c.campo,
            "tipo": "alucinacao" if c.alucinacao else "valor_errado",
        }
        for c in todos_campos
        if c.erro_escapado
    ]
    alucinacoes = [
        {"arquivo": c.arquivo, "campo": c.campo, "critico": c.critico, "aprovado": c.doc_aprovado}
        for c in todos_campos
        if c.alucinacao
    ]
    return {
        "geral": geral,
        "metas": metas,
        "por": por,
        "campo_apagado": campo_apagado,
        "erros_escapados": erros_escapados,
        "alucinacoes": alucinacoes,
    }


def _meta(valor: float | None, criterio: Callable[[float], bool]) -> dict[str, Any]:
    return {"valor": valor, "atingida": None if valor is None else criterio(valor)}
