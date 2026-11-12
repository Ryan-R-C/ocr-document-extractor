"""Regras de retenção: prefixos por TTL e cálculo de `expira_em`.

Quem garante 8 h / 16 h é o job de limpeza, que confere `expira_em` no banco. O lifecycle
do S3 trabalha em dias e serve só como rede de segurança (1 dia nos prefixos com TTL).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.config import Settings

PREFIXO_REVISAO = "revisao"
# Imagens aguardando o trabalhador (modo assíncrono); apagadas ao concluir a tarefa.
PREFIXO_FILA = "fila"


def prefixo_artefatos(ttl: timedelta | None) -> str:
    if ttl is None:
        return "sem-expiracao"
    horas = int(ttl.total_seconds() // 3600)
    return f"ttl-{horas}h"


def prefixo_pacote(settings: Settings, id_processamento: str) -> str:
    return f"{prefixo_artefatos(settings.ttl_artefatos)}/{id_processamento}"


def prefixo_caso_revisao(id_caso: str) -> str:
    return f"{PREFIXO_REVISAO}/{id_caso}"


def chave_imagem_fila(id_processamento: str, extensao: str) -> str:
    return f"{PREFIXO_FILA}/{id_processamento}/imagem_recebida.{extensao}"


def expira_em(inicio: datetime, ttl: timedelta | None) -> datetime | None:
    return None if ttl is None else inicio + ttl


def regras_lifecycle(settings: Settings) -> list[dict[str, object]]:
    """Rede de segurança no bucket. Nunca substitui o job de limpeza."""
    regras: list[dict[str, object]] = []
    for ttl in (timedelta(hours=8), timedelta(hours=16)):
        regras.append(
            {
                "ID": f"seguranca-{prefixo_artefatos(ttl)}",
                "Filter": {"Prefix": f"{prefixo_artefatos(ttl)}/"},
                "Status": "Enabled",
                "Expiration": {"Days": 1},
            }
        )
    regras.append(
        {
            "ID": "seguranca-fila",
            "Filter": {"Prefix": f"{PREFIXO_FILA}/"},
            "Status": "Enabled",
            "Expiration": {"Days": 2},
        }
    )
    ttl_revisao = settings.ttl_casos_revisao
    if ttl_revisao is not None:
        dias = max(1, math.ceil(ttl_revisao.total_seconds() / 86400)) + 1
        regras.append(
            {
                "ID": "seguranca-revisao",
                "Filter": {"Prefix": f"{PREFIXO_REVISAO}/"},
                "Status": "Enabled",
                "Expiration": {"Days": dias},
            }
        )
    return regras
