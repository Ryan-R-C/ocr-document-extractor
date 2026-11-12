"""Tabelas de metadados. Nenhuma coluna guarda valor de campo do documento.

- `processamentos`: um por requisição; apagado pelo job de limpeza quando `expira_em` passa.
- `casos_revisao`: registro próprio dos casos em REVISAO, com TTL próprio.
- `chamadas_provider`: custo por chamada; sem dado pessoal, sobrevive à expiração do
  processamento para o histórico de custo (por isso sem chave estrangeira).
- `tarefas_extracao`: fila do modo assíncrono. A imagem fica no armazenamento de objetos
  (criptografada); o resultado, no pacote de auditoria. Aqui só ficam situação e prazos.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Processamento(Base):
    __tablename__ = "processamentos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id_externo: Mapped[str | None] = mapped_column(String(200), index=True)
    sha256_imagem: Mapped[str | None] = mapped_column(String(64), index=True)
    tipo_documento: Mapped[str] = mapped_column(String(40))
    versao_pipeline: Mapped[str] = mapped_column(String(300))
    # APROVADO | REVISAO | RECUSADO (422) | REQUISICAO_INVALIDA (400/413) | ERRO_PROVIDER (5xx)
    status: Mapped[str] = mapped_column(String(30), index=True)
    http_status: Mapped[int] = mapped_column(Integer)
    # sha256 + tipo + versao_pipeline; preenchida só quando o resultado pode ser reaproveitado.
    chave_cache: Mapped[str | None] = mapped_column(String(64))
    origem_cache_id: Mapped[str | None] = mapped_column(String(36))
    prefixo_pacote: Mapped[str | None] = mapped_column(String(200))
    custo_brl: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))
    recebido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (Index("ix_processamentos_cache", "chave_cache", "expira_em"),)


class CasoRevisao(Base):
    __tablename__ = "casos_revisao"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    processamento_id: Mapped[str] = mapped_column(String(36), index=True)
    sha256_imagem: Mapped[str] = mapped_column(String(64))
    tipo_documento: Mapped[str] = mapped_column(String(40))
    subtipo: Mapped[str | None] = mapped_column(String(40))
    uf: Mapped[str | None] = mapped_column(String(20))
    versao_pipeline: Mapped[str] = mapped_column(String(300))
    prefixo_pacote: Mapped[str] = mapped_column(String(200))
    # PENDENTE | CONFERIDO (quando conferido e corrigido, entra no gabarito)
    situacao: Mapped[str] = mapped_column(String(20), default="PENDENTE")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    conferido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ChamadaProvider(Base):
    __tablename__ = "chamadas_provider"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processamento_id: Mapped[str] = mapped_column(String(36), index=True)
    etapa: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    modelo: Mapped[str] = mapped_column(String(80))
    modo: Mapped[str] = mapped_column(String(10))
    tokens_entrada: Mapped[int] = mapped_column(Integer)
    tokens_saida: Mapped[int] = mapped_column(Integer)
    custo_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    custo_brl: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    latencia_ms: Mapped[float] = mapped_column(Numeric(12, 3))
    sucesso: Mapped[int] = mapped_column(Integer)
    erro: Mapped[str | None] = mapped_column(String(200))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TarefaExtracao(Base):
    __tablename__ = "tarefas_extracao"

    # Mesmo id do processamento: a consulta usa um id só.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id_externo: Mapped[str | None] = mapped_column(String(200))
    tipo_documento: Mapped[str] = mapped_column(String(40))
    sha256_imagem: Mapped[str] = mapped_column(String(64))
    formato: Mapped[str] = mapped_column(String(10))
    chave_imagem: Mapped[str] = mapped_column(String(200))
    # PENDENTE | PROCESSANDO | CONCLUIDA
    situacao: Mapped[str] = mapped_column(String(15))
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    # A partir de quando pode ser reservada (espera crescente entre tentativas).
    disponivel_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Fim da reserva: se o trabalhador cair, a tarefa volta para a fila depois disso.
    prazo_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trabalhador: Mapped[str | None] = mapped_column(String(100))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    # Só corpo de erro gerado pela fila (sem dado pessoal); o resultado fica no pacote.
    corpo_erro: Mapped[str | None] = mapped_column(Text)
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (Index("ix_tarefas_fila", "situacao", "disponivel_em", "criado_em"),)
