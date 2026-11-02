"""Prompts versionados. O hash do conteúdo entra em `versao_pipeline`."""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def carregar(nome: str) -> str:
    return (_DIR / f"{nome}.md").read_text(encoding="utf-8")


def hash_prompts() -> str:
    """Hash curto e estável de todos os prompts, na ordem do nome do arquivo."""
    h = hashlib.sha256()
    for caminho in sorted(_DIR.glob("*.md")):
        h.update(caminho.name.encode())
        h.update(caminho.read_bytes())
    return h.hexdigest()[:8]
