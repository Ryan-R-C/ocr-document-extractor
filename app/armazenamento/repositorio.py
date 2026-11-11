"""Operações de banco usadas pelo serviço, pelo cache e pela limpeza."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.armazenamento.modelos import CasoRevisao, ChamadaProvider, Processamento
from app.providers.interfaces import MetricasChamada


def chave_cache(sha256_imagem: str, tipo_documento: str, versao_pipeline: str) -> str:
    return hashlib.sha256(
        f"{sha256_imagem}|{tipo_documento}|{versao_pipeline}".encode()
    ).hexdigest()


class Repositorio:
    def __init__(self, sessoes: sessionmaker[Session]) -> None:
        self.sessoes = sessoes

    def registrar(
        self,
        processamento: Processamento,
        chamadas: Sequence[MetricasChamada] = (),
        caso: CasoRevisao | None = None,
    ) -> None:
        with self.sessoes.begin() as sessao:
            sessao.add(processamento)
            for c in chamadas:
                sessao.add(
                    ChamadaProvider(
                        processamento_id=processamento.id,
                        etapa=c.etapa,
                        provider=c.provider,
                        modelo=c.modelo,
                        modo=c.modo.value,
                        tokens_entrada=c.tokens_entrada,
                        tokens_saida=c.tokens_saida,
                        custo_usd=Decimal(str(c.custo_usd)),
                        custo_brl=Decimal(str(c.custo_brl)),
                        latencia_ms=Decimal(str(round(c.latencia_ms, 3))),
                        sucesso=int(c.sucesso),
                        erro=c.erro,
                        criado_em=processamento.recebido_em,
                    )
                )
            if caso is not None:
                sessao.add(caso)

    def buscar_cache(self, chave: str, agora: datetime) -> Processamento | None:
        with self.sessoes() as sessao:
            consulta = (
                select(Processamento)
                .where(Processamento.chave_cache == chave)
                .where(or_(Processamento.expira_em.is_(None), Processamento.expira_em > agora))
                .order_by(Processamento.recebido_em.desc())
                .limit(1)
            )
            return sessao.scalars(consulta).first()

    def obter(self, id_processamento: str) -> Processamento | None:
        with self.sessoes() as sessao:
            return sessao.get(Processamento, id_processamento)

    def processamentos_expirados(self, agora: datetime, limite: int = 500) -> list[Processamento]:
        with self.sessoes() as sessao:
            consulta = (
                select(Processamento)
                .where(Processamento.expira_em.is_not(None), Processamento.expira_em <= agora)
                .limit(limite)
            )
            return list(sessao.scalars(consulta))

    def casos_expirados(self, agora: datetime, limite: int = 500) -> list[CasoRevisao]:
        with self.sessoes() as sessao:
            consulta = (
                select(CasoRevisao)
                .where(CasoRevisao.expira_em.is_not(None), CasoRevisao.expira_em <= agora)
                .limit(limite)
            )
            return list(sessao.scalars(consulta))

    def pacote_em_uso(self, prefixo: str, exceto_id: str, agora: datetime) -> bool:
        """Outro processamento ainda válido aponta para o mesmo pacote (resposta vinda do cache)?"""
        with self.sessoes() as sessao:
            consulta = (
                select(Processamento.id)
                .where(Processamento.prefixo_pacote == prefixo, Processamento.id != exceto_id)
                .where(or_(Processamento.expira_em.is_(None), Processamento.expira_em > agora))
                .limit(1)
            )
            return sessao.scalars(consulta).first() is not None

    def apagar_processamento(self, id_processamento: str) -> None:
        with self.sessoes.begin() as sessao:
            objeto = sessao.get(Processamento, id_processamento)
            if objeto is not None:
                sessao.delete(objeto)

    def apagar_caso(self, id_caso: str) -> None:
        with self.sessoes.begin() as sessao:
            objeto = sessao.get(CasoRevisao, id_caso)
            if objeto is not None:
                sessao.delete(objeto)
