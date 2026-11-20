"""Intervalos de Clopper-Pearson, status das metas e as seções novas do relatório.

Os valores de referência são conferidos de forma independente: CDF binomial exata com
`math.comb` e `Fraction` (sem lgamma), formas fechadas de k = 0 e k = n e valores publicados.
"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from avaliacao import confianca, estatistica, relatorio_confianca
from avaliacao.metricas import META_ERRO_ESCAPADO, RegistroCampo, Registros


def _cdf_exata(k: int, n: int, p: float) -> float:
    """P(X ≤ k) em aritmética racional exata, independente da implementação."""
    q = Fraction(p)
    return float(sum(math.comb(n, i) * q**i * (1 - q) ** (n - i) for i in range(k + 1)))


# --- CDF ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("k", "n", "p"), [(0, 10, 0.1), (3, 10, 0.3), (7, 10, 0.5), (2, 50, 0.01), (40, 50, 0.9)]
)
def test_cdf_binomial_igual_a_exata(k: int, n: int, p: float) -> None:
    assert estatistica.cdf_binomial(k, n, p) == pytest.approx(_cdf_exata(k, n, p), abs=1e-12)


def test_cdf_binomial_bordas() -> None:
    assert estatistica.cdf_binomial(-1, 5, 0.3) == 0.0
    assert estatistica.cdf_binomial(5, 5, 0.3) == 1.0
    assert estatistica.cdf_binomial(2, 5, 0.0) == 1.0
    assert estatistica.cdf_binomial(2, 5, 1.0) == 0.0


# --- Clopper-Pearson ---------------------------------------------------------------------


def test_k_zero_n_dez() -> None:
    inferior, superior = estatistica.clopper_pearson(0, 10)
    assert inferior == 0.0
    assert superior == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-9)
    assert superior == pytest.approx(0.3085, abs=1e-4)


def test_k_igual_a_n() -> None:
    inferior, superior = estatistica.clopper_pearson(10, 10)
    assert superior == 1.0
    assert inferior == pytest.approx(0.025 ** (1 / 10), abs=1e-9)  # 0,6915
    assert estatistica.limite_superior(10, 10) == 1.0


@pytest.mark.parametrize(
    ("k", "n", "inferior", "superior"),
    [
        (1, 10, 0.0025, 0.4450),  # valores publicados (tabelas de Clopper-Pearson)
        (5, 10, 0.1871, 0.8129),
    ],
)
def test_valores_publicados(k: int, n: int, inferior: float, superior: float) -> None:
    lo, hi = estatistica.clopper_pearson(k, n)
    assert lo == pytest.approx(inferior, abs=1e-4)
    assert hi == pytest.approx(superior, abs=1e-4)


@pytest.mark.parametrize(("k", "n"), [(1, 10), (3, 40), (2, 200), (17, 60), (59, 60)])
def test_limites_satisfazem_a_definicao(k: int, n: int) -> None:
    """Conferência independente: na borda, a cauda exata vale α/2 (ou α no unilateral)."""
    lo, hi = estatistica.clopper_pearson(k, n)
    assert 1 - _cdf_exata(k - 1, n, lo) == pytest.approx(0.025, abs=1e-9)
    assert _cdf_exata(k, n, hi) == pytest.approx(0.025, abs=1e-9)
    unilateral = estatistica.limite_superior(k, n)
    assert _cdf_exata(k, n, unilateral) == pytest.approx(0.05, abs=1e-9)
    assert lo < k / n < unilateral < hi


def test_limite_superior_unilateral_k_zero() -> None:
    assert estatistica.limite_superior(0, 10) == pytest.approx(1 - 0.05 ** (1 / 10), abs=1e-12)
    assert estatistica.limite_superior(0, 10) == pytest.approx(0.2589, abs=1e-4)


def test_n_zero_e_entradas_invalidas() -> None:
    assert estatistica.clopper_pearson(0, 0) == (0.0, 1.0)
    assert estatistica.limite_superior(0, 0) == 1.0
    with pytest.raises(ValueError):
        estatistica.clopper_pearson(3, 2)
    with pytest.raises(ValueError):
        estatistica.limite_superior(1, 5, confianca=1.0)


def test_n_grande_sem_estouro() -> None:
    lo, hi = estatistica.clopper_pearson(20, 100_000)
    assert lo < 20 / 100_000 < hi < 0.001


# --- n mínimo ----------------------------------------------------------------------------


def test_n_minimo_para_meta() -> None:
    n = estatistica.n_minimo_para_meta(0.005, 0.95)
    assert n == 598  # ln(0,05) / ln(0,995) = 597,6; regra de três: 3 / 0,005 = 600
    assert n == math.ceil(math.log(0.05) / math.log(0.995))
    assert estatistica.limite_superior(0, n) < 0.005 <= estatistica.limite_superior(0, n - 1)
    assert estatistica.n_minimo_para_meta(0.01) == 299


# --- status da meta ----------------------------------------------------------------------


def test_status_da_meta() -> None:
    assert estatistica.avaliar_meta(0, 598, 0.005).status == estatistica.DEMONSTRADA
    assert estatistica.avaliar_meta(0, 597, 0.005).status == estatistica.NAO_DEMONSTRADA
    assert estatistica.avaliar_meta(0, 0, 0.005).status == estatistica.NAO_DEMONSTRADA
    assert estatistica.avaliar_meta(1, 900, 0.005).status == estatistica.NAO_DEMONSTRADA
    assert estatistica.avaliar_meta(1, 1000, 0.005).status == estatistica.DEMONSTRADA  # 0,474%
    assert estatistica.avaliar_meta(5, 1000, 0.005).status == estatistica.VIOLADA
    assert estatistica.avaliar_meta(1, 2000, 0.005).status == estatistica.DEMONSTRADA
    # Alucinação: meta zero, qualquer evento viola.
    assert (
        estatistica.avaliar_meta(1, 2000, 0.005, tolerancia_zero=True).status == estatistica.VIOLADA
    )
    r = estatistica.avaliar_meta(0, 10, 0.005).como_dict()
    assert r["ic95_superior"] == pytest.approx(0.3085, abs=1e-4)
    assert r["n_minimo_sem_erro"] == 598


# --- aplicação aos registros ---------------------------------------------------------------


def _campo(
    *,
    campo: str = "cpf",
    critico: bool = True,
    subtipo: str = "CNH",
    uf: str = "SP",
    qualidade: str = "boa",
    aprovado: bool = True,
    correto: bool | None = True,
    esperado: str = "LEGIVEL",
    devolveu: bool = True,
    status_vlm: str = "LEGIVEL",
    correto_vlm: bool | None = True,
    variante: bool = False,
) -> RegistroCampo:
    return RegistroCampo(
        arquivo="x",
        campo=campo,
        critico=critico,
        subtipo=subtipo,
        uf=uf,
        qualidade=qualidade,
        variante=variante,
        campo_apagado=variante,
        esperado_status=esperado,
        doc_aprovado=aprovado,
        devolveu_valor=devolveu,
        correto=correto,
        id="abc",
        status_leitura="LEGIVEL",
        status_vlm=status_vlm,
        vlm_devolveu_valor=devolveu,
        correto_vlm=correto_vlm,
    )


def _registros() -> Registros:
    campos = [_campo() for _ in range(8)]
    # Erro que a esteira segurou (revisão) e a medição teria aprovado.
    campos.append(_campo(aprovado=False, correto=False, correto_vlm=False, uf="RJ"))
    # Alucinação que só a medição teria aprovado.
    campos.append(
        _campo(aprovado=False, esperado="AUSENTE", correto=None, correto_vlm=None, uf="RJ")
    )
    campos.append(_campo(critico=False, campo="naturalidade"))
    # Variante de campo apagado: alucinação aprovada pela esteira.
    campos.append(_campo(esperado="AUSENTE", correto=None, correto_vlm=None, variante=True))
    return Registros(campos=campos)


def test_confianca_aplica_as_metas() -> None:
    conf = confianca.calcular(_registros(), META_ERRO_ESCAPADO)
    assert conf["n_minimo_sem_erro"] == 598
    esteira = conf["esteira"]
    assert esteira["erro_escapado"]["eventos"] == 0
    assert esteira["erro_escapado"]["total"] == 8
    assert esteira["erro_escapado"]["status"] == estatistica.NAO_DEMONSTRADA
    assert esteira["erro_escapado"]["limite_superior_95"] == pytest.approx(1 - 0.05 ** (1 / 8))
    # A alucinação inclui o campo apagado: 1 em 9 críticos aprovados.
    assert esteira["alucinacao_criticos_aprovados"]["eventos"] == 1
    assert esteira["alucinacao_criticos_aprovados"]["total"] == 9
    assert esteira["alucinacao_criticos_aprovados"]["status"] == estatistica.VIOLADA
    medicao = conf["medicao"]
    assert medicao["erro_escapado"]["eventos"] == 2
    assert medicao["erro_escapado"]["total"] == 10
    assert medicao["erro_escapado"]["status"] == estatistica.VIOLADA
    assert medicao["alucinacao_criticos_aprovados"]["eventos"] == 2


def test_comparacao_medicao_por_dimensao() -> None:
    comp = confianca.comparacao_medicao(_registros(), META_ERRO_ESCAPADO)
    assert set(comp) == {"geral", "campo", "subtipo", "uf", "qualidade"}
    assert "naturalidade" not in comp["campo"]  # sem crítico, fora da tabela
    rj = comp["uf"]["RJ"]
    assert rj["esteira"]["criticos_aprovados"] == 0
    assert rj["esteira"]["taxa_erro_escapado"] is None
    assert rj["esteira"]["ic95_superior"] == 1.0
    assert rj["medicao"]["criticos_aprovados"] == 2
    assert rj["medicao"]["erros_escapados"] == 2
    assert rj["medicao"]["alucinacoes_criticos"] == 1
    sp = comp["uf"]["SP"]
    assert sp["esteira"]["criticos_aprovados"] == 8
    assert sp["esteira"]["ic95_superior"] == pytest.approx(1 - 0.025 ** (1 / 8))
    assert comp["geral"]["medicao"]["criticos_aprovados"] == 10


def test_secoes_do_relatorio() -> None:
    registros = _registros()
    metricas = {
        "confianca": confianca.calcular(registros, META_ERRO_ESCAPADO),
        "comparacao_medicao": confianca.comparacao_medicao(registros, META_ERRO_ESCAPADO),
    }
    texto = "\n".join(relatorio_confianca.secoes(metricas))
    assert "## Confiança estatística das metas" in texto
    assert "**598**" in texto
    assert "**não demonstrada por amostra pequena**" in texto
    assert "**violada**" in texto
    assert "faltam 590 críticos aprovados" in texto
    assert "## Esteira × modo medição por dimensão" in texto
    assert "| UF | RJ | 0 | — (0/0) | — | sem aprovados |" in texto
    assert relatorio_confianca.secoes({}) == []
