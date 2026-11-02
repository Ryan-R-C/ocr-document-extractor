"""Definição declarativa dos campos por subtipo: fonte única para schema, decisão e avaliação.

Campos críticos conforme a tabela "Campos por tipo de documento" do plano (proposta a
confirmar com o sistema de destino). Nomes de campo em snake_case, propostos na Fase 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.api.contrato import (
    UF,
    Lado,
    StatusLeitura,
    Subtipo,
    TipoDocumento,
)
from app.providers.interfaces import QualidadeCaptura
from app.schemas.normalizacao import TipoValor, normalizar

VERSAO_SCHEMAS = "0.1"


class Escrita(StrEnum):
    IMPRESSO = "IMPRESSO"
    MANUSCRITO = "MANUSCRITO"


@dataclass(frozen=True)
class DefinicaoCampo:
    nome: str
    critico: bool
    tipo_valor: TipoValor
    rotulos: tuple[str, ...] = ()
    escrita: Escrita = Escrita.IMPRESSO

    def normalizar(self, valor: str | None) -> str | None:
        return normalizar(self.tipo_valor, valor)


def _c(
    nome: str, tipo: TipoValor, *rotulos: str, escrita: Escrita = Escrita.IMPRESSO
) -> DefinicaoCampo:
    return DefinicaoCampo(nome, True, tipo, rotulos, escrita)


def _i(
    nome: str, tipo: TipoValor, *rotulos: str, escrita: Escrita = Escrita.IMPRESSO
) -> DefinicaoCampo:
    return DefinicaoCampo(nome, False, tipo, rotulos, escrita)


T = TipoValor
M = Escrita.MANUSCRITO

_FILIACAO = (
    _i("filiacao_linha_1", T.TEXTO, "FILIAÇÃO"),
    _i("filiacao_linha_2", T.TEXTO, "FILIAÇÃO"),
)

_RG_CRITICOS = (
    _c("numero_rg", T.ALFANUMERICO, "REGISTRO GERAL", "RG"),
    _c("orgao_emissor", T.TEXTO, "ÓRGÃO EXPEDIDOR", "EXPEDIDOR"),
    _c("uf_emissao", T.UF, "UF"),
    _c("data_expedicao", T.DATA, "DATA DE EXPEDIÇÃO", "EXPEDIÇÃO"),
    _c("nome", T.TEXTO, "NOME"),
    _c("data_nascimento", T.DATA, "DATA DE NASCIMENTO", "NASCIMENTO"),
)

_NUMEROS_EXTRAS = (
    _i("nis", T.DIGITOS, "NIS", "PIS", "PASEP"),
    _i("titulo_eleitor", T.DIGITOS, "TÍTULO DE ELEITOR"),
    _i("numero_cnh", T.DIGITOS, "CNH"),
    _i("numero_ctps", T.ALFANUMERICO, "CTPS"),
    _i("cns", T.DIGITOS, "CNS"),
)

_CONTRATO = (
    _i("contrato_empregador", T.TEXTO, "EMPREGADOR", escrita=M),
    _i("contrato_cnpj", T.CNPJ, "CNPJ", "CGC", escrita=M),
    _i("contrato_cargo", T.TEXTO, "CARGO", escrita=M),
    _i("contrato_admissao", T.DATA, "ADMISSÃO", escrita=M),
    _i("contrato_saida", T.DATA, "SAÍDA", escrita=M),
)

_CPF_CAMPOS = (
    _c("cpf", T.CPF, "CPF", "NÚMERO DE INSCRIÇÃO NO CPF"),
    _i("nome", T.TEXTO, "NOME"),
    _i("data_nascimento", T.DATA, "DATA DE NASCIMENTO", "NASCIMENTO"),
)

CAMPOS_POR_SUBTIPO: dict[Subtipo, tuple[DefinicaoCampo, ...]] = {
    Subtipo.RG_ANTIGO: (
        *_RG_CRITICOS,
        *_FILIACAO,
        _i("naturalidade", T.TEXTO, "NATURALIDADE"),
        _i("documento_origem", T.TEXTO, "DOC. ORIGEM", "DOCUMENTO DE ORIGEM"),
        _i("cpf", T.CPF, "CPF"),
    ),
    Subtipo.RG_2018_2022: (
        *_RG_CRITICOS,
        _c("cpf", T.CPF, "CPF"),
        *_NUMEROS_EXTRAS,
        *_FILIACAO,
        _i("naturalidade", T.TEXTO, "NATURALIDADE"),
        _i("documento_origem", T.TEXTO, "DOC. ORIGEM", "DOCUMENTO DE ORIGEM"),
    ),
    Subtipo.CIN: (
        _c("cpf", T.CPF, "NÚMERO DO DOCUMENTO", "CPF"),
        _c("nome", T.TEXTO, "NOME"),
        _c("data_nascimento", T.DATA, "DATA DE NASCIMENTO"),
        _c("data_expedicao", T.DATA, "DATA DE EXPEDIÇÃO", "EXPEDIÇÃO"),
        _c("validade", T.DATA, "VALIDADE"),
        _c("orgao_emissor", T.TEXTO, "ÓRGÃO EXPEDIDOR"),
        _c("uf_emissao", T.UF, "UF"),
        *_FILIACAO,
        _i("naturalidade", T.TEXTO, "NATURALIDADE", "LOCAL DE NASCIMENTO"),
        _i("mrz", T.TEXTO),
        _i("nis", T.DIGITOS, "NIS"),
        _i("titulo_eleitor", T.DIGITOS, "TÍTULO DE ELEITOR"),
    ),
    Subtipo.CNH: (
        _c("nome", T.TEXTO, "NOME"),
        _c("cpf", T.CPF, "CPF"),
        _c("data_nascimento", T.DATA, "DATA NASCIMENTO", "DATA, LOCAL E UF DE NASCIMENTO"),
        _c("numero_registro", T.DIGITOS, "Nº REGISTRO", "REGISTRO"),
        _c("validade", T.DATA, "VALIDADE"),
        _c("categoria", T.CATEGORIA_CNH, "CAT. HAB.", "CATEGORIA"),
        _i("documento_identidade", T.ALFANUMERICO, "DOC. IDENTIDADE"),
        _i("orgao_emissor_identidade", T.TEXTO, "ÓRG. EMISSOR"),
        *_FILIACAO,
        _i("primeira_habilitacao", T.DATA, "1ª HABILITAÇÃO"),
        _i("data_emissao", T.DATA, "DATA EMISSÃO"),
        _i("local_emissao", T.TEXTO, "LOCAL"),
        _i("observacoes", T.TEXTO, "OBSERVAÇÕES"),
    ),
    Subtipo.CPF_CARTAO: _CPF_CAMPOS,
    Subtipo.CPF_COMPROVANTE: _CPF_CAMPOS,
    Subtipo.CPF_DIGITAL: _CPF_CAMPOS,
    Subtipo.CTPS_FISICA: (
        _c("numero_ctps", T.DIGITOS, "NÚMERO"),
        _c("serie_ctps", T.ALFANUMERICO, "SÉRIE"),
        _c("uf_ctps", T.UF, "UF"),
        _i("nome", T.TEXTO, "NOME"),
        _i("data_nascimento", T.DATA, "DATA DE NASCIMENTO"),
        _i("data_emissao", T.DATA, "DATA DE EMISSÃO"),
        *_CONTRATO,
        _i("contrato_salario", T.TEXTO, "REMUNERAÇÃO", escrita=M),
    ),
    Subtipo.CTPS_DIGITAL: (
        _c("cpf", T.CPF, "CPF"),
        _i("nome", T.TEXTO, "NOME"),
        *(_i(d.nome, d.tipo_valor, *d.rotulos) for d in _CONTRATO),
    ),
    Subtipo.NAO_IDENTIFICADO: (),
}

SUBTIPOS_POR_TIPO: dict[TipoDocumento, tuple[Subtipo, ...]] = {
    TipoDocumento.IDENTITY_DOCUMENT: (
        Subtipo.RG_ANTIGO,
        Subtipo.RG_2018_2022,
        Subtipo.CIN,
        Subtipo.CNH,
    ),
    TipoDocumento.CPF: (Subtipo.CPF_CARTAO, Subtipo.CPF_COMPROVANTE, Subtipo.CPF_DIGITAL),
    TipoDocumento.CTPS: (Subtipo.CTPS_FISICA, Subtipo.CTPS_DIGITAL),
}


def campos_do_subtipo(subtipo: Subtipo) -> tuple[DefinicaoCampo, ...]:
    return CAMPOS_POR_SUBTIPO[subtipo]


def definicao(subtipo: Subtipo, nome: str) -> DefinicaoCampo | None:
    return next((d for d in CAMPOS_POR_SUBTIPO[subtipo] if d.nome == nome), None)


def nomes_criticos(subtipo: Subtipo) -> list[str]:
    return [d.nome for d in CAMPOS_POR_SUBTIPO[subtipo] if d.critico]


# --- Schemas JSON enviados ao VLM ----------------------------------------------------------------
# Invariantes: sem pattern/format/enum nos campos de valor; toda lista fechada tem
# NAO_IDENTIFICADO; nenhum campo de raciocínio; todos os campos obrigatórios e
# additionalProperties=false (compatível com saída estruturada estrita).


def schema_campo() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "rotulo_visto", "texto_literal"],
        "properties": {
            "status": {"type": "string", "enum": [s.value for s in StatusLeitura]},
            "rotulo_visto": {"type": "string"},
            "texto_literal": {"type": "string"},
        },
    }


def _enum(valores: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": valores}


def schema_campos(subtipo: Subtipo) -> dict[str, Any]:
    nomes = [d.nome for d in CAMPOS_POR_SUBTIPO[subtipo]]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": nomes,
        "properties": {n: schema_campo() for n in nomes},
    }


def schema_leitura(tipo: TipoDocumento) -> dict[str, Any]:
    """Schema de uma leitura visual: classificação + campos do subtipo (anyOf por subtipo)."""
    variantes: list[dict[str, Any]] = []
    for subtipo in (*SUBTIPOS_POR_TIPO[tipo], Subtipo.NAO_IDENTIFICADO):
        variantes.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["subtipo", "campos"],
                "properties": {
                    "subtipo": _enum([subtipo.value]),
                    "campos": schema_campos(subtipo),
                },
            }
        )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "tipo_documento_detectado",
            "uf",
            "lado",
            "qualidade_captura",
            "documento",
        ],
        "properties": {
            "tipo_documento_detectado": _enum(
                [t.value for t in TipoDocumento] + ["NAO_IDENTIFICADO"]
            ),
            "uf": _enum([u.value for u in UF]),
            "lado": _enum([lado.value for lado in Lado]),
            "qualidade_captura": _enum([q.value for q in QualidadeCaptura]),
            "documento": {"anyOf": variantes},
        },
    }


def schema_releitura() -> dict[str, Any]:
    return schema_campo()
