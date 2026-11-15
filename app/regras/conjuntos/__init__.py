"""Conjunto de regras vigente: a ordem não altera o resultado (a agregação é por gravidade)."""

from __future__ import annotations

from app.regras.base import Regra
from app.regras.conjuntos import cin, cnh, comuns, ctps, rg

REGRAS: tuple[Regra, ...] = (
    *comuns.REGRAS,
    *rg.REGRAS,
    *cin.REGRAS,
    *cnh.REGRAS,
    *ctps.REGRAS,
)
