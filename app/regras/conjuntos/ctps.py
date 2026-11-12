"""Regras da CTPS física e digital (número, série, contratos e CBO).

O CNPJ dos contratos tem o dígito verificador em `comuns.DIGITOS`. Na CTPS digital, número
e série são derivados do CPF pelo próprio código (`schemas.campos.CAMPOS_DERIVADOS`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from app.api.contrato import MotivoCampo, Subtipo
from app.regras.base import (
    Avaliacao,
    Contexto,
    Regra,
    idade,
    invalido,
    nao_verificavel,
    suspeito,
    valido,
)

FISICA = frozenset({Subtipo.CTPS_FISICA})
DIGITAL = frozenset({Subtipo.CTPS_DIGITAL})
CTPS = FISICA | DIGITAL
FONTE_PLANO = "docs/plano.md#motor-de-regras"
FONTE_CNPJ_ALFA = (
    "https://www.barbieriadvogados.com/cnpj-alfanumerico-2026-mudancas-regulamentares-e-"
    "impactos-empresariais/ [confirmar]"
)
FONTE_MOEDAS = "Banco Central do Brasil, histórico das moedas brasileiras [confirmar]"
CNPJ_ALFANUMERICO_DESDE = date(2026, 7, 31)


def _numero_serie(ctx: Contexto) -> Iterator[Avaliacao]:
    numero = ctx.norm("numero_ctps")
    if numero is not None:
        if re.fullmatch(r"\d{7}", numero):
            yield valido("numero_ctps")
        elif re.fullmatch(r"\d{5,6}", numero):
            yield valido("numero_ctps", "modelo_antigo")
        else:
            yield suspeito("numero_ctps", MotivoCampo.FORMATO_INVALIDO)
    serie = ctx.norm("serie_ctps")
    if serie is not None:
        if re.fullmatch(r"\d{4}", serie):
            yield valido("serie_ctps")
        elif re.fullmatch(r"\d{3,5}", serie):
            yield valido("serie_ctps", "modelo_antigo")
        else:
            yield suspeito("serie_ctps", MotivoCampo.FORMATO_INVALIDO)


NUMERO_SERIE = Regra(
    id="ctps_fisica.numero_serie",
    versao="1",
    descricao="Número (7 dígitos; 5–6 em modelos antigos) e série (4 dígitos; 3–5 antigos)",
    fonte=f"{FONTE_PLANO} [confirmar tolerância dos modelos antigos]",
    subtipos=FISICA,
    campos=("numero_ctps", "serie_ctps"),
    funcao=_numero_serie,
)


def _cnpj_alfanumerico(ctx: Contexto) -> Iterator[Avaliacao]:
    cnpj = ctx.norm("contrato_cnpj")
    admissao = ctx.data("contrato_admissao")
    if cnpj is None or admissao is None or not re.search(r"[A-Z]", cnpj):
        return
    if admissao < CNPJ_ALFANUMERICO_DESDE:
        yield suspeito("contrato_cnpj", MotivoCampo.VALOR_INCOERENTE, "alfanumerico_antes_de_2026")
    else:
        yield valido("contrato_cnpj", "alfanumerico")


CNPJ_ALFANUMERICO = Regra(
    id="ctps.cnpj_alfanumerico",
    versao="1",
    descricao="CNPJ alfanumérico só em contrato com admissão a partir de 31/07/2026",
    fonte=FONTE_CNPJ_ALFA,
    subtipos=CTPS,
    campos=("contrato_cnpj",),
    funcao=_cnpj_alfanumerico,
)


def _datas_contrato(ctx: Contexto) -> Iterator[Avaliacao]:
    admissao = ctx.data("contrato_admissao")
    saida = ctx.data("contrato_saida")
    nascimento = ctx.data("data_nascimento")
    if admissao is not None and saida is not None:
        if saida < admissao:
            yield suspeito("contrato_saida", MotivoCampo.DATA_INCOERENTE, "saida_antes_da_admissao")
        else:
            yield valido("contrato_saida")
    if admissao is not None and nascimento is not None and admissao >= nascimento:
        if idade(nascimento, admissao) < ctx.parametros.idade_min_admissao:
            yield suspeito("contrato_admissao", MotivoCampo.DATA_INCOERENTE, "idade_na_admissao")
        else:
            yield valido("contrato_admissao")


DATAS_CONTRATO = Regra(
    id="ctps.datas_contrato",
    versao="1",
    descricao="Admissão antes da saída; idade mínima plausível na admissão",
    fonte=FONTE_PLANO,
    subtipos=CTPS,
    campos=("contrato_admissao", "contrato_saida"),
    funcao=_datas_contrato,
)


@dataclass(frozen=True)
class Moeda:
    simbolo: str
    inicio: date
    fim: date | None


MOEDAS = (
    Moeda("Cr$", date(1942, 11, 1), date(1967, 2, 12)),
    Moeda("NCr$", date(1967, 2, 13), date(1970, 5, 14)),
    Moeda("Cr$", date(1970, 5, 15), date(1986, 2, 27)),
    Moeda("Cz$", date(1986, 2, 28), date(1989, 1, 15)),
    Moeda("NCz$", date(1989, 1, 16), date(1990, 3, 15)),
    Moeda("Cr$", date(1990, 3, 16), date(1993, 7, 31)),
    Moeda("CR$", date(1993, 8, 1), date(1994, 6, 30)),
    Moeda("R$", date(1994, 7, 1), None),
)
_SIMBOLO = re.compile(r"(?<![A-Za-z])(NCz|NCr|Cz|Cr|R)\$", re.IGNORECASE)


def simbolos_possiveis(texto: str) -> set[str] | None:
    """Símbolos que o texto pode representar. `CR$` em maiúsculas pode ser o cruzeiro
    (Cr$) transcrito em caixa alta ou o cruzeiro real (CR$)."""
    casamento = _SIMBOLO.search(texto)
    if casamento is None:
        return None
    escrito = casamento.group(0)
    if escrito.upper() != "CR$":
        return {m.simbolo for m in MOEDAS if m.simbolo.upper() == escrito.upper()}
    return {"Cr$"} if escrito == "Cr$" else {"Cr$", "CR$"}


def moeda_coerente(simbolos: set[str], dia: date, transicao_dias: int) -> bool:
    folga = timedelta(days=transicao_dias)
    return any(
        m.simbolo in simbolos and m.inicio <= dia and (m.fim is None or dia <= m.fim + folga)
        for m in MOEDAS
    )


def _moeda(ctx: Contexto) -> Iterator[Avaliacao]:
    salario = ctx.bruto("contrato_salario")
    admissao = ctx.data("contrato_admissao")
    if salario is None:
        return
    simbolos = simbolos_possiveis(salario)
    if simbolos is None:
        return
    if admissao is None:
        yield nao_verificavel("contrato_salario", "sem_admissao")
    elif moeda_coerente(simbolos, admissao, ctx.parametros.moeda_transicao_dias):
        yield valido("contrato_salario")
    else:
        yield suspeito("contrato_salario", MotivoCampo.VALOR_INCOERENTE, "moeda_da_epoca")


MOEDA_SALARIO = Regra(
    id="ctps.moeda_salario",
    versao="1",
    descricao="Moeda do salário coerente com a data de admissão (Cr$, NCr$, Cz$, NCz$, CR$, R$)",
    fonte=FONTE_MOEDAS,
    subtipos=FISICA,
    campos=("contrato_salario",),
    funcao=_moeda,
)


def _cbo(ctx: Contexto) -> Iterator[Avaliacao]:
    codigo = ctx.norm("contrato_cbo")
    if codigo is None:
        return
    if not re.fullmatch(r"\d{6}", codigo):
        yield invalido("contrato_cbo", MotivoCampo.FORMATO_INVALIDO)
    elif ctx.tabelas.cbo is None:
        yield nao_verificavel("contrato_cbo", "tabela_cbo_ausente")
    elif codigo in ctx.tabelas.cbo:
        yield valido("contrato_cbo")
    else:
        yield invalido("contrato_cbo", MotivoCampo.FORA_DA_TABELA)


CBO = Regra(
    id="ctps_digital.cbo",
    versao="1",
    descricao="CBO do contrato na tabela oficial (6 dígitos)",
    fonte="app/regras/tabelas/fontes.toml#cbo",
    subtipos=DIGITAL,
    campos=("contrato_cbo",),
    funcao=_cbo,
)

REGRAS = [NUMERO_SERIE, CNPJ_ALFANUMERICO, DATAS_CONTRATO, MOEDA_SALARIO, CBO]
