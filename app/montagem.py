"""Raiz de composição: junta configuração, providers, esteira e armazenamento."""

from __future__ import annotations

from app.config import Settings
from app.core.pipeline import Esteira
from app.providers.fabrica import criar_dependencias


def criar_esteira(settings: Settings) -> Esteira:
    return Esteira(settings, criar_dependencias(settings))
