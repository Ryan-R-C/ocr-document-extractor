"""Composição de providers: tempo limite, fallback e limites por provider.

As novas tentativas com espera crescente (429/5xx) ficam dentro de cada adaptador; aqui só
entra o que é comum: tempo limite total por chamada, provider de fallback e controle de
concorrência/tokens por minuto.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.providers.interfaces import (
    ErroProvider,
    Imagem,
    LeitorTexto,
    LeitorVisual,
    LeituraTexto,
    MetricasChamada,
    PedidoVisual,
    RespostaVisual,
    TempoEsgotadoProvider,
)
from app.providers.limites import LimitadorProvider


async def _chamar[R: (RespostaVisual, LeituraTexto)](
    chamada: Callable[[], Awaitable[R]],
    timeout_s: float,
    limitador: LimitadorProvider | None,
) -> R:
    async def executar() -> R:
        if limitador is None:
            return await chamada()
        async with limitador.chamada() as registrar:
            resultado = await chamada()
            metricas: MetricasChamada = resultado.metricas
            registrar(metricas.tokens_entrada + metricas.tokens_saida)
            return resultado

    try:
        return await asyncio.wait_for(executar(), timeout=timeout_s)
    except TimeoutError:
        raise TempoEsgotadoProvider("tempo limite") from None


async def _com_fallback[R: (RespostaVisual, LeituraTexto)](
    principal: Callable[[], Awaitable[R]],
    fallback: Callable[[], Awaitable[R]] | None,
) -> R:
    try:
        return await principal()
    except ErroProvider as erro_principal:
        if fallback is None:
            raise
        try:
            return await fallback()
        except TempoEsgotadoProvider:
            raise
        except ErroProvider:
            # Se o principal estourou o tempo e o fallback falhou, vale a falha do fallback.
            raise ErroProvider("principal e fallback falharam") from erro_principal


class LeitorVisualComposto:
    def __init__(
        self,
        principal: LeitorVisual,
        fallback: LeitorVisual | None,
        timeout_s: float,
        limitadores: dict[str, LimitadorProvider],
    ) -> None:
        self.principal = principal
        self.fallback = fallback
        self.timeout_s = timeout_s
        self.limitadores = limitadores
        self.nome = principal.nome

    async def ler(self, pedido: PedidoVisual) -> RespostaVisual:
        fb = self.fallback

        async def principal() -> RespostaVisual:
            return await _chamar(
                lambda: self.principal.ler(pedido),
                self.timeout_s,
                self.limitadores.get(self.principal.nome),
            )

        async def reserva() -> RespostaVisual:
            assert fb is not None
            return await _chamar(
                lambda: fb.ler(pedido), self.timeout_s, self.limitadores.get(fb.nome)
            )

        return await _com_fallback(principal, reserva if fb else None)


class LeitorTextoComposto:
    def __init__(self, principal: LeitorTexto, timeout_s: float) -> None:
        self.principal = principal
        self.timeout_s = timeout_s
        self.nome = principal.nome

    async def ler(self, imagem: Imagem) -> LeituraTexto:
        return await _chamar(lambda: self.principal.ler(imagem), self.timeout_s, None)
