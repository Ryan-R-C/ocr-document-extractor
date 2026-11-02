"""Rotas HTTP. Toda resposta de erro traz `id_processamento`."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.contrato import RequisicaoExtracao, RespostaErro, RespostaExtracao, RespostaSaude

router = APIRouter()

_ERRO = {"model": RespostaErro}

RESPOSTAS_EXTRACAO: dict[int | str, dict[str, Any]] = {
    400: {**_ERRO, "description": "Requisição malformada"},
    413: {**_ERRO, "description": "Arquivo acima do limite"},
    422: {
        **_ERRO,
        "description": "Imagem válida, sem como extrair os campos críticos "
        "(código configurável em HTTP_STATUS_FALHA_EXTRACAO)",
    },
    500: {**_ERRO, "description": "Erro interno"},
    502: {**_ERRO, "description": "Provider indisponível após nova tentativa e fallback"},
    504: {**_ERRO, "description": "Provider estourou o tempo após nova tentativa e fallback"},
}


def id_da_requisicao(request: Request) -> str:
    atual = getattr(request.state, "id_processamento", None)
    if not atual:
        atual = str(uuid.uuid4())
        request.state.id_processamento = atual
    return str(atual)


@router.post(
    "/v1/extracoes",
    response_model=RespostaExtracao,
    responses=RESPOSTAS_EXTRACAO,
    summary="Extrai e valida os campos de uma imagem de documento",
)
async def extrair(requisicao: RequisicaoExtracao, request: Request) -> JSONResponse:
    servico = request.app.state.servico
    resultado = await servico.processar(requisicao, id_da_requisicao(request))
    return JSONResponse(status_code=resultado.http_status, content=resultado.corpo)


@router.get("/health", response_model=RespostaSaude, responses={503: {"model": RespostaSaude}})
async def saude(request: Request) -> JSONResponse:
    verificar = request.app.state.verificar_saude
    componentes: dict[str, str] = await verificar()
    ok = all(v == "ok" for v in componentes.values())
    corpo = RespostaSaude(
        status="ok" if ok else "degradado",
        versao_pipeline=request.app.state.servico.esteira.versao,
        componentes=componentes,
    )
    return JSONResponse(status_code=200 if ok else 503, content=corpo.model_dump(mode="json"))
