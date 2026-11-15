"""Normalizadores determinísticos por tipo de valor.

Regras gerais:
- Nunca corrigem caracteres (O→0, I→1 etc.): isso é tolerância da ancoragem, não do valor.
- Qualquer `?` (caractere duvidoso) ou formato fora do esperado devolve None.
- Validade semântica (dígito verificador, coerência de datas) é do motor de regras.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import date
from enum import StrEnum

from app.api.contrato import UF


class TipoValor(StrEnum):
    TEXTO = "TEXTO"
    CPF = "CPF"
    CNPJ = "CNPJ"
    DATA = "DATA"
    DIGITOS = "DIGITOS"
    ALFANUMERICO = "ALFANUMERICO"
    UF = "UF"
    CATEGORIA_CNH = "CATEGORIA_CNH"
    # Data, ou validade indeterminada impressa por extenso (CIN de quem tem 60 anos ou mais).
    VALIDADE = "VALIDADE"


_SEPARADORES_DOC = re.compile(r"[\s.\-/]")
_DATA = re.compile(r"^(\d{2})[/.\-](\d{2})[/.\-](\d{4})$")
_UFS = {u.value for u in UF if u is not UF.NAO_IDENTIFICADO}


def _texto(valor: str) -> str | None:
    limpo = " ".join(unicodedata.normalize("NFC", valor).upper().split())
    return limpo or None


def _sem_separadores(valor: str) -> str:
    return _SEPARADORES_DOC.sub("", valor.strip().upper())


def _cpf(valor: str) -> str | None:
    bruto = _sem_separadores(valor)
    return bruto if re.fullmatch(r"\d{11}", bruto) else None


def _cnpj(valor: str) -> str | None:
    # Aceita o CNPJ alfanumérico (12 posições alfanuméricas + 2 dígitos verificadores).
    bruto = _sem_separadores(valor)
    return bruto if re.fullmatch(r"[0-9A-Z]{12}\d{2}", bruto) else None


def _digitos(valor: str) -> str | None:
    bruto = _sem_separadores(valor)
    return bruto if re.fullmatch(r"\d+", bruto) else None


def _alfanumerico(valor: str) -> str | None:
    bruto = _sem_separadores(valor)
    return bruto if re.fullmatch(r"[0-9A-Z]+", bruto) else None


def _data(valor: str) -> str | None:
    casamento = _DATA.match(valor.strip())
    if not casamento:
        return None
    dia, mes, ano = (int(g) for g in casamento.groups())
    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return None


INDETERMINADA = "INDETERMINADA"
_INDETERMINADA = re.compile(r"^INDETERMINAD[AO]$")


def _validade(valor: str) -> str | None:
    if _INDETERMINADA.match(valor.strip().upper()):
        return INDETERMINADA
    return _data(valor)


def _uf(valor: str) -> str | None:
    bruto = valor.strip().upper()
    return bruto if bruto in _UFS else None


def _categoria_cnh(valor: str) -> str | None:
    bruto = re.sub(r"\s", "", valor.upper())
    return bruto if re.fullmatch(r"[A-E]{1,2}|ACC", bruto) else None


NORMALIZADORES: dict[TipoValor, Callable[[str], str | None]] = {
    TipoValor.TEXTO: _texto,
    TipoValor.CPF: _cpf,
    TipoValor.CNPJ: _cnpj,
    TipoValor.DATA: _data,
    TipoValor.DIGITOS: _digitos,
    TipoValor.ALFANUMERICO: _alfanumerico,
    TipoValor.UF: _uf,
    TipoValor.CATEGORIA_CNH: _categoria_cnh,
    TipoValor.VALIDADE: _validade,
}


def normalizar(tipo: TipoValor, valor: str | None) -> str | None:
    if valor is None or "?" in valor or not valor.strip():
        return None
    return NORMALIZADORES[tipo](valor)
