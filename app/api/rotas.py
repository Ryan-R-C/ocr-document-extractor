"""Rotas HTTP. Toda resposta de erro traz `id_processamento`."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.contrato import (
    AcaoSugerida,
    CodigoErro,
    MotivoErro,
    RequisicaoExtracao,
    RespostaAceita,
    RespostaErro,
    RespostaExtracao,
    RespostaSaude,
)

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
    responses={
        **RESPOSTAS_EXTRACAO,
        404: {**_ERRO, "description": "Modo síncrono desligado (EXTRACAO_SINCRONA=false)"},
    },
    summary="Extrai e valida os campos de uma imagem de documento (síncrono)",
    description="Desligado por padrão em produção: use `POST /v1/extracoes/assincronas`.",
)
async def extrair(requisicao: RequisicaoExtracao, request: Request) -> JSONResponse:
    if not request.app.state.settings.extracao_sincrona:
        from app.servico import erro_simples

        corpo = erro_simples(
            id_da_requisicao(request),
            CodigoErro.NAO_ENCONTRADO,
            MotivoErro.MODO_SINCRONO_DESLIGADO,
            AcaoSugerida.CORRIGIR_REQUISICAO,
        )
        return JSONResponse(status_code=404, content=corpo)
    servico = request.app.state.servico
    resultado = await servico.processar(requisicao, id_da_requisicao(request))
    return JSONResponse(status_code=resultado.http_status, content=resultado.corpo)


@router.post(
    "/v1/extracoes/assincronas",
    status_code=202,
    response_model=RespostaAceita,
    responses={
        400: RESPOSTAS_EXTRACAO[400],
        413: RESPOSTAS_EXTRACAO[413],
        500: RESPOSTAS_EXTRACAO[500],
    },
    summary="Põe a extração na fila e responde 202 na hora",
    description="Erros de requisição (400, 413) voltam na hora. O resultado sai em "
    "`GET /v1/extracoes/{id_processamento}` (header `Location`).",
)
async def extrair_assincrono(requisicao: RequisicaoExtracao, request: Request) -> JSONResponse:
    servico = request.app.state.servico
    resultado = await servico.enfileirar(requisicao, id_da_requisicao(request))
    return JSONResponse(
        status_code=resultado.http_status, content=resultado.corpo, headers=resultado.headers
    )


@router.get(
    "/v1/extracoes/{id_processamento}",
    response_model=RespostaExtracao,
    responses={
        202: {"model": RespostaAceita, "description": "Ainda na fila ou em processamento"},
        404: {**_ERRO, "description": "Id desconhecido ou resultado expirado (TTL)"},
        **{k: v for k, v in RESPOSTAS_EXTRACAO.items() if k not in (400, 413)},
    },
    summary="Consulta uma extração assíncrona",
    description="Concluída, devolve o mesmo corpo e o mesmo código HTTP do modo síncrono.",
)
async def consultar(id_processamento: str, request: Request) -> JSONResponse:
    request.state.id_processamento = id_processamento[:36]
    servico = request.app.state.servico
    resultado = await servico.consultar(id_processamento[:36])
    return JSONResponse(
        status_code=resultado.http_status, content=resultado.corpo, headers=resultado.headers
    )


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
