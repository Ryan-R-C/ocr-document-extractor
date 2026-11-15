"""Regras da CIN: validade pela idade na expedição e MRZ (bônus)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

from app.api.contrato import MotivoCampo, Sinalizacao, Subtipo
from app.regras.base import (
    Avaliacao,
    Contexto,
    Regra,
    idade,
    invalido,
    nao_verificavel,
    sinalizar,
    somar_anos,
    suspeito,
    valido,
)
from app.regras.tabelas import chave_nome
from app.schemas.normalizacao import INDETERMINADA

CIN = frozenset({Subtipo.CIN})
FONTE_CIN = (
    "https://arpensp.org.br/noticia/artigo-decreto-1097722-e-a-validade-da-carteira-de-"
    "identidade-%E2%80%93-por-fernanda-maria-alves-gomes [confirmar]"
)
FONTE_MRZ = "ICAO Doc 9303, parte 5 (TD1) [confirmar]"


def prazo_cin(idade_na_expedicao: int) -> int | None:
    """Anos de validade; None = indeterminada (60 anos ou mais)."""
    if idade_na_expedicao <= 11:
        return 5
    if idade_na_expedicao <= 59:
        return 10
    return None


def _validade(ctx: Contexto) -> Iterator[Avaliacao]:
    validade = ctx.norm("validade")
    if validade is None:
        return
    expedicao = ctx.data("data_expedicao")
    nascimento = ctx.data("data_nascimento")
    if expedicao is None or nascimento is None or expedicao < nascimento:
        yield nao_verificavel("validade", "sem_expedicao_ou_nascimento")
        return
    prazo = prazo_cin(idade(nascimento, expedicao))
    if validade == INDETERMINADA:
        if prazo is None:
            yield valido("validade", "indeterminada")
        else:
            yield invalido("validade", MotivoCampo.DATA_INCOERENTE, "indeterminada_antes_dos_60")
        return
    dia = ctx.data("validade")
    assert dia is not None
    if dia <= expedicao:
        yield invalido("validade", MotivoCampo.DATA_INCOERENTE, "vence_antes_da_expedicao")
        return
    if prazo is None:
        yield suspeito("validade", MotivoCampo.DATA_INCOERENTE, "deveria_ser_indeterminada")
        return
    esperado = somar_anos(expedicao, prazo)
    tolerancia = timedelta(days=ctx.parametros.cin_validade_tolerancia_dias)
    if abs(dia - esperado) > tolerancia:
        yield suspeito("validade", MotivoCampo.DATA_INCOERENTE, f"prazo_esperado_{prazo}_anos")
    else:
        yield valido("validade", f"prazo_{prazo}_anos")


VALIDADE = Regra(
    id="cin.validade",
    versao="1",
    descricao="Validade pela idade na expedição: 5 anos até 11, 10 de 12 a 59, indeterminada 60+",
    fonte=FONTE_CIN,
    subtipos=CIN,
    campos=("validade",),
    funcao=_validade,
)


def _vencimento(ctx: Contexto) -> Iterator[Avaliacao]:
    dia = ctx.data("validade")
    if dia is not None and dia < ctx.data_processamento:
        yield sinalizar(Sinalizacao.DOCUMENTO_VENCIDO, "cin_vencida")


VENCIMENTO = Regra(
    id="cin.vencimento",
    versao="1",
    descricao="Sinaliza CIN com validade anterior à data do processamento",
    fonte=FONTE_CIN,
    subtipos=CIN,
    campos=(),
    funcao=_vencimento,
    tags=("sinalizacao",),
)

# --- MRZ (TD1: 3 linhas de 30 caracteres) --------------------------------------------------------


def digito_mrz(texto: str) -> str:
    """Dígito de controle ICAO: pesos 7, 3, 1; letras valem 10–35 e `<` vale 0."""
    soma = 0
    for i, c in enumerate(texto):
        if c.isdigit():
            v = int(c)
        elif "A" <= c <= "Z":
            v = ord(c) - 55
        else:
            v = 0
        soma += v * (7, 3, 1)[i % 3]
    return str(soma % 10)


@dataclass(frozen=True)
class Mrz:
    numero: str
    nascimento: str  # AAMMDD
    validade: str  # AAMMDD ou <<<<<<
    nomes: list[str]
    checagens_ok: bool


def ler_mrz(texto: str) -> Mrz | None:
    """None quando não tem o formato TD1 (3 × 30). Nunca corrige caracteres."""
    bruto = re.sub(r"\s", "", texto.upper())
    if len(bruto) != 90 or not re.fullmatch(r"[A-Z0-9<]+", bruto):
        return None
    l1, l2, l3 = bruto[:30], bruto[30:60], bruto[60:]
    if not l1.startswith("I"):
        return None
    numero, dv_numero = l1[5:14], l1[14]
    if dv_numero == "<":
        # Número com mais de 9 caracteres: continua nos dados opcionais e termina no DV.
        resto = l1[15:].split("<", 1)[0]
        if len(resto) < 1:
            return None
        numero, dv_numero = numero + resto[:-1], resto[-1]
    checagens = [
        digito_mrz(numero) == dv_numero,
        digito_mrz(l2[0:6]) == l2[6],
        l2[8:14] == "<<<<<<" or digito_mrz(l2[8:14]) == l2[14],
        digito_mrz(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]) == l2[29],
    ]
    nomes = [n for n in re.split(r"<+", l3) if n]
    return Mrz(numero.replace("<", ""), l2[0:6], l2[8:14], nomes, all(checagens))


def _aammdd(iso: str) -> str:
    return iso[2:4] + iso[5:7] + iso[8:10]


def _mrz(ctx: Contexto) -> Iterator[Avaliacao]:
    valor = ctx.norm("mrz")
    if valor is None:
        return
    mrz = ler_mrz(valor)
    if mrz is None:
        yield nao_verificavel("mrz", "fora_do_formato_td1", MotivoCampo.FORMATO_INVALIDO)
        return
    if not mrz.checagens_ok:
        yield suspeito("mrz", MotivoCampo.DIGITO_VERIFICADOR_INVALIDO, "checksum")
        return
    conferidos: list[bool] = []
    if (nascimento := ctx.norm("data_nascimento")) is not None:
        conferidos.append(_aammdd(nascimento) == mrz.nascimento)
    validade = ctx.norm("validade")
    if validade is not None and validade != INDETERMINADA and mrz.validade != "<<<<<<":
        conferidos.append(_aammdd(validade) == mrz.validade)
    cpf = ctx.norm("cpf")
    if cpf is not None and re.fullmatch(r"\d{11}", mrz.numero):
        conferidos.append(mrz.numero == cpf)
    if (nome := ctx.norm("nome")) is not None and mrz.nomes:
        palavras = [chave_nome(p) for p in nome.split()]
        # O último nome da MRZ pode vir truncado: basta ser prefixo de uma palavra.
        conferidos.append(
            all(n in palavras for n in mrz.nomes[:-1])
            and any(p.startswith(mrz.nomes[-1]) for p in palavras)
        )
    if not conferidos:
        yield nao_verificavel("mrz", "nada_para_conferir")
    elif all(conferidos):
        yield valido("mrz")
        yield sinalizar(Sinalizacao.MRZ_CONFERIDA)
    else:
        yield suspeito("mrz", MotivoCampo.VALOR_INCOERENTE, "diverge_do_impresso")


MRZ = Regra(
    id="cin.mrz",
    versao="1",
    descricao="MRZ legível: checksums e conferência com os campos impressos (bônus)",
    fonte=FONTE_MRZ,
    subtipos=CIN,
    campos=("mrz",),
    funcao=_mrz,
)

REGRAS = [VALIDADE, VENCIMENTO, MRZ]
