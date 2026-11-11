"""Ancoragem: o `texto_literal` do VLM precisa aparecer no OCR, perto do rótulo visto.

STUB da Fase 0: todo campo lido fica NAO_ANCORADO, o que impede APROVADO.
A implementação real (tolerância a espaços, pontuação e O/0, I/1, S/5) entra na Fase 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.api.contrato import StatusLeitura
from app.providers.interfaces import CampoLido, LeituraTexto

IMPLEMENTADO = False
VERSAO_ANCORAGEM = "0.0-stub"


class StatusAncoragem(StrEnum):
    ANCORADO = "ANCORADO"
    PARCIAL = "PARCIAL"
    NAO_ANCORADO = "NAO_ANCORADO"
    NAO_APLICAVEL = "NAO_APLICAVEL"  # campo AUSENTE ou ILEGIVEL: nada a ancorar


@dataclass(frozen=True)
class ResultadoAncoragem:
    campo: str
    status: StatusAncoragem
    palavras_ids: list[int] = field(default_factory=list)
    implementado: bool = IMPLEMENTADO


def ancorar(
    campos: dict[str, CampoLido],
    leitura_texto: LeituraTexto,
) -> dict[str, ResultadoAncoragem]:
    resultado: dict[str, ResultadoAncoragem] = {}
    for nome, lido in campos.items():
        if lido.status in (StatusLeitura.AUSENTE, StatusLeitura.ILEGIVEL):
            status = StatusAncoragem.NAO_APLICAVEL
        else:
            status = StatusAncoragem.NAO_ANCORADO
        resultado[nome] = ResultadoAncoragem(campo=nome, status=status)
    return resultado
