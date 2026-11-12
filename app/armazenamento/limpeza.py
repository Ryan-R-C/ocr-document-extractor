"""Job de limpeza: garante o TTL de 8 h / 16 h conferindo `expira_em` no banco.

Para cada registro vencido (processamento, caso em revisão ou tarefa da fila do modo
assíncrono), apaga primeiro os objetos e depois o metadado. Se apagar os
objetos falhar, o registro fica para a próxima rodada (idempotente).

Uso: `limpeza` (uma rodada) ou `limpeza --loop` (a cada LIMPEZA_INTERVALO_S).
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from app.armazenamento.db import criar_engine, criar_sessoes
from app.armazenamento.fila import Fila
from app.armazenamento.objetos import Objetos
from app.armazenamento.repositorio import Repositorio
from app.armazenamento.retencao import PREFIXO_FILA
from app.config import obter_settings
from app.logs import configurar_logs, obter_logger

log = obter_logger(__name__)


@dataclass
class RelatorioLimpeza:
    processamentos: int = 0
    casos: int = 0
    tarefas: int = 0
    objetos: int = 0
    falhas: int = 0


def executar_limpeza(
    repositorio: Repositorio, objetos: Objetos, agora: datetime | None = None
) -> RelatorioLimpeza:
    agora = agora or datetime.now(UTC)
    relatorio = RelatorioLimpeza()

    for proc in repositorio.processamentos_expirados(agora):
        try:
            if proc.prefixo_pacote and not repositorio.pacote_em_uso(
                proc.prefixo_pacote, proc.id, agora
            ):
                relatorio.objetos += objetos.apagar_prefixo(proc.prefixo_pacote)
            repositorio.apagar_processamento(proc.id)
            relatorio.processamentos += 1
        except Exception as erro:
            relatorio.falhas += 1
            log.error("limpeza_falhou", id_processamento=proc.id, erro=type(erro).__name__)

    for caso in repositorio.casos_expirados(agora):
        try:
            relatorio.objetos += objetos.apagar_prefixo(caso.prefixo_pacote)
            repositorio.apagar_caso(caso.id)
            relatorio.casos += 1
        except Exception as erro:
            relatorio.falhas += 1
            log.error("limpeza_falhou", id_caso=caso.id, erro=type(erro).__name__)

    fila = Fila(repositorio.sessoes)
    for tarefa in fila.expiradas(agora):
        try:
            relatorio.objetos += objetos.apagar_prefixo(f"{PREFIXO_FILA}/{tarefa.id}")
            fila.apagar(tarefa.id)
            relatorio.tarefas += 1
        except Exception as erro:
            relatorio.falhas += 1
            log.error("limpeza_falhou", id_processamento=tarefa.id, erro=type(erro).__name__)

    log.info("limpeza_concluida", **relatorio.__dict__)
    return relatorio


def main() -> None:
    parser = argparse.ArgumentParser(description="Apaga pacotes e metadados vencidos.")
    parser.add_argument("--loop", action="store_true", help="roda continuamente")
    args = parser.parse_args()

    settings = obter_settings()
    configurar_logs(settings.log_level, settings.log_json)
    repositorio = Repositorio(criar_sessoes(criar_engine(settings)))
    objetos = Objetos(settings)
    while True:
        executar_limpeza(repositorio, objetos)
        if not args.loop:
            break
        time.sleep(settings.limpeza_intervalo_s)


if __name__ == "__main__":
    main()
