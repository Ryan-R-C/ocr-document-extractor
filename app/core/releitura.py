"""Releitura dirigida: campo não ancorado ou com dígito verificador falho é recortado,
ampliado e relido pelo modelo de escalada, sem valor esperado nem leituras anteriores.

STUB da Fase 0: identifica os campos que seriam relidos e registra no rastro, mas não
chama o provider. A releitura real entra na Fase 1. No máximo uma releitura por campo.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ancoragem import ResultadoAncoragem, StatusAncoragem
from app.providers.interfaces import Releitura

IMPLEMENTADO = False


@dataclass(frozen=True)
class ResultadoReleitura:
    campo: str
    executada: bool
    releitura: Releitura | None = None
    # Concordância entre leituras (ex.: "2_DE_3"); None quando não houve releitura.
    concordancia: str | None = None
    motivo_nao_executada: str | None = None


def campos_para_reler(ancoragens: dict[str, ResultadoAncoragem]) -> list[str]:
    return sorted(
        nome
        for nome, anc in ancoragens.items()
        if anc.status in (StatusAncoragem.NAO_ANCORADO, StatusAncoragem.PARCIAL)
    )


async def reler(campos: list[str]) -> dict[str, ResultadoReleitura]:
    return {
        nome: ResultadoReleitura(
            campo=nome, executada=False, motivo_nao_executada="nao_implementada_fase_0"
        )
        for nome in campos
    }
