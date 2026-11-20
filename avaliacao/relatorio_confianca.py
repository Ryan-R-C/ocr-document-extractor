"""Seções do relatório sobre a confiança das metas e a comparação com o modo medição.

Ficam fora de `relatorio.py` (que as chama no fim do Markdown) e repetem os formatadores para
não haver import circular. Só contagens e taxas, como o resto do relatório.
"""

from __future__ import annotations

from typing import Any


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{valor * 100:.2f}%"


def _ic(inferior: float | None, superior: float | None) -> str:
    if inferior is None or superior is None:
        return "—"
    return f"{_pct(inferior)} – {_pct(superior)}"


_TITULOS = (
    ("esteira", "erro_escapado", "Esteira", "Erro escapado"),
    ("esteira", "alucinacao_criticos_aprovados", "Esteira", "Alucinação em críticos aprovados"),
    ("medicao", "erro_escapado", "Medição", "Erro escapado"),
    ("medicao", "alucinacao_criticos_aprovados", "Medição", "Alucinação em críticos aprovados"),
)


def confianca(conf: dict[str, Any]) -> list[str]:
    n_min = conf["n_minimo_sem_erro"]
    meta = conf["meta_erro_escapado"]
    linhas = [
        "",
        "## Confiança estatística das metas",
        "",
        f"Intervalo exato de Clopper-Pearson (95%, bilateral) e limite superior unilateral de "
        f"95%. A meta só está **demonstrada** quando o limite superior fica abaixo de "
        f"{_pct(meta)}; sem nenhum erro, isso exige pelo menos **{n_min}** campos críticos "
        "aprovados. A meta de alucinação é zero: qualquer alucinação a viola, e a demonstração "
        "usa a mesma tolerância do erro escapado.",
        "",
        "| Modo | Métrica | Eventos / críticos aprovados | Taxa | IC 95% | Limite sup. 95% | "
        "Status |",
        "|---|---|---|---|---|---|---|",
    ]
    faltam: list[str] = []
    for modo, chave, titulo_modo, titulo in _TITULOS:
        r = conf[modo][chave]
        linhas.append(
            f"| {titulo_modo} | {titulo} | {r['eventos']}/{r['total']} | {_pct(r['taxa'])} | "
            f"{_ic(r['ic95_inferior'], r['ic95_superior'])} | {_pct(r['limite_superior_95'])} | "
            f"**{r['status']}** |"
        )
        if modo == "esteira" and r["eventos"] == 0 and r["total"] < n_min:
            faltam.append(f"{titulo.lower()}: faltam {n_min - r['total']} críticos aprovados")
    if faltam:
        linhas += ["", "Sem erro observado na esteira, para demonstrar: " + "; ".join(faltam) + "."]
    return linhas


_DIMENSOES = (("campo", "campo"), ("subtipo", "subtipo"), ("uf", "UF"), ("qualidade", "qualidade"))


def comparacao_medicao(comp: dict[str, Any]) -> list[str]:
    linhas = [
        "",
        "## Esteira × modo medição por dimensão",
        "",
        "Campos críticos dos documentos originais. Na esteira, aprovado é o documento "
        "`APROVADO`; na medição, todo campo `LEGIVEL` do VLM principal. O IC (95%, "
        "Clopper-Pearson) e o status são do erro escapado da esteira.",
        "",
        "| Dimensão | Grupo | Aprov. esteira | Erro escapado esteira | IC 95% | Status | "
        "Aluc. esteira | Aprov. medição | Erro escapado medição | Aluc. medição |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    grupos: list[tuple[str, str, dict[str, Any]]] = [("geral", "—", comp["geral"])]
    for chave, titulo in _DIMENSOES:
        grupos += [(titulo, nome, linha) for nome, linha in comp.get(chave, {}).items()]
    for titulo, nome, linha in grupos:
        e, m = linha["esteira"], linha["medicao"]
        sem_aprovados = e["criticos_aprovados"] == 0
        ic = "—" if sem_aprovados else _ic(e["ic95_inferior"], e["ic95_superior"])
        status = "sem aprovados" if sem_aprovados else e["status"]
        linhas.append(
            f"| {titulo} | {nome} | {e['criticos_aprovados']} | "
            f"{_pct(e['taxa_erro_escapado'])} ({e['erros_escapados']}/{e['criticos_aprovados']}) | "
            f"{ic} | {status} | "
            f"{e['alucinacoes_criticos']} | {m['criticos_aprovados']} | "
            f"{_pct(m['taxa_erro_escapado'])} ({m['erros_escapados']}/{m['criticos_aprovados']}) | "
            f"{m['alucinacoes_criticos']} |"
        )
    return linhas


def secoes(metricas: dict[str, Any]) -> list[str]:
    """As duas seções, quando o relatório as tem (relatórios antigos não têm)."""
    linhas: list[str] = []
    if "confianca" in metricas:
        linhas += confianca(metricas["confianca"])
    if "comparacao_medicao" in metricas:
        linhas += comparacao_medicao(metricas["comparacao_medicao"])
    return linhas
