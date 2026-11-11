"""Mapeamento de falhas do núcleo para códigos HTTP e corpo de erro do contrato."""

from __future__ import annotations

from typing import Any

from app.api.contrato import RespostaErro
from app.config import Settings
from app.core.erros import (
    FalhaArquivoGrande,
    FalhaExtracao,
    FalhaProcessamento,
    FalhaProviderIndisponivel,
    FalhaRequisicao,
    FalhaTempoEsgotado,
)


def status_http(falha: FalhaProcessamento, settings: Settings) -> int:
    if isinstance(falha, FalhaRequisicao):
        return 400
    if isinstance(falha, FalhaArquivoGrande):
        return 413
    if isinstance(falha, FalhaExtracao):
        return settings.http_status_falha_extracao
    if isinstance(falha, FalhaProviderIndisponivel):
        return 502
    if isinstance(falha, FalhaTempoEsgotado):
        return 504
    raise TypeError(f"falha sem mapeamento HTTP: {type(falha).__name__}")


def corpo_erro(id_processamento: str, falha: FalhaProcessamento) -> dict[str, Any]:
    resposta = RespostaErro(
        id_processamento=id_processamento,
        erro=falha.codigo,
        motivos=falha.motivos,
        acao_sugerida=falha.acao_sugerida,
    )
    return resposta.model_dump(mode="json", exclude_none=True)
