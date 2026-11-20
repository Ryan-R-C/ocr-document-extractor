"""Intervalos de confiança exatos para proporções binomiais, sem dependências externas.

Com gabarito pequeno, "0 erros escapados" não demonstra "erro escapado < 0,5%": o que
demonstra é o limite superior do intervalo de confiança ficar abaixo da meta. Aqui está o
intervalo exato de Clopper-Pearson, calculado invertendo a CDF binomial por bisseção.

- Bilateral (1 - α): [p_L, p_U] com P(X ≥ k | p_L) = α/2 e P(X ≤ k | p_U) = α/2.
- Limite superior unilateral (1 - α): P(X ≤ k | p) = α.
- k = 0 → p_L = 0; k = n → p_U = 1 (e o unilateral também é 1).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

CONFIANCA = 0.95
_TOLERANCIA = 1e-12
_MAX_ITERACOES = 200


def _log_pmf(i: int, n: int, p: float) -> float:
    return (
        math.lgamma(n + 1)
        - math.lgamma(i + 1)
        - math.lgamma(n - i + 1)
        + i * math.log(p)
        + (n - i) * math.log1p(-p)
    )


def cdf_binomial(k: int, n: int, p: float) -> float:
    """P(X ≤ k) para X ~ Binomial(n, p)."""
    if n < 0:
        raise ValueError("n deve ser ≥ 0")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p deve estar em [0, 1]")
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p == 0.0:
        return 1.0
    if p == 1.0:
        return 0.0
    # Soma pela cauda mais curta, em escala logarítmica, para não estourar com n grande.
    if k <= n // 2:
        return min(1.0, math.fsum(math.exp(_log_pmf(i, n, p)) for i in range(k + 1)))
    cauda = math.fsum(math.exp(_log_pmf(i, n, p)) for i in range(k + 1, n + 1))
    return max(0.0, 1.0 - cauda)


def _inverter(f: Callable[[float], float], alvo: float, crescente: bool) -> float:
    """p em [0, 1] tal que f(p) = alvo, para f monótona em p, por bisseção."""
    lo, hi = 0.0, 1.0
    for _ in range(_MAX_ITERACOES):
        meio = (lo + hi) / 2
        if (f(meio) < alvo) == crescente:
            lo = meio
        else:
            hi = meio
        if hi - lo < _TOLERANCIA:
            break
    return (lo + hi) / 2


def _validar(k: int, n: int, confianca: float) -> None:
    if n < 0 or not 0 <= k <= n:
        raise ValueError("exige 0 ≤ k ≤ n")
    if not 0.0 < confianca < 1.0:
        raise ValueError("confiança deve estar em (0, 1)")


def clopper_pearson(k: int, n: int, confianca: float = 0.95) -> tuple[float, float]:
    """Intervalo exato bilateral de Clopper-Pearson para k sucessos em n."""
    _validar(k, n, confianca)
    if n == 0:
        return 0.0, 1.0
    alfa = 1.0 - confianca
    # P(X ≥ k | p) cresce com p; P(X ≤ k | p) decresce com p.
    inferior = (
        0.0 if k == 0 else _inverter(lambda p: 1.0 - cdf_binomial(k - 1, n, p), alfa / 2, True)
    )
    superior = 1.0 if k == n else _inverter(lambda p: cdf_binomial(k, n, p), alfa / 2, False)
    return inferior, superior


def limite_superior(k: int, n: int, confianca: float = 0.95) -> float:
    """Limite superior unilateral exato (Clopper-Pearson) de nível `confianca`."""
    _validar(k, n, confianca)
    if n == 0 or k == n:
        return 1.0
    if k == 0:  # forma fechada: (1 - p)^n = α
        return 1.0 - math.pow(1.0 - confianca, 1.0 / n)
    return _inverter(lambda p: cdf_binomial(k, n, p), 1.0 - confianca, False)


def n_minimo_para_meta(meta: float = 0.005, confianca: float = 0.95) -> int:
    """Menor n de casos sem nenhum erro cujo limite superior unilateral fica abaixo da meta.

    Para k = 0 o limite é 1 - α^(1/n), então n > ln(α) / ln(1 - meta) (≈ 3/meta, a "regra de
    três"). O laço final corrige qualquer arredondamento da forma fechada.
    """
    if not 0.0 < meta < 1.0:
        raise ValueError("meta deve estar em (0, 1)")
    if not 0.0 < confianca < 1.0:
        raise ValueError("confiança deve estar em (0, 1)")
    n = max(1, math.ceil(math.log(1.0 - confianca) / math.log1p(-meta)))
    while n > 1 and limite_superior(0, n - 1, confianca) < meta:
        n -= 1
    while limite_superior(0, n, confianca) >= meta:
        n += 1
    return n


DEMONSTRADA = "demonstrada"
NAO_DEMONSTRADA = "não demonstrada por amostra pequena"
VIOLADA = "violada"


@dataclass(frozen=True)
class AvaliacaoMeta:
    """Uma taxa k/n confrontada com uma meta "taxa < meta" pelo limite superior unilateral."""

    eventos: int
    total: int
    taxa: float | None
    ic95: tuple[float, float]
    limite_superior_95: float
    meta: float
    status: str
    n_minimo_sem_erro: int

    def como_dict(self) -> dict[str, object]:
        return {
            "eventos": self.eventos,
            "total": self.total,
            "taxa": self.taxa,
            "ic95_inferior": self.ic95[0],
            "ic95_superior": self.ic95[1],
            "limite_superior_95": self.limite_superior_95,
            "meta": self.meta,
            "status": self.status,
            "n_minimo_sem_erro": self.n_minimo_sem_erro,
        }


def avaliar_meta(
    eventos: int,
    total: int,
    meta: float,
    tolerancia_zero: bool = False,
) -> AvaliacaoMeta:
    """Status da meta a 95%: demonstrada, não demonstrada por amostra pequena ou violada.

    - violada: taxa observada ≥ meta; com `tolerancia_zero` (alucinação), qualquer evento;
    - demonstrada: limite superior unilateral < meta;
    - não demonstrada por amostra pequena: o resto (inclusive n = 0).
    """
    taxa = eventos / total if total else None
    superior = limite_superior(eventos, total, CONFIANCA)
    if (tolerancia_zero and eventos > 0) or (taxa is not None and taxa >= meta):
        status = VIOLADA
    elif superior < meta:
        status = DEMONSTRADA
    else:
        status = NAO_DEMONSTRADA
    return AvaliacaoMeta(
        eventos=eventos,
        total=total,
        taxa=taxa,
        ic95=clopper_pearson(eventos, total, CONFIANCA),
        limite_superior_95=superior,
        meta=meta,
        status=status,
        n_minimo_sem_erro=n_minimo_para_meta(meta, CONFIANCA),
    )
