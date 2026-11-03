"""Leitor visual OpenAI: monta a requisição, chama com novas tentativas e interpreta a resposta.

`montar_requisicao` e `interpretar_resposta` são separados da chamada para que a rodada
Batch do avaliador reutilize exatamente o mesmo corpo e a mesma leitura do resultado.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from openai import AsyncOpenAI

from app.config import Settings
from app.providers.custo import calcular_custo
from app.providers.interfaces import (
    ErroProvider,
    ImagemRecusadaLocalmente,
    MetricasChamada,
    ModoChamada,
    PedidoVisual,
    RespostaVisual,
    TempoEsgotadoProvider,
)
from app.providers.openai.imagem import estimar_tokens_imagem, patches

NOME = "openai"

# Erros transitórios: nova tentativa com espera exponencial e jitter.
_TRANSITORIOS: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,  # inclui APITimeoutError
)


class ProviderMalConfigurado(RuntimeError):
    pass


class ImagemAcimaDoLimite(ImagemRecusadaLocalmente):
    """A imagem passaria do limite de patches do modelo: recusada antes de enviar."""


class RespostaNaoUtilizavel(ErroProvider):
    """Resposta incompleta, recusada pelo modelo ou com JSON inválido."""


class LeitorVisualOpenAI:
    nome = NOME

    def __init__(
        self,
        settings: Settings,
        cliente: AsyncOpenAI | None = None,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
        aleatorio: Callable[[], float] = random.random,
    ) -> None:
        self.settings = settings
        if cliente is None:
            if settings.openai_api_key is None:
                raise ProviderMalConfigurado("OPENAI_API_KEY não configurada")
            cliente = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                base_url=settings.openai_base_url,
                max_retries=0,  # as novas tentativas são nossas, com orçamento de tempo
                timeout=settings.provider_timeout_s,
            )
        self.cliente = cliente
        self._dormir = dormir
        self._aleatorio = aleatorio

    # --- Requisição ----------------------------------------------------------------------

    def montar_requisicao(self, pedido: PedidoVisual) -> dict[str, Any]:
        regra = self.settings.regra_imagem(pedido.modelo)
        quantos = patches(pedido.imagem.largura, pedido.imagem.altura, regra)
        if quantos > regra.patches_max:
            raise ImagemAcimaDoLimite(
                f"{quantos} patches acima do limite de {regra.patches_max} para {pedido.modelo}"
            )
        data_url = f"data:{pedido.imagem.mime};base64," + base64.b64encode(
            pedido.imagem.conteudo
        ).decode("ascii")
        corpo: dict[str, Any] = {
            "model": pedido.modelo,
            "instructions": pedido.prompt,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": data_url,
                            "detail": self.settings.leitor_visual_detalhe,
                        }
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": pedido.nome_schema[:64],
                    "schema": pedido.schema,
                    "strict": True,
                }
            },
            "max_output_tokens": self.settings.leitor_visual_max_tokens_saida,
            # Nada fica guardado no provider: o pacote de auditoria é nosso.
            "store": False,
        }
        if self.settings.leitor_visual_esforco_raciocinio is not None:
            corpo["reasoning"] = {"effort": self.settings.leitor_visual_esforco_raciocinio}
        if pedido.modo is ModoChamada.FLEX:
            corpo["service_tier"] = "flex"
        return corpo

    # --- Chamada com novas tentativas ----------------------------------------------------

    async def ler(self, pedido: PedidoVisual) -> RespostaVisual:
        corpo = self.montar_requisicao(pedido)
        prazo = time.monotonic() + self.settings.provider_timeout_s
        espera = self.settings.provider_espera_inicial_s
        ultimo: Exception | None = None
        for tentativa in range(1, self.settings.provider_tentativas_max + 1):
            inicio = time.perf_counter()
            try:
                restante = max(0.1, prazo - time.monotonic())
                resposta = await self.cliente.with_options(timeout=restante).responses.create(
                    **corpo
                )
            except _TRANSITORIOS as erro:
                ultimo = erro
            except openai.APIStatusError as erro:
                if erro.status_code >= 500:
                    ultimo = erro
                else:
                    # 400/401/403/404/422: tentar de novo não resolve.
                    raise ErroProvider(
                        f"openai recusou a requisição ({erro.status_code})"
                    ) from None
            else:
                latencia = (time.perf_counter() - inicio) * 1000
                return self.interpretar_resposta(
                    resposta.model_dump(mode="json"), pedido, latencia, tentativa
                )
            if tentativa == self.settings.provider_tentativas_max:
                break
            pausa = min(self.settings.provider_espera_max_s, espera) * (0.5 + self._aleatorio())
            if time.monotonic() + pausa >= prazo:
                break
            await self._dormir(pausa)
            espera *= 2
        if isinstance(ultimo, openai.APITimeoutError) or time.monotonic() >= prazo:
            raise TempoEsgotadoProvider("openai: tempo esgotado") from None
        raise ErroProvider(f"openai indisponível ({type(ultimo).__name__})") from None

    # --- Resposta ------------------------------------------------------------------------

    def interpretar_resposta(
        self,
        bruta: dict[str, Any],
        pedido: PedidoVisual,
        latencia_ms: float = 0.0,
        tentativas: int = 1,
    ) -> RespostaVisual:
        metricas = self._metricas(bruta, pedido, latencia_ms, tentativas)
        status = bruta.get("status")
        if status not in (None, "completed"):
            motivo = (bruta.get("incomplete_details") or {}).get("reason", status)
            raise RespostaNaoUtilizavel(f"resposta não concluída: {motivo}")
        textos: list[str] = []
        for item in bruta.get("output") or []:
            if item.get("type") != "message":
                continue
            for parte in item.get("content") or []:
                if parte.get("type") == "refusal":
                    raise RespostaNaoUtilizavel("o modelo recusou a tarefa")
                if parte.get("type") == "output_text":
                    textos.append(parte.get("text", ""))
        try:
            dados = json.loads("".join(textos))
        except json.JSONDecodeError:
            raise RespostaNaoUtilizavel("JSON inválido na resposta") from None
        if not isinstance(dados, dict):
            raise RespostaNaoUtilizavel("a resposta não é um objeto JSON")
        return RespostaVisual(dados=dados, resposta_bruta=bruta, metricas=metricas)

    def _metricas(
        self, bruta: dict[str, Any], pedido: PedidoVisual, latencia_ms: float, tentativas: int
    ) -> MetricasChamada:
        uso = bruta.get("usage") or {}
        entrada = int(uso.get("input_tokens") or 0)
        # output_tokens já inclui os de raciocínio, cobrados como saída.
        saida = int(uso.get("output_tokens") or 0)
        raciocinio = int((uso.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
        cache = int((uso.get("input_tokens_details") or {}).get("cached_tokens") or 0)
        modelo = str(bruta.get("model") or pedido.modelo)
        modelo_preco = pedido.modelo if pedido.modelo in self.settings.precos_modelos else modelo
        usd, brl = calcular_custo(self.settings, modelo_preco, entrada, saida, pedido.modo)
        return MetricasChamada(
            provider=NOME,
            modelo=pedido.modelo,
            etapa=pedido.etapa.value,
            tokens_entrada=entrada,
            tokens_saida=saida,
            custo_usd=usd,
            custo_brl=brl,
            latencia_ms=latencia_ms,
            modo=pedido.modo,
            tokens_raciocinio=raciocinio,
            tokens_cache=cache,
            tokens_imagem_estimados=estimar_tokens_imagem(
                pedido.imagem.largura,
                pedido.imagem.altura,
                self.settings.regra_imagem(pedido.modelo),
            ),
            tentativas=tentativas,
        )
