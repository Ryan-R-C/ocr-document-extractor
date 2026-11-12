"""Dígitos verificadores: só dizem se confere, nunca corrigem (invariante 4).

Fonte: validate-docbr 2.0.1 (CPF, CNPJ numérico e alfanumérico, CNH, PIS, título, CNS e
certidão). O RG de SP e a checagem de tamanho exato são código do projeto.

Atenção: o `CPF.validate` do validate-docbr completa com zeros à esquerda um número com
menos de 11 dígitos. Isso seria corrigir o valor, então o tamanho exato é checado aqui
antes de chamar a biblioteca, para todo documento.
"""

from __future__ import annotations

import re

from validate_docbr import CNH, CNPJ, CNS, CPF, PIS, Certidao, TituloEleitoral

from app.api.contrato import UF
from app.core.digitos import ResultadoDigitos
from app.schemas.campos import DefinicaoCampo, Verificador

_CPF = CPF()  # repeated_digits=False: rejeita 11 dígitos iguais
_CNPJ = CNPJ()
_CNH = CNH()
_PIS = PIS()
_TITULO = TituloEleitoral()
_CNS = CNS()
_CERTIDAO = Certidao()

# Tamanho e alfabeto exatos de cada documento, antes do cálculo do dígito.
_FORMATOS: dict[Verificador, re.Pattern[str]] = {
    Verificador.CPF: re.compile(r"\d{11}"),
    Verificador.CNPJ: re.compile(r"[0-9A-Z]{12}\d{2}"),
    Verificador.CNH: re.compile(r"\d{11}"),
    Verificador.PIS: re.compile(r"\d{11}"),
    Verificador.TITULO_ELEITOR: re.compile(r"\d{12}"),
    Verificador.CNS: re.compile(r"\d{15}"),
    Verificador.RG_SP: re.compile(r"\d{8}[\dX]"),
}


def cpf_valido(valor: str) -> bool:
    return bool(_FORMATOS[Verificador.CPF].fullmatch(valor)) and _CPF.validate(valor)


def cnpj_valido(valor: str) -> bool:
    if not _FORMATOS[Verificador.CNPJ].fullmatch(valor):
        return False
    if len(set(valor)) == 1:
        return False
    return _CNPJ.validate(valor)


def dv_rg_sp(base: str) -> str:
    """RG de SP: 8 dígitos com pesos 2 a 9, módulo 11; resto 10 vira X e 11 vira 0."""
    soma = sum(int(d) * peso for d, peso in zip(base, range(2, 10), strict=True))
    dv = 11 - soma % 11
    return "X" if dv == 10 else "0" if dv == 11 else str(dv)


def rg_sp_valido(valor: str) -> bool:
    if not _FORMATOS[Verificador.RG_SP].fullmatch(valor):
        return False
    if len(set(valor[:8])) == 1:
        return False
    return dv_rg_sp(valor[:8]) == valor[8]


def certidao_valida(matricula: str) -> bool:
    return bool(re.fullmatch(r"\d{32}", matricula)) and _CERTIDAO.validate(matricula)


def verificar(verificador: Verificador, valor: str) -> bool:
    """True quando o formato é exato e o dígito verificador confere."""
    if verificador is Verificador.CPF:
        return cpf_valido(valor)
    if verificador is Verificador.CNPJ:
        return cnpj_valido(valor)
    if verificador is Verificador.RG_SP:
        return rg_sp_valido(valor)
    if not _FORMATOS[verificador].fullmatch(valor):
        return False
    validador = {
        Verificador.CNH: _CNH,
        Verificador.PIS: _PIS,
        Verificador.TITULO_ELEITOR: _TITULO,
        Verificador.CNS: _CNS,
    }[verificador]
    return validador.validate(valor)


def checar_digitos(
    definicao: DefinicaoCampo, valor_normalizado: str, uf: UF | None
) -> ResultadoDigitos:
    """Implementa `app.core.digitos.ChecadorDigitos` para a releitura dirigida."""
    verificador = definicao.verificador
    if verificador is None:
        return ResultadoDigitos.NAO_APLICAVEL
    if verificador is Verificador.RG_SP and (
        uf is not UF.SP or not _FORMATOS[verificador].fullmatch(valor_normalizado)
    ):
        # Fora de SP não há DV; em SP, número fora do formato 8 + DV é modelo antigo.
        return ResultadoDigitos.NAO_APLICAVEL
    if verificar(verificador, valor_normalizado):
        return ResultadoDigitos.VALIDO
    return ResultadoDigitos.INVALIDO
