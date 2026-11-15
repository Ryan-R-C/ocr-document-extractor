"""Regras de todos os documentos: dígitos verificadores e datas."""

from __future__ import annotations

from collections.abc import Iterator

from app.api.contrato import MotivoCampo, Subtipo
from app.regras import digitos
from app.regras.base import (
    Avaliacao,
    Contexto,
    Regra,
    idade,
    invalido,
    suspeito,
    valido,
)
from app.schemas.campos import Verificador
from app.schemas.normalizacao import TipoValor

TODOS = frozenset(s for s in Subtipo if s is not Subtipo.NAO_IDENTIFICADO)
FONTE_PLANO = "docs/plano.md#motor-de-regras"
FONTE_DOCBR = "https://github.com/alvarofpp/validate-docbr (2.0.1)"

# Datas que não podem ser posteriores ao processamento nem anteriores ao nascimento.
DATAS_PASSADAS = (
    "data_expedicao",
    "data_emissao",
    "primeira_habilitacao",
    "contrato_admissao",
    "contrato_saida",
)


def _regra_digitos(verificador: Verificador, descricao: str) -> Regra:
    def funcao(ctx: Contexto) -> Iterator[Avaliacao]:
        for definicao in ctx.definicoes:
            if definicao.verificador is not verificador:
                continue
            valor = ctx.norm(definicao.nome)
            if valor is None:
                continue
            if digitos.verificar(verificador, valor):
                yield valido(definicao.nome)
            else:
                yield invalido(definicao.nome, MotivoCampo.DIGITO_VERIFICADOR_INVALIDO)

    return Regra(
        id=f"digitos.{verificador.value.lower()}",
        versao="1",
        descricao=descricao,
        fonte=FONTE_DOCBR,
        subtipos=TODOS,
        campos=(),
        funcao=funcao,
    )


DIGITOS = [
    _regra_digitos(
        Verificador.CPF, "CPF: dígitos verificadores, 11 dígitos exatos, nunca 11 iguais"
    ),
    _regra_digitos(Verificador.CNPJ, "CNPJ numérico ou alfanumérico: dígitos verificadores"),
    _regra_digitos(Verificador.CNH, "Registro da CNH: dígitos verificadores"),
    _regra_digitos(Verificador.PIS, "NIS/PIS/PASEP: dígito verificador"),
    _regra_digitos(Verificador.TITULO_ELEITOR, "Título de eleitor: dígitos verificadores"),
    _regra_digitos(Verificador.CNS, "CNS: dígito verificador"),
]


def _datas_validas(ctx: Contexto) -> Iterator[Avaliacao]:
    for definicao in ctx.definicoes:
        if definicao.tipo_valor not in (TipoValor.DATA, TipoValor.VALIDADE):
            continue
        dia = ctx.data(definicao.nome)
        if dia is None:
            continue
        if dia.year < ctx.parametros.ano_minimo:
            yield invalido(definicao.nome, MotivoCampo.DATA_INVALIDA, "ano_minimo")
        elif definicao.nome != "validade" and dia > ctx.data_processamento:
            yield invalido(definicao.nome, MotivoCampo.DATA_INVALIDA, "futura")
        else:
            yield valido(definicao.nome)


DATAS_VALIDAS = Regra(
    id="datas.validas",
    versao="1",
    descricao="Datas no calendário, não anteriores ao ano mínimo e, exceto validade, não futuras",
    fonte=FONTE_PLANO,
    subtipos=TODOS,
    campos=(),
    funcao=_datas_validas,
)


def _idade_plausivel(ctx: Contexto) -> Iterator[Avaliacao]:
    nascimento = ctx.data("data_nascimento")
    if nascimento is None or nascimento > ctx.data_processamento:
        return
    anos = idade(nascimento, ctx.data_processamento)
    if anos < ctx.parametros.idade_min:
        yield suspeito("data_nascimento", MotivoCampo.DATA_INCOERENTE, "idade_abaixo_do_minimo")
    elif anos > ctx.parametros.idade_max:
        yield suspeito("data_nascimento", MotivoCampo.DATA_INCOERENTE, "idade_acima_do_maximo")
    else:
        yield valido("data_nascimento")


IDADE_PLAUSIVEL = Regra(
    id="datas.idade_plausivel",
    versao="1",
    descricao="Idade na data do processamento entre os limites configurados",
    fonte=FONTE_PLANO,
    subtipos=TODOS,
    campos=("data_nascimento",),
    funcao=_idade_plausivel,
)


def _depois_do_nascimento(ctx: Contexto) -> Iterator[Avaliacao]:
    nascimento = ctx.data("data_nascimento")
    if nascimento is None:
        return
    for nome in DATAS_PASSADAS:
        dia = ctx.data(nome)
        if dia is None:
            continue
        if dia < nascimento:
            yield invalido(nome, MotivoCampo.DATA_INCOERENTE, "antes_do_nascimento")
        else:
            yield valido(nome)


DEPOIS_DO_NASCIMENTO = Regra(
    id="datas.depois_do_nascimento",
    versao="1",
    descricao="Expedição, emissão, habilitação e contratos não anteriores ao nascimento",
    fonte=FONTE_PLANO,
    subtipos=TODOS,
    campos=DATAS_PASSADAS,
    funcao=_depois_do_nascimento,
)

REGRAS = [*DIGITOS, DATAS_VALIDAS, IDADE_PLAUSIVEL, DEPOIS_DO_NASCIMENTO]
