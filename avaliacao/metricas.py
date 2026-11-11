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

# Plano, "Custos estimados" (R$ por documento, leitura principal + escalada; inclui 30% dos
# documentos com dois campos relidos pelo modelo de escalada).
PLANO_CUSTO_BRL: dict[tuple[str, str], float] = {
    ("gpt-6-luna", "gpt-6.1-sol"): 0.01,
    ("gpt-6.1-terra", "gpt-6.1-sol"): 0.04,
    ("gpt-6.1-sol", "gpt-6-astra"): 0.10,
    ("gpt-6-astra", "gpt-6-astra"): 0.37,
}
# Plano: 1.920 tokens de imagem (1.600 patches × 1,2) + 1.500 de instruções e schema.
PLANO_TOKENS_IMAGEM = 1920
PLANO_TOKENS_ENTRADA = 3420
PLANO_TOKENS_SAIDA = 600


@dataclass(frozen=True)
class RegistroChamada:
    etapa: str
    modelo: str
    entrada: int
    saida: int
    raciocinio: int
    imagem_estimada: int
    custo_brl: float
    latencia_ms: float


@dataclass(frozen=True)
class RegistroDocumento:
    id: str  # sha256 curto do arquivo: identifica o caso sem dado pessoal
    arquivo: str
    subtipo: str
    uf: str
    lado: str
    qualidade: str
    variante: bool
    desfecho: str  # APROVADO | REVISAO | RECUSADO | ERRO_PROVIDER | ERRO_REQUISICAO
    motivos: tuple[str, ...]
    subtipo_previsto: str | None
    custo_brl: float
    tempos_ms: dict[str, float]
    uf_prevista: str | None = None
    lado_previsto: str | None = None
    # Captura: o que o VLM disse, o que a heurística em CPU disse e o que saiu na resposta.
    captura_vlm: str | None = None
    copia_heuristica: bool | None = None
    print_heuristica: bool | None = None
    sinalizacoes: tuple[str, ...] = ()
    chamadas: tuple[RegistroChamada, ...] = ()


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
    id: str = ""
    status_leitura: str | None = None

    @property
    def alucinacao(self) -> bool:
        return self.esperado_status != "LEGIVEL" and self.devolveu_valor

    @property
    def erro_escapado(self) -> bool:
        if not (self.doc_aprovado and self.critico):
            return False
        return self.alucinacao or self.correto is False

    # Modo medição: o que aconteceria se todo campo LEGIVEL fosse aprovado direto, sem
    # segundo leitor nem regras. É o baseline das Fases 2 e 3.
    @property
    def aprovado_medicao(self) -> bool:
        return self.status_leitura == "LEGIVEL"

    @property
    def erro_escapado_medicao(self) -> bool:
        return self.critico and self.aprovado_medicao and (self.alucinacao or self.correto is False)


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
    rastro = resultado.rastro
    classificacao = rastro.leitura_visual or rastro.classificacao
    captura = rastro.captura
    registros.documentos.append(
        RegistroDocumento(
            id=item.sha256[:12],
            arquivo=item.arquivo,
            subtipo=item.subtipo.value,
            uf=item.uf.value,
            lado=item.lado.value,
            qualidade=item.qualidade,
            variante=variante,
            desfecho=desfecho,
            motivos=motivos,
            subtipo_previsto=classificacao.subtipo.value if classificacao else None,
            custo_brl=rastro.custo_brl,
            tempos_ms=dict(rastro.tempos_ms),
            uf_prevista=classificacao.uf.value if classificacao else None,
            lado_previsto=classificacao.lado.value if classificacao else None,
            captura_vlm=classificacao.qualidade_captura.value if classificacao else None,
            copia_heuristica=captura.copia_reprografica if captura else None,
            print_heuristica=captura.print_de_tela if captura else None,
            sinalizacoes=tuple(s.value for s in resposta.sinalizacoes) if resposta else (),
            chamadas=tuple(
                RegistroChamada(
                    etapa=c.etapa,
                    modelo=c.modelo,
                    entrada=c.tokens_entrada,
                    saida=c.tokens_saida,
                    raciocinio=c.tokens_raciocinio,
                    imagem_estimada=c.tokens_imagem_estimados,
                    custo_brl=c.custo_brl,
                    latencia_ms=c.latencia_ms,
                )
                for c in rastro.chamadas
                if c.tokens_entrada or c.tokens_saida
            ),
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
                id=item.sha256[:12],
                status_leitura=devolvido.status_leitura.value if devolvido else None,
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
        "uf_correta": _taxa(
            sum(1 for d in docs if d.uf_prevista == d.uf),
            sum(1 for d in docs if d.uf_prevista is not None),
        ),
        "lado_correto": _taxa(
            sum(1 for d in docs if d.lado_previsto == d.lado),
            sum(1 for d in docs if d.lado_previsto is not None),
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
        "medicao": _metricas_medicao(campos),
    }


def _metricas_medicao(campos: Sequence[RegistroCampo]) -> dict[str, Any]:
    """Baseline: todo campo LEGIVEL aprovado direto."""
    criticos = [c for c in campos if c.critico and c.aprovado_medicao]
    escapados = sum(1 for c in criticos if c.erro_escapado_medicao)
    return {
        "criticos_aprovados": len(criticos),
        "erros_escapados": escapados,
        "taxa_erro_escapado": _taxa(escapados, len(criticos)),
        "alucinacoes_criticos": sum(1 for c in criticos if c.alucinacao),
        "alucinacoes": sum(1 for c in campos if c.aprovado_medicao and c.alucinacao),
    }


def _agrupar[T](itens: Iterable[T], chave: Callable[[T], str]) -> dict[str, list[T]]:
    grupos: dict[str, list[T]] = defaultdict(list)
    for item in itens:
        grupos[chave(item)].append(item)
    return dict(sorted(grupos.items()))


def _precisao_revocacao(previstos: Sequence[bool], reais: Sequence[bool]) -> dict[str, Any]:
    vp = sum(1 for p, r in zip(previstos, reais, strict=True) if p and r)
    fp = sum(1 for p, r in zip(previstos, reais, strict=True) if p and not r)
    fn = sum(1 for p, r in zip(previstos, reais, strict=True) if not p and r)
    return {
        "precisao": _taxa(vp, vp + fp),
        "revocacao": _taxa(vp, vp + fn),
        "vp": vp,
        "fp": fp,
        "fn": fn,
    }


_COM_RESPOSTA = ("APROVADO", "REVISAO")


def _captura(docs: Sequence[RegistroDocumento]) -> dict[str, Any]:
    """Precisão e revocação das sinalizações de captura contra a qualidade do gabarito."""
    com_leitura = [d for d in docs if d.captura_vlm is not None]
    resultado: dict[str, Any] = {}
    for sinal, qualidade, rotulo_vlm, heuristica in (
        ("COPIA_REPROGRAFICA", "xerox", "COPIA_REPROGRAFICA", "copia_heuristica"),
        ("PRINT_DE_TELA", "print", "PRINT_DE_TELA", "print_heuristica"),
    ):
        reais = [d.qualidade == qualidade for d in com_leitura]
        resultado[sinal] = {
            "vlm": _precisao_revocacao([d.captura_vlm == rotulo_vlm for d in com_leitura], reais),
            "heuristica": _precisao_revocacao(
                [bool(getattr(d, heuristica)) for d in com_leitura], reais
            ),
            # Só documentos com resposta 200 (as recusas não levam sinalização).
            "resposta": _precisao_revocacao(
                [sinal in d.sinalizacoes for d in com_leitura if d.desfecho in _COM_RESPOSTA],
                [d.qualidade == qualidade for d in com_leitura if d.desfecho in _COM_RESPOSTA],
            ),
        }
    return resultado


def _media(valores: Sequence[float]) -> float | None:
    return sum(valores) / len(valores) if valores else None


def _tokens(docs: Sequence[RegistroDocumento]) -> dict[str, Any]:
    """Tokens medidos por etapa, comparados à estimativa do plano."""
    por_etapa: dict[str, list[RegistroChamada]] = defaultdict(list)
    for d in docs:
        for c in d.chamadas:
            por_etapa[c.etapa].append(c)
    return {
        etapa: {
            "chamadas": len(cs),
            "modelos": sorted({c.modelo for c in cs}),
            "entrada_media": _media([c.entrada for c in cs]),
            "entrada_p95": percentil([c.entrada for c in cs], 95),
            "saida_media": _media([c.saida for c in cs]),
            "raciocinio_medio": _media([c.raciocinio for c in cs]),
            "imagem_estimada_media": _media([c.imagem_estimada for c in cs]),
            "custo_medio_brl": _media([c.custo_brl for c in cs]),
            "latencia_p50_ms": percentil([c.latencia_ms for c in cs], 50),
            "latencia_p95_ms": percentil([c.latencia_ms for c in cs], 95),
            "plano": {
                "entrada": PLANO_TOKENS_ENTRADA,
                "imagem": PLANO_TOKENS_IMAGEM,
                "saida": PLANO_TOKENS_SAIDA,
            },
        }
        for etapa, cs in sorted(por_etapa.items())
    }


def _piores_casos(
    docs: Sequence[RegistroDocumento], campos: Sequence[RegistroCampo], limite: int = 15
) -> list[dict[str, Any]]:
    """Documentos com mais problemas no modo medição (alucinação pesa mais que erro)."""
    por_doc: dict[str, list[RegistroCampo]] = defaultdict(list)
    for c in campos:
        por_doc[c.id].append(c)
    casos: list[dict[str, Any]] = []
    for d in docs:
        cs = por_doc.get(d.id, [])
        alucinacoes = [c.campo for c in cs if c.alucinacao]
        errados = [c.campo for c in cs if c.correto is False]
        criticos_errados = [c.campo for c in cs if c.correto is False and c.critico]
        classificacao = d.subtipo_previsto not in (None, d.subtipo)
        pontos = 3 * len(alucinacoes) + 2 * len(criticos_errados) + len(errados) + 2 * classificacao
        if pontos:
            casos.append(
                {
                    "id": d.id,
                    "subtipo": d.subtipo,
                    "uf": d.uf,
                    "qualidade": d.qualidade,
                    "desfecho": d.desfecho,
                    "alucinacoes": alucinacoes,
                    "campos_errados": errados,
                    "subtipo_previsto": d.subtipo_previsto,
                    "pontos": pontos,
                }
            )
    return sorted(casos, key=lambda c: (-int(c["pontos"]), str(c["id"])))[:limite]


def calcular(
    registros: Registros, referencia_plano: tuple[str, str] | None = None
) -> dict[str, Any]:
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
            "id": c.id,
            "campo": c.campo,
            "tipo": "alucinacao" if c.alucinacao else "valor_errado",
        }
        for c in todos_campos
        if c.erro_escapado
    ]
    alucinacoes = [
        {
            "id": c.id,
            "campo": c.campo,
            "critico": c.critico,
            "aprovado": c.doc_aprovado,
            "aprovado_medicao": c.aprovado_medicao,
            "campo_apagado": c.campo_apagado,
        }
        for c in todos_campos
        if c.alucinacao
    ]
    campo_apagado["medicao_alucinacoes"] = sum(
        1 for c in apagados if c.alucinacao and c.aprovado_medicao
    )
    custo_plano = PLANO_CUSTO_BRL.get(referencia_plano) if referencia_plano else None
    return {
        "geral": geral,
        "metas": metas,
        "por": por,
        "campo_apagado": campo_apagado,
        "captura": _captura(docs),
        "tokens": _tokens(docs),
        "custo_plano": {
            "configuracao": list(referencia_plano) if referencia_plano else None,
            "plano_brl": custo_plano,
            "medido_medio_brl": geral["custo_brl"]["media"],
            "medido_p95_brl": geral["custo_brl"]["p95"],
            "observacao": "o plano inclui escalada em 30% dos documentos; a Fase 1 não relê",
        },
        "piores_casos": _piores_casos(docs, campos),
        "erros_escapados": erros_escapados,
        "alucinacoes": alucinacoes,
    }


def _meta(valor: float | None, criterio: Callable[[float], bool]) -> dict[str, Any]:
    return {"valor": valor, "atingida": None if valor is None else criterio(valor)}
