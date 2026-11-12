"""Interface entre a releitura (Fase 2) e a checagem de dígitos verificadores (Fase 3).

A implementação está em `app/regras/digitos.py`. A checagem só diz se o dígito
verificador confere e nunca corrige o valor (invariante 4).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from app.api.contrato import UF
from app.schemas.campos import DefinicaoCampo


class ResultadoDigitos(StrEnum):
    VALIDO = "VALIDO"
    INVALIDO = "INVALIDO"
    # Campo sem dígito verificador, ou que não dá para checar (RG fora de SP).
    NAO_APLICAVEL = "NAO_APLICAVEL"


class ChecadorDigitos(Protocol):
    def __call__(
        self, definicao: DefinicaoCampo, valor_normalizado: str, uf: UF | None
    ) -> ResultadoDigitos: ...
