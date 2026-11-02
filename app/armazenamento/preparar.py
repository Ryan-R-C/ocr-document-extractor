"""Prepara o ambiente: aplica as migrações do banco e configura o bucket.

Uso: `python -m app.armazenamento.preparar` (idempotente; roda no `make up`).
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from app.armazenamento.objetos import Objetos
from app.config import obter_settings
from app.logs import configurar_logs, obter_logger

RAIZ = Path(__file__).resolve().parents[2]


def migrar(database_url: str) -> None:
    config = Config(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def main() -> None:
    settings = obter_settings()
    configurar_logs(settings.log_level, settings.log_json)
    log = obter_logger(__name__)
    migrar(settings.database_url)
    log.info("migracoes_aplicadas")
    Objetos(settings).preparar_bucket()
    log.info("bucket_preparado", bucket=settings.s3_bucket)


if __name__ == "__main__":
    main()
