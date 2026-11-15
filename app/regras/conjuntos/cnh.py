"""Regras da CNH: categoria, teto de validade por idade e época, vencimento e 1ª habilitação.

Os dígitos verificadores do registro e do CPF estão em `comuns.DIGITOS`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from app.api.contrato import MotivoCampo, Sinalizacao, Subtipo
from app.regras.base import (
    Avaliacao,
    Contexto,
    FuncaoRegra,
    Referencia,
    Regra,
    idade,
    invalido,
    nao_verificavel,
    sinalizar,
    somar_anos,
    suspeito,
    valido,
)

CNH = frozenset({Subtipo.CNH})
CATEGORIAS = frozenset({"A", "B", "C", "D", "E", "AB", "AC", "AD", "AE", "ACC"})
FONTE_CTB = (
    "https://www.portaldotransito.com.br/noticias/nova-lei-de-transito-mitos-e-verdades-"
    "sobre-o-novo-prazo-de-renovacao-da-cnh/ [confirmar]"
)
FONTE_LEI_15428 = (
    "https://atarde.com.br/brasil/cnh-aos-50-anos-entenda-a-mudanca-na-validade-e-regras-de-"
    "2026-1401935; 10 anos até os 69 por decisão do responsável em 2026-11-12 [confirmar]"
)
FONTE_CONTRAN = "https://www.portaldotransito.com.br/?p=58299 [confirmar]"
LEI_14071_VIGENCIA = date(2021, 4, 12)


def _categoria(ctx: Contexto) -> Iterator[Avaliacao]:
    valor = ctx.norm("categoria")
    if valor is None:
        return
    if valor in CATEGORIAS:
        yield valido("categoria")
    else:
        yield invalido("categoria", MotivoCampo.FORA_DA_TABELA)


CATEGORIA = Regra(
    id="cnh.categoria",
    versao="1",
    descricao="Categoria em lista fechada: A, B, C, D, E, AB, AC, AD, AE, ACC",
    fonte="docs/plano.md#motor-de-regras",
    subtipos=CNH,
    campos=("categoria",),
    funcao=_categoria,
)


def teto_desde_2021(anos: int) -> int:
    """Lei 14.071/2020 com a Lei 15.428/2026: 10 anos até 69, 3 anos a partir de 70."""
    return 10 if anos < 70 else 3


def teto_antes_de_2021(anos: int) -> int:
    """Antes de 12/04/2021: 5 anos até 65, 3 anos acima de 65."""
    return 5 if anos <= 65 else 3


def _teto(regra_teto: Callable[[int], int]) -> FuncaoRegra:
    def funcao(ctx: Contexto) -> Iterator[Avaliacao]:
        validade = ctx.data("validade")
        emissao = ctx.data("data_emissao")
        nascimento = ctx.data("data_nascimento")
        if validade is None or emissao is None:
            return
        if nascimento is None or emissao < nascimento:
            yield nao_verificavel("validade", "sem_nascimento")
            return
        if validade <= emissao:
            yield invalido("validade", MotivoCampo.DATA_INCOERENTE, "vence_antes_da_emissao")
            return
        teto = regra_teto(idade(nascimento, emissao))
        limite = somar_anos(emissao, teto) + timedelta(
            days=ctx.parametros.cnh_validade_tolerancia_dias
        )
        if validade > limite:
            yield suspeito("validade", MotivoCampo.DATA_INCOERENTE, f"acima_do_teto_{teto}_anos")
        else:
            yield valido("validade", f"teto_{teto}_anos")

    return funcao


TETO_DESDE_2021 = Regra(
    id="cnh.validade_teto",
    versao="2021",
    descricao="Validade como teto pela idade na emissão: 10 anos até 69, 3 a partir de 70",
    fonte=FONTE_LEI_15428,
    subtipos=CNH,
    campos=("validade",),
    funcao=_teto(teto_desde_2021),
    referencia=Referencia.DOCUMENTO,
    campo_data_referencia="data_emissao",
    vigencia_inicio=LEI_14071_VIGENCIA,
)

TETO_ANTES_DE_2021 = Regra(
    id="cnh.validade_teto",
    versao="1998",
    descricao="Validade como teto pela idade na emissão: 5 anos até 65, 3 acima de 65",
    fonte=FONTE_CTB,
    subtipos=CNH,
    campos=("validade",),
    funcao=_teto(teto_antes_de_2021),
    referencia=Referencia.DOCUMENTO,
    campo_data_referencia="data_emissao",
    vigencia_fim=LEI_14071_VIGENCIA - timedelta(days=1),
)


@dataclass(frozen=True)
class Prorrogacao:
    id: str
    vencidas_de: date
    vencidas_ate: date
    valem_ate: date


PRORROGACOES = (
    Prorrogacao("contran.279.2026", date(2026, 6, 5), date(2026, 12, 8), date(2026, 12, 9)),
)


def _vencimento(ctx: Contexto) -> Iterator[Avaliacao]:
    validade = ctx.data("validade")
    hoje = ctx.data_processamento
    if validade is None or validade >= hoje:
        return
    for p in PRORROGACOES:
        if p.vencidas_de <= validade <= p.vencidas_ate and hoje <= p.valem_ate:
            return  # prorrogada: ainda vale
    yield sinalizar(Sinalizacao.DOCUMENTO_VENCIDO, "cnh_vencida")


VENCIMENTO = Regra(
    id="cnh.vencimento",
    versao="1",
    descricao=(
        "Sinaliza CNH vencida na data do processamento, considerando a prorrogação do "
        "Contran (vencidas de 05/06 a 08/12/2026 valem até 09/12/2026)"
    ),
    fonte=FONTE_CONTRAN,
    subtipos=CNH,
    campos=(),
    funcao=_vencimento,
    tags=("sinalizacao",),
)


def _primeira_habilitacao(ctx: Contexto) -> Iterator[Avaliacao]:
    primeira = ctx.data("primeira_habilitacao")
    if primeira is None:
        return
    nascimento = ctx.data("data_nascimento")
    emissao = ctx.data("data_emissao")
    if nascimento is not None and idade(nascimento, primeira) < 18:
        yield suspeito("primeira_habilitacao", MotivoCampo.DATA_INCOERENTE, "menor_de_18")
    elif emissao is not None and primeira > emissao:
        yield suspeito("primeira_habilitacao", MotivoCampo.DATA_INCOERENTE, "depois_da_emissao")
    elif nascimento is not None or emissao is not None:
        yield valido("primeira_habilitacao")


PRIMEIRA_HABILITACAO = Regra(
    id="cnh.primeira_habilitacao",
    versao="1",
    descricao="1ª habilitação com 18 anos ou mais e não posterior à emissão",
    fonte="docs/plano.md#motor-de-regras",
    subtipos=CNH,
    campos=("primeira_habilitacao",),
    funcao=_primeira_habilitacao,
)

REGRAS = [CATEGORIA, TETO_DESDE_2021, TETO_ANTES_DE_2021, VENCIMENTO, PRIMEIRA_HABILITACAO]
