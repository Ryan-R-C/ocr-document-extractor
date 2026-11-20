"""Intervalos de confiança aplicados às metas do portão e a comparação com o modo medição.

- `calcular`: erro escapado e alucinação em críticos aprovados, na esteira completa e no modo
  medição, com IC exato de Clopper-Pearson e o status da meta (ver `estatistica.avaliar_meta`).
- `comparacao_medicao`: por campo, subtipo, UF e qualidade, os críticos aprovados, o erro
  escapado e a alucinação na esteira × no modo medição, com o IC da esteira.

Mesmas populações de `metricas.calcular`: o erro escapado conta só os documentos originais; a
alucinação em críticos aprovados inclui as variantes do campo apagado (numerador e
denominador). Só contagens e taxas: nada de valor de campo.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from avaliacao.estatistica import avaliar_meta, n_minimo_para_meta

if TYPE_CHECKING:
    from avaliacao.metricas import RegistroCampo, Registros

DIMENSOES = ("campo", "subtipo", "uf", "qualidade")


def _escapado(campos: Sequence[RegistroCampo], meta: float) -> dict[str, Any]:
    aprovados = [c for c in campos if c.critico and c.doc_aprovado]
    return avaliar_meta(
        sum(1 for c in aprovados if c.erro_escapado), len(aprovados), meta
    ).como_dict()


def _escapado_medicao(campos: Sequence[RegistroCampo], meta: float) -> dict[str, Any]:
    aprovados = [c for c in campos if c.critico and c.aprovado_medicao]
    return avaliar_meta(
        sum(1 for c in aprovados if c.erro_escapado_medicao), len(aprovados), meta
    ).como_dict()


# A meta de alucinação é zero: qualquer alucinação a viola, e "demonstrada" exige o limite
# superior abaixo da mesma tolerância do erro escapado (a alucinação é um caso dele).
def _alucinacao(campos: Sequence[RegistroCampo], meta: float) -> dict[str, Any]:
    aprovados = [c for c in campos if c.critico and c.doc_aprovado]
    return avaliar_meta(
        sum(1 for c in aprovados if c.alucinacao), len(aprovados), meta, tolerancia_zero=True
    ).como_dict()


def _alucinacao_medicao(campos: Sequence[RegistroCampo], meta: float) -> dict[str, Any]:
    aprovados = [c for c in campos if c.critico and c.aprovado_medicao]
    return avaliar_meta(
        sum(1 for c in aprovados if c.alucinacao_vlm),
        len(aprovados),
        meta,
        tolerancia_zero=True,
    ).como_dict()


def calcular(registros: Registros, meta: float) -> dict[str, Any]:
    originais = [c for c in registros.campos if not c.variante]
    todos = registros.campos
    return {
        "nivel": 0.95,
        "metodo": "Clopper-Pearson exato; status pelo limite superior unilateral de 95%",
        "meta_erro_escapado": meta,
        "n_minimo_sem_erro": n_minimo_para_meta(meta),
        "esteira": {
            "erro_escapado": _escapado(originais, meta),
            "alucinacao_criticos_aprovados": _alucinacao(todos, meta),
        },
        "medicao": {
            "erro_escapado": _escapado_medicao(originais, meta),
            "alucinacao_criticos_aprovados": _alucinacao_medicao(todos, meta),
        },
    }


def _linha(campos: Sequence[RegistroCampo], meta: float) -> dict[str, Any]:
    esteira = [c for c in campos if c.critico and c.doc_aprovado]
    medicao = [c for c in campos if c.critico and c.aprovado_medicao]
    escapados = sum(1 for c in esteira if c.erro_escapado)
    ic = avaliar_meta(escapados, len(esteira), meta)
    return {
        "esteira": {
            "criticos_aprovados": len(esteira),
            "erros_escapados": escapados,
            "taxa_erro_escapado": ic.taxa,
            "ic95_inferior": ic.ic95[0],
            "ic95_superior": ic.ic95[1],
            "limite_superior_95": ic.limite_superior_95,
            "status": ic.status,
            "alucinacoes_criticos": sum(1 for c in esteira if c.alucinacao),
        },
        "medicao": {
            "criticos_aprovados": len(medicao),
            "erros_escapados": sum(1 for c in medicao if c.erro_escapado_medicao),
            "taxa_erro_escapado": (
                sum(1 for c in medicao if c.erro_escapado_medicao) / len(medicao)
                if medicao
                else None
            ),
            "alucinacoes_criticos": sum(1 for c in medicao if c.alucinacao_vlm),
        },
    }


def comparacao_medicao(registros: Registros, meta: float) -> dict[str, Any]:
    """Esteira × modo medição por dimensão, só com os documentos originais (sem variantes).

    Grupos sem nenhum campo crítico (ex.: campo informativo) ficam de fora.
    """
    originais = [c for c in registros.campos if not c.variante]
    chaves: dict[str, Callable[[RegistroCampo], str]] = {
        "campo": lambda c: c.campo,
        "subtipo": lambda c: c.subtipo,
        "uf": lambda c: c.uf,
        "qualidade": lambda c: c.qualidade,
    }
    resultado: dict[str, Any] = {"geral": _linha(originais, meta)}
    for dimensao in DIMENSOES:
        grupos: dict[str, list[RegistroCampo]] = {}
        for c in originais:
            if c.critico:
                grupos.setdefault(chaves[dimensao](c), []).append(c)
        resultado[dimensao] = {k: _linha(v, meta) for k, v in sorted(grupos.items())}
    return resultado
