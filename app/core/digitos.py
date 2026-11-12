"""Interface com a Fase 3: checagem de dígitos verificadores usada pela releitura.

A Fase 3 entrega `app/regras/digitos.py` com uma função neste formato. Ela só diz se o
dígito verificador confere e nunca corrige o valor (invariante 4). Até a integração no
portão 2+3, a esteira usa `checar_pendente`, que não decide nada (NAO_APLICAVEL): a
releitura dispara só por ancoragem.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from app.schemas.normalizacao import TipoValor

IMPLEMENTADO = False


class ResultadoDigitos(StrEnum):
    VALIDO = "VALIDO"
    INVALIDO = "INVALIDO"
    NAO_APLICAVEL = "NAO_APLICAVEL"  # tipo sem dígito verificador, ou checagem pendente


class ChecadorDigitos(Protocol):
    def __call__(self, tipo_valor: TipoValor, valor_normalizado: str) -> ResultadoDigitos: ...


def checar_pendente(tipo_valor: TipoValor, valor_normalizado: str) -> ResultadoDigitos:
    return ResultadoDigitos.NAO_APLICAVEL
