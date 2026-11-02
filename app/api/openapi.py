"""Exporta o OpenAPI do contrato para `docs/openapi.json`.

Uso: `python -m app.api.openapi` (ou `make openapi`). O teste de contrato falha se o
arquivo versionado divergir do gerado.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from app.config import Settings
from app.main import criar_app
from app.servico import ServicoExtracao

ARQUIVO = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def gerar() -> dict[str, Any]:
    # O documento só depende das rotas e dos modelos: nenhum provider ou banco é montado.
    settings = Settings(_env_file=None)
    app = criar_app(settings=settings, servico=cast(ServicoExtracao, object()))
    return app.openapi()


def serializar(documento: dict[str, Any]) -> str:
    return json.dumps(documento, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    ARQUIVO.write_text(serializar(gerar()), encoding="utf-8")
    print(f"OpenAPI exportado para {ARQUIVO}")


if __name__ == "__main__":
    main()
