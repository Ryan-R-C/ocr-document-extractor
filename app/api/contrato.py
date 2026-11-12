"""Contrato público da API: enums e modelos Pydantic.

Nomes idênticos aos do plano. Qualquer mudança aqui altera `docs/openapi.json`, e o teste
de contrato falha até o arquivo ser regenerado (`make openapi`) e revisado.

Este módulo não importa FastAPI: o núcleo (`app/core`) e o avaliador usam os mesmos modelos.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TipoDocumento(StrEnum):
    """Tipo declarado por quem chama. Lista a confirmar com o sistema de destino."""

    IDENTITY_DOCUMENT = "IDENTITY_DOCUMENT"
    CTPS = "CTPS"
    CPF = "CPF"


class Subtipo(StrEnum):
    RG_ANTIGO = "RG_ANTIGO"
    RG_2018_2022 = "RG_2018_2022"
    CIN = "CIN"
    CNH = "CNH"
    CPF_CARTAO = "CPF_CARTAO"
    CPF_COMPROVANTE = "CPF_COMPROVANTE"
    CPF_DIGITAL = "CPF_DIGITAL"
    CTPS_FISICA = "CTPS_FISICA"
    CTPS_DIGITAL = "CTPS_DIGITAL"
    NAO_IDENTIFICADO = "NAO_IDENTIFICADO"


class Lado(StrEnum):
    FRENTE = "FRENTE"
    VERSO = "VERSO"
    FRENTE_E_VERSO = "FRENTE_E_VERSO"
    PAGINA = "PAGINA"
    NAO_IDENTIFICADO = "NAO_IDENTIFICADO"


class UF(StrEnum):
    AC = "AC"
    AL = "AL"
    AP = "AP"
    AM = "AM"
    BA = "BA"
    CE = "CE"
    DF = "DF"
    ES = "ES"
    GO = "GO"
    MA = "MA"
    MT = "MT"
    MS = "MS"
    MG = "MG"
    PA = "PA"
    PB = "PB"
    PR = "PR"
    PE = "PE"
    PI = "PI"
    RJ = "RJ"
    RN = "RN"
    RS = "RS"
    RO = "RO"
    RR = "RR"
    SC = "SC"
    SP = "SP"
    SE = "SE"
    TO = "TO"
    NAO_IDENTIFICADO = "NAO_IDENTIFICADO"


class StatusDocumento(StrEnum):
    APROVADO = "APROVADO"
    REVISAO = "REVISAO"


class StatusLeitura(StrEnum):
    LEGIVEL = "LEGIVEL"
    PARCIAL = "PARCIAL"
    ILEGIVEL = "ILEGIVEL"
    AUSENTE = "AUSENTE"


class StatusValidacao(StrEnum):
    VALIDO = "VALIDO"
    INVALIDO = "INVALIDO"
    SUSPEITO = "SUSPEITO"
    NAO_VERIFICAVEL = "NAO_VERIFICAVEL"


class MotivoCampo(StrEnum):
    """Por que um campo não está limpo. Proposta da Fase 0, aprovada em 2026-11-02."""

    NAO_ANCORADO = "NAO_ANCORADO"
    ANCORAGEM_PARCIAL = "ANCORAGEM_PARCIAL"
    LEITURAS_DIVERGENTES = "LEITURAS_DIVERGENTES"
    DIGITO_VERIFICADOR_INVALIDO = "DIGITO_VERIFICADOR_INVALIDO"
    FORMATO_INVALIDO = "FORMATO_INVALIDO"
    DATA_INVALIDA = "DATA_INVALIDA"
    DATA_INCOERENTE = "DATA_INCOERENTE"
    CARACTERE_DUVIDOSO = "CARACTERE_DUVIDOSO"
    MANUSCRITO_SEM_DUPLA_LEITURA = "MANUSCRITO_SEM_DUPLA_LEITURA"
    REGRA_NAO_IMPLEMENTADA = "REGRA_NAO_IMPLEMENTADA"


class Sinalizacao(StrEnum):
    """Sinalizações de política: informam, nunca bloqueiam."""

    COPIA_REPROGRAFICA = "COPIA_REPROGRAFICA"
    PRINT_DE_TELA = "PRINT_DE_TELA"
    RG_ANTIGO = "RG_ANTIGO"
    DOCUMENTO_VENCIDO = "DOCUMENTO_VENCIDO"
    QR_CONFERIDO = "QR_CONFERIDO"
    MRZ_CONFERIDA = "MRZ_CONFERIDA"


class CodigoErro(StrEnum):
    REQUISICAO_INVALIDA = "REQUISICAO_INVALIDA"
    NAO_ENCONTRADO = "NAO_ENCONTRADO"
    ARQUIVO_GRANDE_DEMAIS = "ARQUIVO_GRANDE_DEMAIS"
    DOCUMENTO_NAO_PROCESSAVEL = "DOCUMENTO_NAO_PROCESSAVEL"
    PROVIDER_INDISPONIVEL = "PROVIDER_INDISPONIVEL"
    TEMPO_ESGOTADO = "TEMPO_ESGOTADO"
    ERRO_INTERNO = "ERRO_INTERNO"


class MotivoErro(StrEnum):
    # 400
    BASE64_INVALIDO = "BASE64_INVALIDO"
    TIPO_DESCONHECIDO = "TIPO_DESCONHECIDO"
    FORMATO_NAO_SUPORTADO = "FORMATO_NAO_SUPORTADO"
    REQUISICAO_MALFORMADA = "REQUISICAO_MALFORMADA"
    # 404 (modo assíncrono; ver docs/decisoes.md)
    EXTRACAO_NAO_ENCONTRADA = "EXTRACAO_NAO_ENCONTRADA"
    MODO_SINCRONO_DESLIGADO = "MODO_SINCRONO_DESLIGADO"
    # 413
    ARQUIVO_GRANDE_DEMAIS = "ARQUIVO_GRANDE_DEMAIS"
    # 422 (configurável)
    NENHUM_DOCUMENTO = "NENHUM_DOCUMENTO"
    TIPO_DIVERGENTE = "TIPO_DIVERGENTE"
    LADO_SEM_DADOS = "LADO_SEM_DADOS"
    IMAGEM_DESFOCADA = "IMAGEM_DESFOCADA"
    DOCUMENTO_CORTADO = "DOCUMENTO_CORTADO"
    REFLEXO_EXCESSIVO = "REFLEXO_EXCESSIVO"
    CAMPO_CRITICO_ILEGIVEL = "CAMPO_CRITICO_ILEGIVEL"
    # 502 / 504
    PROVIDER_INDISPONIVEL = "PROVIDER_INDISPONIVEL"
    TEMPO_ESGOTADO = "TEMPO_ESGOTADO"
    # 500 (fora do plano; ver docs/decisoes.md)
    ERRO_INTERNO = "ERRO_INTERNO"


class AcaoSugerida(StrEnum):
    REENVIAR_IMAGEM = "REENVIAR_IMAGEM"
    CORRIGIR_REQUISICAO = "CORRIGIR_REQUISICAO"
    TENTAR_NOVAMENTE = "TENTAR_NOVAMENTE"


# --- Modelos -----------------------------------------------------------------------------------

_EXEMPLO_REQUISICAO: dict[str, Any] = {
    "tipo_documento": "IDENTITY_DOCUMENT",
    "imagem_base64": "<JPEG, PNG, WebP, HEIC ou PDF>",
    "id_externo": "opcional, para rastreio",
}

_EXEMPLO_RESPOSTA: dict[str, Any] = {
    "id_processamento": "9b1c...",
    "status": "REVISAO",
    "documento": {"subtipo": "RG_ANTIGO", "uf": "SP", "lado": "VERSO"},
    "campos": {
        "cpf": {
            "valor": "123.456.789-09",
            "valor_normalizado": "12345678909",
            "status_leitura": "LEGIVEL",
            "status_validacao": "VALIDO",
            "motivos": [],
        },
        "data_expedicao": {
            "valor": "12/03/2O15",
            "valor_normalizado": None,
            "status_leitura": "PARCIAL",
            "status_validacao": "NAO_VERIFICAVEL",
            "motivos": ["LEITURAS_DIVERGENTES"],
        },
    },
    "sinalizacoes": ["COPIA_REPROGRAFICA"],
    "versao_pipeline": "1.0.0+openai:<modelo>+regras:1.0",
}

_EXEMPLO_ERRO: dict[str, Any] = {
    "id_processamento": "9b1c...",
    "erro": "DOCUMENTO_NAO_PROCESSAVEL",
    "motivos": [
        {"codigo": "CAMPO_CRITICO_ILEGIVEL", "campo": "cpf"},
        {"codigo": "REFLEXO_EXCESSIVO", "detalhe": "reflexo sobre o número do registro"},
    ],
    "acao_sugerida": "REENVIAR_IMAGEM",
}

_EXEMPLO_ACEITA: dict[str, Any] = {
    "id_processamento": "9b1c...",
    "situacao": "PENDENTE",
    "url_consulta": "/v1/extracoes/9b1c...",
    "posicao_fila": 3,
    "recebido_em": "2026-11-12T10:00:00Z",
}

EXEMPLOS = {
    "RequisicaoExtracao": _EXEMPLO_REQUISICAO,
    "RespostaAceita": _EXEMPLO_ACEITA,
    "RespostaExtracao": _EXEMPLO_RESPOSTA,
    "RespostaErro": _EXEMPLO_ERRO,
}


class RequisicaoExtracao(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [_EXEMPLO_REQUISICAO]})

    tipo_documento: TipoDocumento
    imagem_base64: str = Field(description="Arquivo em base64: JPEG, PNG, WebP, HEIC ou PDF.")
    id_externo: str | None = Field(
        default=None, max_length=200, description="Identificador de quem chama, para rastreio."
    )


class Documento(BaseModel):
    subtipo: Subtipo
    uf: UF
    lado: Lado


class Campo(BaseModel):
    valor: str | None = Field(description="Transcrição literal; null quando ilegível ou ausente.")
    valor_normalizado: str | None = Field(
        description="Valor normalizado pelo código; null quando não há leitura limpa."
    )
    status_leitura: StatusLeitura
    status_validacao: StatusValidacao
    motivos: list[MotivoCampo] = Field(default_factory=list)


class RespostaExtracao(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [_EXEMPLO_RESPOSTA]})

    id_processamento: str
    status: StatusDocumento
    documento: Documento
    campos: dict[str, Campo]
    sinalizacoes: list[Sinalizacao] = Field(default_factory=list)
    versao_pipeline: str


class MotivoErroDetalhado(BaseModel):
    codigo: MotivoErro
    campo: str | None = None
    detalhe: str | None = Field(
        default=None, description="Texto curto, sem dados pessoais do documento."
    )


class RespostaErro(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [_EXEMPLO_ERRO]})

    id_processamento: str
    erro: CodigoErro
    motivos: list[MotivoErroDetalhado]
    acao_sugerida: AcaoSugerida


class SituacaoExtracao(StrEnum):
    """Modo assíncrono: onde a extração está. Concluída, a consulta devolve o resultado."""

    PENDENTE = "PENDENTE"
    PROCESSANDO = "PROCESSANDO"
    CONCLUIDA = "CONCLUIDA"


class RespostaAceita(BaseModel):
    """202 do modo assíncrono: no POST e na consulta enquanto não conclui."""

    model_config = ConfigDict(json_schema_extra={"examples": [_EXEMPLO_ACEITA]})

    id_processamento: str
    situacao: SituacaoExtracao
    url_consulta: str = Field(description="GET aqui devolve o resultado quando concluir.")
    posicao_fila: int | None = Field(
        default=None, description="Posição entre as pendentes (1 = a próxima); só em PENDENTE."
    )
    recebido_em: datetime


class RespostaSaude(BaseModel):
    status: str = Field(description="ok ou degradado")
    versao_pipeline: str
    componentes: dict[str, str]
