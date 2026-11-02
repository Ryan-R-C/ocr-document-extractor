"""Conexão com o banco (SQLAlchemy 2, síncrono; chamado de threads pela API)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def criar_engine(settings: Settings) -> Engine:
    url = settings.database_url
    if url.startswith("sqlite"):
        # Testes: SQLite em memória compartilhado entre threads.
        from sqlalchemy.pool import StaticPool

        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(
        url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
    )


def criar_sessoes(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def banco_ok(engine: Engine) -> bool:
    try:
        with engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
