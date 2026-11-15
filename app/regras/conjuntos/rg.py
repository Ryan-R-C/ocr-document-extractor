"""Regras do RG (modelo antigo e 2018–2022)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date

from app.api.contrato import UF, MotivoCampo, Sinalizacao, Subtipo
from app.regras import digitos
from app.regras.base import (
    Avaliacao,
    Contexto,
    Referencia,
    Regra,
    idade,
    invalido,
    nao_verificavel,
    sem_acento,
    sinalizar,
    suspeito,
    valido,
)
from app.regras.tabelas import chave_nome

RG = frozenset({Subtipo.RG_ANTIGO, Subtipo.RG_2018_2022})
_UFS = {u.value for u in UF if u is not UF.NAO_IDENTIFICADO}

FONTE_RG_SP = "https://www.axonwireless.com/toolbox/brazil-rg-generator/ [confirmar]"
FONTE_CNJ = "https://www.legjur.com/legislacao/art/out_2026020900001492023-473 [confirmar]"
FONTE_DECRETO_10977 = (
    "https://www2.camara.gov.br/legin/fed/decret/2022/"
    "decreto-10977-23-fevereiro-2022-792318-anexo-pe.pdf [confirmar]"
)


def _numero(ctx: Contexto) -> Iterator[Avaliacao]:
    valor = ctx.norm("numero_rg")
    if valor is None:
        return
    uf = ctx.uf("uf_emissao")
    if uf is UF.SP:
        if not re.fullmatch(r"\d{8}[\dX]", valor):
            yield nao_verificavel("numero_rg", "sp_fora_do_formato_8_mais_dv")
        elif digitos.rg_sp_valido(valor):
            yield valido("numero_rg", "dv_sp")
        else:
            # Persistindo depois da releitura, DV falho de SP é INVALIDO (tabela do plano).
            yield invalido("numero_rg", MotivoCampo.DIGITO_VERIFICADOR_INVALIDO, "dv_sp")
        return
    if re.search(r"[A-Z]", valor[:-1]) or not re.search(r"\d", valor):
        # Letra só cabe como DV no fim (X); fora disso, o formato não é de RG.
        yield suspeito("numero_rg", MotivoCampo.FORMATO_INVALIDO, "letra_no_numero")
        return
    yield nao_verificavel("numero_rg", "sem_dv_na_uf" if uf else "uf_desconhecida")


NUMERO = Regra(
    id="rg.numero",
    versao="1",
    descricao="Número do RG: em SP, 8 dígitos + DV (módulo 11 com X); fora de SP não há DV",
    fonte=FONTE_RG_SP,
    subtipos=RG,
    campos=("numero_rg",),
    funcao=_numero,
)

_ORGAO = re.compile(r"^([A-Z]+)(?:\s*[/\-]?\s*([A-Z]{2}))?$")


def _orgao(ctx: Contexto) -> Iterator[Avaliacao]:
    valor = ctx.norm("orgao_emissor")
    if valor is None:
        return
    texto = " ".join(sem_acento(valor).replace(".", "").split())
    casamento = _ORGAO.match(texto)
    if casamento is None:
        yield nao_verificavel("orgao_emissor", "formato_desconhecido")
        return
    sigla, sufixo = casamento.group(1), casamento.group(2)
    if (
        sufixo is None
        and len(sigla) > 2
        and sigla[-2:] in _UFS
        and sigla[:-2] in ctx.tabelas.orgaos
    ):
        sigla, sufixo = sigla[:-2], sigla[-2:]  # "SSPSP"
    if sufixo is not None and sufixo not in _UFS:
        yield nao_verificavel("orgao_emissor", "sufixo_nao_e_uf")
        return
    ufs = ctx.tabelas.orgaos.get(sigla)
    if ufs is None:
        yield nao_verificavel("orgao_emissor", "orgao_fora_da_tabela", MotivoCampo.FORA_DA_TABELA)
        return
    uf = ctx.uf("uf_emissao")
    if sufixo is not None and uf is not None and sufixo != uf.value:
        yield suspeito("orgao_emissor", MotivoCampo.VALOR_INCOERENTE, "uf_do_orgao_diferente")
    elif uf is not None and uf.value not in ufs:
        yield suspeito("orgao_emissor", MotivoCampo.VALOR_INCOERENTE, "orgao_inexistente_na_uf")
    elif uf is None:
        yield nao_verificavel("orgao_emissor", "uf_desconhecida")
    else:
        yield valido("orgao_emissor")


ORGAO_EMISSOR = Regra(
    id="rg.orgao_emissor",
    versao="1",
    descricao="Órgão emissor na tabela (SSP/UF) e coerente com a UF de emissão",
    fonte="app/regras/tabelas/fontes.toml#orgaos_emissores",
    subtipos=RG,
    campos=("orgao_emissor",),
    funcao=_orgao,
)

_MATRICULA = re.compile(r"(?<!\d)\d{32}(?!\d)")


def _matricula(ctx: Contexto) -> Iterator[Avaliacao]:
    """Matrícula de certidão no documento de origem: só avalia quando há 32 dígitos."""
    bruto = ctx.bruto("documento_origem")
    if bruto is None:
        return
    casamento = _MATRICULA.search(re.sub(r"[\s.\-/]", "", bruto))
    if casamento is None:
        return
    matricula = casamento.group(0)
    if not digitos.certidao_valida(matricula):
        yield suspeito("documento_origem", MotivoCampo.DIGITO_VERIFICADOR_INVALIDO, "matricula")
        return
    if matricula[8:10] != "55":
        yield suspeito("documento_origem", MotivoCampo.VALOR_INCOERENTE, "codigo_55")
        return
    ano = int(matricula[10:14])
    nascimento = ctx.data("data_nascimento")
    if ano > ctx.data_processamento.year or (nascimento and ano < nascimento.year):
        yield suspeito("documento_origem", MotivoCampo.DATA_INCOERENTE, "ano_do_registro")
        return
    yield valido("documento_origem", "matricula")


MATRICULA_CERTIDAO = Regra(
    id="rg.matricula_certidao",
    versao="1",
    descricao="Matrícula (32 dígitos): DVs, código 55 nas posições 9–10, ano ≥ nascimento",
    fonte=FONTE_CNJ,
    subtipos=RG,
    campos=("documento_origem",),
    funcao=_matricula,
)

_NATURALIDADE = re.compile(r"^(.*?)[\s/\-,]+([A-Z]{2})$")


def _naturalidade(ctx: Contexto) -> Iterator[Avaliacao]:
    valor = ctx.norm("naturalidade")
    if valor is None:
        return
    if ctx.tabelas.municipios is None:
        yield nao_verificavel("naturalidade", "tabela_ibge_ausente")
        return
    casamento = _NATURALIDADE.match(sem_acento(valor).strip())
    if casamento is None or casamento.group(2) not in _UFS:
        yield nao_verificavel("naturalidade", "uf_nao_identificada")
        return
    municipio, uf = chave_nome(casamento.group(1)), casamento.group(2)
    if municipio in ctx.tabelas.municipios.get(uf, frozenset()):
        yield valido("naturalidade")
    else:
        yield nao_verificavel(
            "naturalidade", "municipio_fora_da_tabela", MotivoCampo.FORA_DA_TABELA
        )


NATURALIDADE = Regra(
    id="rg.naturalidade",
    versao="1",
    descricao="Naturalidade (município × UF) na tabela do IBGE, com nomes históricos",
    fonte="app/regras/tabelas/fontes.toml#municipios",
    subtipos=RG,
    campos=("naturalidade",),
    funcao=_naturalidade,
)

RG_ANTIGO_VALIDO_ATE = date(2032, 2, 28)
RG_ANTIGO_DATA_IDADE = date(2022, 3, 1)


def _validade_rg_antigo(ctx: Contexto) -> Iterator[Avaliacao]:
    """Vigência começa em 01/03/2032: antes disso, o RG antigo vale para todos."""
    nascimento = ctx.data("data_nascimento")
    if nascimento is None:
        return
    if idade(nascimento, RG_ANTIGO_DATA_IDADE) >= 60:
        return  # validade indeterminada
    yield sinalizar(Sinalizacao.DOCUMENTO_VENCIDO, "rg_antigo_apos_28_02_2032")


VALIDADE_RG_ANTIGO = Regra(
    id="rg_antigo.validade",
    versao="1",
    descricao="RG antigo vale até 28/02/2032; indeterminado para quem tinha 60 anos em 01/03/2022",
    fonte=FONTE_DECRETO_10977,
    subtipos=frozenset({Subtipo.RG_ANTIGO}),
    campos=(),
    funcao=_validade_rg_antigo,
    referencia=Referencia.PROCESSAMENTO,
    vigencia_inicio=date(2032, 3, 1),
    tags=("sinalizacao",),
)

REGRAS = [NUMERO, ORGAO_EMISSOR, MATRICULA_CERTIDAO, NATURALIDADE, VALIDADE_RG_ANTIGO]
