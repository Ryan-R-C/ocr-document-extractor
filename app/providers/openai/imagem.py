"""Regras de imagem do provider: patches e tokens estimados, sem chamada de rede."""

from __future__ import annotations

import math

from app.config import RegraImagemModelo


def patches(largura: int, altura: int, regra: RegraImagemModelo) -> int:
    return math.ceil(largura / regra.patch_px) * math.ceil(altura / regra.patch_px)


def estimar_tokens_imagem(largura: int, altura: int, regra: RegraImagemModelo) -> int:
    """Estimativa do plano: patches × multiplicador do modelo (1.600 × 1,2 = 1.920)."""
    return round(patches(largura, altura, regra) * regra.multiplicador)
