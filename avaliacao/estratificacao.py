"""Relatório de estratificação do gabarito: contagem por subtipo × UF × qualidade.

Aponta células vazias e células com menos de `minimo` exemplos, por partição.
Não contém dados pessoais: só contagens.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.api.contrato import UF, Subtipo
from avaliacao.gabarito import QUALIDADES, ItemGabarito

UFS = [u.value for u in UF if u is not UF.NAO_IDENTIFICADO]


def gerar(itens: list[ItemGabarito], minimo: int = 5) -> dict[str, Any]:
    originais = [i for i in itens if i.variante_de is None]
    contagem: Counter[tuple[str, str, str]] = Counter(i.estrato for i in originais)
    por_particao: dict[str, Counter[tuple[str, str, str]]] = {
        p: Counter(i.estrato for i in originais if (i.particao or "sem_particao") == p)
        for p in ("ajuste", "holdout", "sem_particao")
    }
    subtipos = sorted({i.subtipo.value for i in originais})
    ausentes = [
        s.value for s in Subtipo if s is not Subtipo.NAO_IDENTIFICADO and s.value not in subtipos
    ]

    celulas = []
    for subtipo in subtipos:
        for uf in UFS:
            for qualidade in QUALIDADES:
                chave = (subtipo, uf, qualidade)
                total = contagem.get(chave, 0)
                celulas.append(
                    {
                        "subtipo": subtipo,
                        "uf": uf,
                        "qualidade": qualidade,
                        "total": total,
                        "ajuste": por_particao["ajuste"].get(chave, 0),
                        "holdout": por_particao["holdout"].get(chave, 0),
                        "sem_particao": por_particao["sem_particao"].get(chave, 0),
                        "situacao": "vazia"
                        if total == 0
                        else ("pouca" if total < minimo else "ok"),
                    }
                )
    resumo = {
        "itens": len(originais),
        "minimo_por_celula": minimo,
        "por_particao": {p: sum(c.values()) for p, c in por_particao.items()},
        "por_subtipo": dict(Counter(i.subtipo.value for i in originais)),
        "por_qualidade": dict(Counter(i.qualidade for i in originais)),
        "por_uf": dict(Counter(i.uf.value for i in originais)),
        "celulas_total": len(celulas),
        "celulas_vazias": sum(1 for c in celulas if c["situacao"] == "vazia"),
        "celulas_com_poucos": sum(1 for c in celulas if c["situacao"] == "pouca"),
        "celulas_ok": sum(1 for c in celulas if c["situacao"] == "ok"),
        "subtipos_sem_exemplo": ausentes,
        "ufs_sem_exemplo": [u for u in UFS if u not in {i.uf.value for i in originais}],
    }
    return {"resumo": resumo, "celulas": celulas}


def _marca(celula: dict[str, Any]) -> str:
    total = int(celula["total"])
    if celula["situacao"] == "vazia":
        return "· 0"
    texto = f"{total} ({celula['holdout']}h)"
    return f"⚠ {texto}" if celula["situacao"] == "pouca" else texto


def markdown(relatorio: dict[str, Any]) -> str:
    r = relatorio["resumo"]
    linhas = [
        "# Estratificação do gabarito",
        "",
        f"- Itens (sem variantes): **{r['itens']}** · ajuste {r['por_particao']['ajuste']} · "
        f"holdout {r['por_particao']['holdout']} · "
        f"sem partição {r['por_particao']['sem_particao']}",
        f"- Mínimo por célula: {r['minimo_por_celula']}",
        f"- Células (subtipos presentes × 27 UFs × 3 qualidades): {r['celulas_total']} · "
        f"vazias **{r['celulas_vazias']}** · com poucos **{r['celulas_com_poucos']}** · "
        f"ok {r['celulas_ok']}",
        f"- Subtipos sem nenhum exemplo: {', '.join(r['subtipos_sem_exemplo']) or 'nenhum'}",
        f"- UFs sem nenhum exemplo: {', '.join(r['ufs_sem_exemplo']) or 'nenhuma'}",
        "",
        "Por qualidade: " + ", ".join(f"{q} {r['por_qualidade'].get(q, 0)}" for q in QUALIDADES),
        "",
        "Legenda: `N (Hh)` = N exemplos, H no holdout · `⚠` abaixo do mínimo · `· 0` vazia.",
    ]
    celulas = relatorio["celulas"]
    for subtipo in sorted({c["subtipo"] for c in celulas}):
        linhas += ["", f"## {subtipo}", "", "| UF | foto | xerox | print |", "|---|---|---|---|"]
        for uf in UFS:
            linha = [c for c in celulas if c["subtipo"] == subtipo and c["uf"] == uf]
            por_q = {c["qualidade"]: _marca(c) for c in linha}
            linhas.append(f"| {uf} | " + " | ".join(por_q[q] for q in QUALIDADES) + " |")
    return "\n".join(linhas) + "\n"
