"""Trabalhador do modo assíncrono: reserva tarefas da fila e roda a esteira.

- Processa até `TRABALHADOR_CONCORRENCIA` tarefas ao mesmo tempo (padrão: o número de
  instâncias do OCR), para a fila do OCR nunca estourar o tempo limite do provider.
- Carrega e aquece o leitor de texto antes de reservar a primeira tarefa.
- Escala horizontal: mais containers de trabalhador; o `SKIP LOCKED` evita disputa.
- SIGTERM/SIGINT: para de reservar e termina as tarefas em andamento. Se o processo morrer
  no meio, a reserva vence e outra instância retoma a tarefa.

Uso: `uv run trabalhador`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
from datetime import UTC, datetime, timedelta

from app.armazenamento.fila import utc
from app.config import Settings, obter_settings
from app.logs import configurar_logs, obter_logger
from app.servico import ServicoExtracao

log = obter_logger(__name__)


class Trabalhador:
    def __init__(self, servico: ServicoExtracao, nome: str | None = None) -> None:
        self.servico = servico
        self.settings: Settings = servico.settings
        self.nome = nome or f"{socket.gethostname()}-{os.getpid()}"

    async def uma(self, vaga: int = 0) -> bool:
        """Reserva e processa uma tarefa. Devolve False se a fila estava vazia."""
        fila, settings = self.servico.fila, self.settings
        tarefa = await asyncio.to_thread(
            fila.reservar,
            datetime.now(UTC),
            f"{self.nome}/{vaga}",
            timedelta(seconds=settings.fila_reserva_s),
        )
        if tarefa is None:
            return False
        if tarefa.tentativas > settings.fila_tentativas_max:
            # Reservada de novo depois de reservas vencidas: o processo caiu nela outras
            # vezes. Encerra com erro interno em vez de derrubar o próximo trabalhador.
            await self.servico._concluir_tarefa_com_erro(tarefa.id, "tentativas_esgotadas")
            return True
        inicio = datetime.now(UTC)
        try:
            desfecho = await self.servico.processar_tarefa(tarefa)
        except Exception as erro:
            espera = settings.fila_espera_inicial_s * 2 ** (tarefa.tentativas - 1)
            await asyncio.to_thread(
                fila.devolver,
                tarefa.id,
                datetime.now(UTC) + timedelta(seconds=min(espera, settings.fila_espera_max_s)),
            )
            log.error("tarefa_falhou", id_processamento=tarefa.id, erro=type(erro).__name__)
            return True
        log.info(
            "tarefa_processada",
            id_processamento=tarefa.id,
            desfecho=desfecho,
            tentativa=tarefa.tentativas,
            espera_fila_ms=round((inicio - utc(tarefa.criado_em)).total_seconds() * 1000, 1),
            processamento_ms=round((datetime.now(UTC) - inicio).total_seconds() * 1000, 1),
        )
        return True

    async def rodar(self, parar: asyncio.Event) -> None:
        concorrencia = self.settings.concorrencia_trabalhador
        log.info("trabalhador_iniciado", trabalhador=self.nome, concorrencia=concorrencia)

        async def laco(vaga: int) -> None:
            while not parar.is_set():
                try:
                    if await self.uma(vaga):
                        continue
                except Exception as erro:
                    # Banco ou armazenamento fora do ar: espera e tenta de novo; a vaga
                    # nunca morre (a tarefa reservada volta quando a reserva vencer).
                    log.error("fila_indisponivel", vaga=vaga, erro=type(erro).__name__)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(parar.wait(), timeout=self.settings.fila_intervalo_s)

        await asyncio.gather(*(laco(v) for v in range(concorrencia)))
        log.info("trabalhador_parado", trabalhador=self.nome)


def _montar(settings: Settings) -> ServicoExtracao:
    from app.armazenamento.db import criar_engine, criar_sessoes
    from app.armazenamento.objetos import Objetos
    from app.armazenamento.repositorio import Repositorio
    from app.montagem import criar_esteira

    repositorio = Repositorio(criar_sessoes(criar_engine(settings)))
    return ServicoExtracao(settings, criar_esteira(settings), repositorio, Objetos(settings))


async def _principal() -> None:
    settings = obter_settings()
    configurar_logs(settings.log_level, settings.log_json)
    servico = _montar(settings)
    aquecer = getattr(servico.esteira.deps.leitor_texto, "aquecer", None)
    if settings.leitor_texto_aquecer and callable(aquecer):
        await asyncio.to_thread(aquecer)
        log.info("leitor_texto_aquecido")
    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sinal, parar.set)
    await Trabalhador(servico).rodar(parar)


def main() -> None:
    asyncio.run(_principal())


if __name__ == "__main__":  # pragma: no cover
    main()
