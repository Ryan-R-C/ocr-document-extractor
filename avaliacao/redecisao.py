"""Efeito das opções de decisão, recalculado sem novas chamadas a provider.

As decisões em aberto sobre `NAO_VERIFICAVEL` em campo crítico
(`APROVAR_CRITICO_NAO_VERIFICAVEL`) e campo crítico `AUSENTE` (`CRITICO_AUSENTE`) só mudam
o último passo da esteira. Para cada documento que chegou à decisão, este módulo chama de
novo o código real (`app.core.decisao.decidir`) com os intermediários guardados no rastro
(leitura visual, campos depois da releitura, ancoragens, releituras e validação) nas quatro
combinações, e registra o resultado com o mesmo `metricas.registrar` da rodada. Documentos
que pararam antes da decisão (pré-processamento, classificação, provider) não dependem das
opções e entram como saíram.

A combinação configurada na rodada é recalculada também e comparada com o que a esteira
devolveu: qualquer divergência aparece no relatório.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Literal

from app.api.contrato import Documento, RespostaExtracao
from app.config import Settings
from app.core import decisao
from app.core.erros import FalhaExtracao
from app.core.pipeline import ResultadoPipeline
from avaliacao import metricas
from avaliacao.gabarito import ItemGabarito

CriticoAusente = Literal["REVISAO", "422"]


@dataclass(frozen=True)
class Opcao:
    aprovar_nao_verificavel: bool
    critico_ausente: CriticoAusente

    @property
    def chave(self) -> str:
        return (
            f"aprovar_nao_verificavel={str(self.aprovar_nao_verificavel).lower()};"
            f"critico_ausente={self.critico_ausente}"
        )

    @classmethod
    def de(cls, settings: Settings) -> Opcao:
        return cls(settings.aprovar_critico_nao_verificavel, settings.critico_ausente)


_AUSENTE: tuple[CriticoAusente, ...] = ("REVISAO", "422")
OPCOES: tuple[Opcao, ...] = tuple(Opcao(a, c) for a, c in product((False, True), _AUSENTE))


@dataclass
class Recalculo:
    """Registros do avaliador por combinação de opções (mesmo formato da rodada)."""

    rodada: str
    por_opcao: dict[str, metricas.Registros] = field(
        default_factory=lambda: {o.chave: metricas.Registros() for o in OPCOES}
    )
    # Ids (sha256 curto) cujo recálculo na opção da rodada não reproduziu a resposta.
    divergencias: list[str] = field(default_factory=list)


def chegou_a_decisao(resultado: ResultadoPipeline) -> bool:
    r = resultado.rastro
    return r.leitura_visual is not None and r.validacao is not None


def redecidir(resultado: ResultadoPipeline, opcao: Opcao, fonte_captura: str) -> ResultadoPipeline:
    """O resultado que a esteira teria devolvido com `opcao`, a partir do rastro."""
    if not chegou_a_decisao(resultado):
        return resultado
    r = resultado.rastro
    leitura, validacao = r.leitura_visual, r.validacao
    assert leitura is not None
    assert validacao is not None
    captura = r.captura
    try:
        d = decisao.decidir(
            leitura,
            r.preparados,
            r.ancoragens,
            r.releituras,
            validacao,
            aprovar_nao_verificavel=opcao.aprovar_nao_verificavel,
            critico_ausente_422=opcao.critico_ausente == "422",
            sinalizacoes_captura=decisao.sinalizacoes_de_captura(
                leitura,
                captura.copia_reprografica if captura else False,
                captura.print_de_tela if captura else False,
                fonte_captura,
            ),
        )
    except FalhaExtracao as falha:
        return ResultadoPipeline(rastro=r, falha=falha)
    resposta = RespostaExtracao(
        id_processamento=r.id_processamento,
        status=d.status,
        documento=Documento(subtipo=leitura.subtipo, uf=leitura.uf, lado=leitura.lado),
        campos=d.campos,
        sinalizacoes=d.sinalizacoes,
        versao_pipeline=r.versao_pipeline,
    )
    return ResultadoPipeline(rastro=r, resposta=resposta)


def _mesmo_resultado(a: ResultadoPipeline, b: ResultadoPipeline) -> bool:
    if a.resposta is not None or b.resposta is not None:
        return a.resposta == b.resposta
    assert a.falha is not None
    assert b.falha is not None
    return type(a.falha) is type(b.falha) and a.falha.motivos == b.falha.motivos


def registrar(
    registros: metricas.Registros,
    item: ItemGabarito,
    resultado: ResultadoPipeline,
    settings: Settings,
) -> None:
    rodada = Opcao.de(settings)
    if registros.opcoes_decisao is None:
        registros.opcoes_decisao = Recalculo(rodada=rodada.chave)
    recalculo = registros.opcoes_decisao
    for opcao in OPCOES:
        novo = redecidir(resultado, opcao, settings.sinalizacao_captura_fonte)
        if opcao == rodada and not _mesmo_resultado(novo, resultado):
            recalculo.divergencias.append(item.sha256[:12])
        metricas.registrar(recalculo.por_opcao[opcao.chave], item, novo)


def _taxa(parte: int, todo: int) -> float | None:
    return parte / todo if todo else None


def _resumo(
    docs: Sequence[metricas.RegistroDocumento], campos: Sequence[metricas.RegistroCampo]
) -> dict[str, Any]:
    n = len(docs)
    desfechos = Counter(d.desfecho for d in docs)
    # Documentos recusados com o motivo (um documento conta uma vez por motivo).
    por_motivo = Counter(m for d in docs if d.desfecho == "RECUSADO" for m in set(d.motivos))
    criticos_aprovados = [c for c in campos if c.critico and c.doc_aprovado]
    escapados = sum(1 for c in criticos_aprovados if c.erro_escapado)
    return {
        "documentos": n,
        "desfechos": dict(sorted(desfechos.items())),
        "taxa_aprovacao": _taxa(desfechos["APROVADO"], n),
        "taxa_revisao": _taxa(desfechos["REVISAO"], n),
        "taxa_422": _taxa(desfechos["RECUSADO"], n),
        "422_por_motivo": dict(sorted(por_motivo.items())),
        "taxa_422_por_motivo": {m: _taxa(c, n) for m, c in sorted(por_motivo.items())},
        "criticos_aprovados": len(criticos_aprovados),
        "erros_escapados": escapados,
        "taxa_erro_escapado": _taxa(escapados, len(criticos_aprovados)),
        "alucinacoes_criticos_aprovados": sum(1 for c in criticos_aprovados if c.alucinacao),
    }


def calcular(recalculo: Recalculo | None) -> dict[str, Any] | None:
    """Por combinação: desfechos, 422 por motivo, críticos aprovados, erro escapado e
    alucinação (as definições de `metricas.RegistroCampo`), geral e por subtipo."""
    if recalculo is None:
        return None
    opcoes: dict[str, Any] = {}
    for opcao in OPCOES:
        registros = recalculo.por_opcao[opcao.chave]
        docs = [d for d in registros.documentos if not d.variante]
        campos = [c for c in registros.campos if not c.variante]
        subtipos = sorted({d.subtipo for d in docs})
        opcoes[opcao.chave] = {
            "aprovar_nao_verificavel": opcao.aprovar_nao_verificavel,
            "critico_ausente": opcao.critico_ausente,
            "da_rodada": opcao.chave == recalculo.rodada,
            **_resumo(docs, campos),
            "alucinacoes_criticos_aprovados_incluindo_campo_apagado": sum(
                1 for c in registros.campos if c.alucinacao and c.critico and c.doc_aprovado
            ),
            "por_subtipo": {
                s: _resumo(
                    [d for d in docs if d.subtipo == s], [c for c in campos if c.subtipo == s]
                )
                for s in subtipos
            },
        }
    return {
        "rodada": recalculo.rodada,
        "divergencias_com_a_rodada": list(recalculo.divergencias),
        "opcoes": opcoes,
    }
