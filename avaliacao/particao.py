"""Partição holdout fixa, determinística e estratificada.

- Estrato: subtipo × UF × qualidade.
- Dentro de cada estrato, os itens ainda sem partição são ordenados pelo sha256 do arquivo
  e os primeiros vão para o holdout, na quantidade calculada para o estrato.
- Partição atribuída nunca muda: casos novos (semanais) são particionados entre si, sem
  mover os antigos de lado.
- Nada é ajustado no holdout; ele só fornece o número final de cada relatório.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict

from avaliacao.gabarito import ItemGabarito

FRACAO_HOLDOUT = 0.2


def _chave_estrato(estrato: tuple[str, str, str]) -> str:
    return hashlib.sha256("|".join(estrato).encode()).hexdigest()


def atribuir_particoes(
    itens: list[ItemGabarito], fracao: float = FRACAO_HOLDOUT
) -> list[ItemGabarito]:
    """Cada estrato recebe a parte inteira de `fracao × total` no holdout; as vagas que
    sobram (para chegar a `fracao` do gabarito todo) vão aos estratos com maior resto
    fracionário, com desempate pelo hash do estrato. Assim estratos pequenos também
    contribuem para o holdout, e a escolha continua determinística."""
    if not 0 < fracao < 1:
        raise ValueError("fração do holdout precisa estar entre 0 e 1")
    por_estrato: dict[tuple[str, str, str], list[ItemGabarito]] = defaultdict(list)
    for item in itens:
        por_estrato[item.estrato].append(item)

    vagas: dict[tuple[str, str, str], int] = {}
    restos: list[tuple[float, str, tuple[str, str, str]]] = []
    for estrato, grupo in por_estrato.items():
        novos = sum(1 for i in grupo if i.particao is None)
        deficit = len(grupo) * fracao - sum(1 for i in grupo if i.particao == "holdout")
        inteiro = max(0, min(novos, math.floor(deficit)))
        vagas[estrato] = inteiro
        if novos > inteiro and deficit - inteiro > 0:
            restos.append((deficit - inteiro, _chave_estrato(estrato), estrato))

    alvo_global = int(len(itens) * fracao + 0.5)
    ja_holdout = sum(1 for i in itens if i.particao == "holdout")
    sobra = alvo_global - ja_holdout - sum(vagas.values())
    for _, _, estrato in sorted(restos, key=lambda r: (-r[0], r[1]))[: max(0, sobra)]:
        vagas[estrato] += 1

    atribuicao: dict[int, str] = {}
    for estrato, grupo in por_estrato.items():
        ordenados = sorted(
            (i for i in grupo if i.particao is None), key=lambda i: (i.sha256, i.arquivo)
        )
        for posicao, item in enumerate(ordenados):
            atribuicao[id(item)] = "holdout" if posicao < vagas[estrato] else "ajuste"

    return [
        item
        if item.particao is not None
        else item.model_copy(update={"particao": atribuicao[id(item)]})
        for item in itens
    ]
