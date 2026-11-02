"""Controle de concorrência e de tokens por minuto por provider.

Tokens de imagem contam no limite de tokens por minuto, o gargalo provável nos picos.
Reserva-se uma estimativa antes da chamada e acerta-se a diferença depois.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from app.config import LimiteProvider


class BaldeTokens:
    """Balde de tokens com reposição contínua (capacidade = tokens por minuto)."""

    def __init__(self, por_minuto: int, relogio: Callable[[], float] = time.monotonic) -> None:
        self.capacidade = float(por_minuto)
        self.disponivel = float(por_minuto)
        self._taxa = por_minuto / 60.0
        self._relogio = relogio
        self._ultimo = relogio()
        self._trava = asyncio.Lock()

    def _repor(self) -> None:
        agora = self._relogio()
        self.disponivel = min(
            self.capacidade, self.disponivel + (agora - self._ultimo) * self._taxa
        )
        self._ultimo = agora

    async def reservar(self, tokens: int) -> None:
        pedido = min(float(tokens), self.capacidade)
        async with self._trava:
            while True:
                self._repor()
                if self.disponivel >= pedido:
                    self.disponivel -= pedido
                    return
                await asyncio.sleep((pedido - self.disponivel) / self._taxa)

    def acertar(self, reservado: int, usado: int) -> None:
        """Devolve a sobra (ou cobra o excesso) da estimativa."""
        self._repor()
        self.disponivel = min(self.capacidade, self.disponivel + (reservado - usado))


class LimitadorProvider:
    def __init__(self, limite: LimiteProvider) -> None:
        self.limite = limite
        self._semaforo = asyncio.Semaphore(limite.concorrencia)
        self.balde = BaldeTokens(limite.tokens_por_minuto)

    @asynccontextmanager
    async def chamada(self) -> AsyncIterator[Callable[[int], None]]:
        """Uso: `async with limitador.chamada() as registrar: ...; registrar(tokens_usados)`."""
        estimativa = self.limite.tokens_estimados_por_chamada
        usado: list[int] = []
        async with self._semaforo:
            await self.balde.reservar(estimativa)
            try:
                yield usado.append
            finally:
                self.balde.acertar(estimativa, sum(usado) if usado else estimativa)
