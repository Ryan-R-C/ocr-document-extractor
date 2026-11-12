"""Fila do modo assíncrono no próprio Postgres (`SELECT … FOR UPDATE SKIP LOCKED`).

- Cada trabalhador reserva uma tarefa por vez: PENDENTE disponível, ou PROCESSANDO com a
  reserva vencida (o trabalhador anterior caiu). A reserva vale `FILA_RESERVA_S`.
- Erro transitório do provider devolve a tarefa à fila com espera crescente, até
  `FILA_TENTATIVAS_MAX`; depois disso o resultado de erro é o final.
- Concluída, a tarefa guarda só o código HTTP (e, para erro da própria fila, o corpo de
  erro, sem dado pessoal). O resultado fica no pacote de auditoria.

No SQLite dos testes o `SKIP LOCKED` não existe e é ignorado (um processo só).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.armazenamento.modelos import TarefaExtracao

PENDENTE = "PENDENTE"
PROCESSANDO = "PROCESSANDO"
CONCLUIDA = "CONCLUIDA"


def utc(momento: datetime) -> datetime:
    """Datas do banco em UTC (o SQLite dos testes devolve sem fuso; o Postgres, com)."""
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


class Fila:
    def __init__(self, sessoes: sessionmaker[Session]) -> None:
        self.sessoes = sessoes

    def enfileirar(self, tarefa: TarefaExtracao) -> None:
        with self.sessoes.begin() as sessao:
            sessao.add(tarefa)

    def reservar(
        self, agora: datetime, trabalhador: str, reserva: timedelta
    ) -> TarefaExtracao | None:
        with self.sessoes.begin() as sessao:
            consulta = (
                select(TarefaExtracao)
                .where(
                    or_(
                        and_(
                            TarefaExtracao.situacao == PENDENTE,
                            TarefaExtracao.disponivel_em <= agora,
                        ),
                        and_(
                            TarefaExtracao.situacao == PROCESSANDO,
                            TarefaExtracao.prazo_em <= agora,
                        ),
                    )
                )
                .order_by(TarefaExtracao.disponivel_em, TarefaExtracao.criado_em)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            tarefa = sessao.scalars(consulta).first()
            if tarefa is None:
                return None
            tarefa.situacao = PROCESSANDO
            tarefa.tentativas += 1
            tarefa.iniciado_em = agora
            tarefa.prazo_em = agora + reserva
            tarefa.trabalhador = trabalhador
            return tarefa

    def devolver(self, id_tarefa: str, disponivel_em: datetime) -> None:
        """Erro transitório: volta para a fila, disponível depois da espera."""
        with self.sessoes.begin() as sessao:
            tarefa = sessao.get(TarefaExtracao, id_tarefa)
            if tarefa is not None:
                tarefa.situacao = PENDENTE
                tarefa.disponivel_em = disponivel_em
                tarefa.prazo_em = None
                tarefa.trabalhador = None

    def concluir(
        self, id_tarefa: str, agora: datetime, http_status: int, corpo_erro: str | None = None
    ) -> None:
        with self.sessoes.begin() as sessao:
            tarefa = sessao.get(TarefaExtracao, id_tarefa)
            if tarefa is not None:
                tarefa.situacao = CONCLUIDA
                tarefa.concluido_em = agora
                tarefa.prazo_em = None
                tarefa.http_status = http_status
                tarefa.corpo_erro = corpo_erro

    def obter(self, id_tarefa: str) -> TarefaExtracao | None:
        with self.sessoes() as sessao:
            return sessao.get(TarefaExtracao, id_tarefa)

    def posicao(self, tarefa: TarefaExtracao) -> int:
        """Quantas pendentes estão na frente (1 = é a próxima)."""
        with self.sessoes() as sessao:
            na_frente = sessao.scalar(
                select(func.count())
                .select_from(TarefaExtracao)
                .where(
                    TarefaExtracao.situacao == PENDENTE,
                    TarefaExtracao.criado_em < tarefa.criado_em,
                )
            )
            return int(na_frente or 0) + 1

    def pendentes(self) -> int:
        with self.sessoes() as sessao:
            total = sessao.scalar(
                select(func.count())
                .select_from(TarefaExtracao)
                .where(TarefaExtracao.situacao.in_([PENDENTE, PROCESSANDO]))
            )
            return int(total or 0)

    def expiradas(self, agora: datetime, limite: int = 500) -> list[TarefaExtracao]:
        with self.sessoes() as sessao:
            consulta = (
                select(TarefaExtracao)
                .where(TarefaExtracao.expira_em.is_not(None), TarefaExtracao.expira_em <= agora)
                .limit(limite)
            )
            return list(sessao.scalars(consulta))

    def apagar(self, id_tarefa: str) -> None:
        with self.sessoes.begin() as sessao:
            tarefa = sessao.get(TarefaExtracao, id_tarefa)
            if tarefa is not None:
                sessao.delete(tarefa)
