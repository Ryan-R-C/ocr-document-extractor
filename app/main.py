"""Aplicação FastAPI: só HTTP. A esteira e o serviço não dependem daqui."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.api.contrato import (
    EXEMPLOS,
    AcaoSugerida,
    CodigoErro,
    MotivoErro,
    MotivoErroDetalhado,
    RespostaErro,
)
from app.api.rotas import id_da_requisicao, router
from app.armazenamento.db import banco_ok, criar_engine, criar_sessoes
from app.armazenamento.objetos import Objetos
from app.armazenamento.repositorio import Repositorio
from app.config import Settings, obter_settings
from app.core.versao import VERSAO_SERVICO
from app.logs import configurar_logs, obter_logger
from app.montagem import criar_esteira
from app.servico import ServicoExtracao

log = obter_logger(__name__)

VerificadorSaude = Callable[[], Awaitable[dict[str, str]]]


def _erro(
    request: Request,
    status: int,
    codigo: CodigoErro,
    motivos: list[MotivoErroDetalhado],
    acao: AcaoSugerida,
) -> JSONResponse:
    corpo = RespostaErro(
        id_processamento=id_da_requisicao(request), erro=codigo, motivos=motivos, acao_sugerida=acao
    )
    return JSONResponse(
        status_code=status, content=corpo.model_dump(mode="json", exclude_none=True)
    )


def _motivos_validacao(erro: RequestValidationError) -> list[MotivoErroDetalhado]:
    """Traduz erros de validação sem ecoar o conteúdo enviado (pode ter dados pessoais)."""
    motivos: list[MotivoErroDetalhado] = []
    for item in erro.errors():
        local = [str(p) for p in item.get("loc", ()) if p != "body"]
        campo = ".".join(local) or None
        if local[:1] == ["tipo_documento"] and item.get("type") == "enum":
            motivos.append(MotivoErroDetalhado(codigo=MotivoErro.TIPO_DESCONHECIDO, campo=campo))
        else:
            motivos.append(
                MotivoErroDetalhado(
                    codigo=MotivoErro.REQUISICAO_MALFORMADA,
                    campo=campo,
                    detalhe=str(item.get("type", "invalido")),
                )
            )
    return motivos or [MotivoErroDetalhado(codigo=MotivoErro.REQUISICAO_MALFORMADA)]


class LimiteCorpo(BaseHTTPMiddleware):
    """Recusa com 413 corpos acima do limite antes de ler e decodificar o JSON."""

    def __init__(self, app: Any, limite_bytes: int) -> None:
        super().__init__(app)
        self.limite = limite_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tamanho = request.headers.get("content-length")
        if tamanho and tamanho.isdigit() and int(tamanho) > self.limite:
            return _erro(
                request,
                413,
                CodigoErro.ARQUIVO_GRANDE_DEMAIS,
                [MotivoErroDetalhado(codigo=MotivoErro.ARQUIVO_GRANDE_DEMAIS)],
                AcaoSugerida.REENVIAR_IMAGEM,
            )
        return await call_next(request)


def criar_app(
    settings: Settings | None = None,
    servico: ServicoExtracao | None = None,
    verificar_saude: VerificadorSaude | None = None,
) -> FastAPI:
    settings = settings or obter_settings()
    configurar_logs(settings.log_level, settings.log_json)

    aquecer: Callable[[], None] | None = None
    if servico is None:
        engine = criar_engine(settings)
        objetos = Objetos(settings)
        esteira = criar_esteira(settings)
        servico = ServicoExtracao(settings, esteira, Repositorio(criar_sessoes(engine)), objetos)
        candidato = getattr(esteira.deps.leitor_texto, "aquecer", None)
        if settings.leitor_texto_aquecer and callable(candidato):
            aquecer = candidato

        async def verificar_padrao() -> dict[str, str]:
            banco, s3 = await asyncio.gather(
                asyncio.to_thread(banco_ok, engine), asyncio.to_thread(objetos.ok)
            )
            return {"banco": "ok" if banco else "falha", "objetos": "ok" if s3 else "falha"}

        verificar_saude = verificar_saude or verificar_padrao

    async def sem_verificacao() -> dict[str, str]:
        return {}

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        # Carrega o modelo do OCR antes da primeira requisição.
        if aquecer is not None:
            inicio = asyncio.get_running_loop().time()
            await asyncio.to_thread(aquecer)
            log.info(
                "leitor_texto_aquecido",
                segundos=round(asyncio.get_running_loop().time() - inicio, 2),
            )
        yield

    app = FastAPI(
        title="Extração e validação de documentos admissionais",
        version=VERSAO_SERVICO,
        description="Recebe a imagem de um documento com o tipo declarado, extrai os campos e "
        "valida cada valor dentro do próprio documento.",
        lifespan=ciclo_de_vida,
    )
    _openapi_padrao = app.openapi

    def openapi_com_exemplos() -> dict[str, Any]:
        # O FastAPI remove todo `null` ao gerar o OpenAPI, inclusive dos exemplos; os
        # exemplos do plano têm `valor_normalizado: null`, então são reinseridos aqui.
        if app.openapi_schema is None:
            documento = _openapi_padrao()
            schemas = documento.get("components", {}).get("schemas", {})
            for nome, exemplo in EXEMPLOS.items():
                if nome in schemas:
                    schemas[nome]["examples"] = [exemplo]
            app.openapi_schema = documento
        return app.openapi_schema

    app.openapi = openapi_com_exemplos  # type: ignore[method-assign]
    app.state.settings = settings
    app.state.servico = servico
    app.state.verificar_saude = verificar_saude or sem_verificacao
    app.include_router(router)

    # Base64 ocupa ~4/3 do binário; folga para o JSON em volta.
    limite_corpo = math.ceil(settings.arquivo_max_bytes / 3) * 4 + 64 * 1024
    app.add_middleware(LimiteCorpo, limite_bytes=int(limite_corpo * 1.05))

    @app.exception_handler(RequestValidationError)
    async def _validacao(request: Request, erro: RequestValidationError) -> JSONResponse:
        return _erro(
            request,
            400,
            CodigoErro.REQUISICAO_INVALIDA,
            _motivos_validacao(erro),
            AcaoSugerida.CORRIGIR_REQUISICAO,
        )

    @app.exception_handler(Exception)
    async def _interno(request: Request, erro: Exception) -> JSONResponse:
        log.error(
            "erro_interno", id_processamento=id_da_requisicao(request), erro=type(erro).__name__
        )
        return _erro(
            request,
            500,
            CodigoErro.ERRO_INTERNO,
            [MotivoErroDetalhado(codigo=MotivoErro.ERRO_INTERNO)],
            AcaoSugerida.TENTAR_NOVAMENTE,
        )

    return app


def app_padrao() -> FastAPI:
    """Fábrica usada pelo uvicorn (`--factory`)."""
    return criar_app()
