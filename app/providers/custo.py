"""Custo por chamada a partir dos preços configurados."""

from __future__ import annotations

from app.config import Settings
from app.providers.interfaces import ModoChamada


class PrecoDesconhecido(KeyError):
    pass


def calcular_custo(
    settings: Settings,
    modelo: str,
    tokens_entrada: int,
    tokens_saida: int,
    modo: ModoChamada = ModoChamada.PADRAO,
) -> tuple[float, float]:
    """Devolve (US$, R$). Modelo sem preço configurado é erro: custo escondido é pior."""
    if tokens_entrada == 0 and tokens_saida == 0:
        return 0.0, 0.0
    preco = settings.precos_modelos.get(modelo)
    if preco is None:
        raise PrecoDesconhecido(f"modelo sem preço em PRECOS_MODELOS: {modelo}")
    fator = {
        ModoChamada.PADRAO: 1.0,
        ModoChamada.BATCH: preco.fator_batch,
        ModoChamada.FLEX: preco.fator_flex,
    }[modo]
    usd = (tokens_entrada * preco.entrada + tokens_saida * preco.saida) / 1_000_000 * fator
    return usd, usd * settings.cambio_usd_brl
