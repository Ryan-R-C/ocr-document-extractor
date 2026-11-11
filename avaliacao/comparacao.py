"""Comparação lado a lado de rodadas do avaliador (desenho do schema, modelos, esforço).

Uso: `uv run comparar saidas/avaliacoes/<a>/relatorio.json saidas/avaliacoes/<b>/relatorio.json`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from avaliacao.relatorio import _brl, _ms, _num, _pct

LINHAS: list[tuple[str, Any]] = [
    ("Leitor visual", lambda r: r["execucao"].get("leitor_visual", "—")),
    ("Desenho", lambda r: r["execucao"].get("desenho", "—")),
    ("Raciocínio", lambda r: r["execucao"].get("raciocinio", "—")),
    ("Modo", lambda r: r["execucao"]["modo"]),
    ("Partição", lambda r: r["execucao"]["particao"]),
    ("Documentos", lambda r: str(r["metricas"]["geral"]["documentos"])),
    ("Subtipo correto", lambda r: _pct(r["metricas"]["geral"]["subtipo_correto"])),
    ("UF correta", lambda r: _pct(r["metricas"]["geral"].get("uf_correta"))),
    ("Lado correto", lambda r: _pct(r["metricas"]["geral"].get("lado_correto"))),
    ("Acurácia dos críticos", lambda r: _pct(r["metricas"]["geral"]["acuracia_criticos"])),
    ("Acurácia de todos os campos", lambda r: _pct(r["metricas"]["geral"]["acuracia"])),
    (
        "Erro escapado (medição)",
        lambda r: _pct(r["metricas"]["geral"]["medicao"]["taxa_erro_escapado"]),
    ),
    (
        "Alucinações em críticos (medição)",
        lambda r: str(r["metricas"]["geral"]["medicao"]["alucinacoes_criticos"]),
    ),
    (
        "Alucinações no campo apagado",
        lambda r: str(r["metricas"]["campo_apagado"]["alucinacoes"]),
    ),
    ("Taxa de 422", lambda r: _pct(r["metricas"]["geral"]["taxa_422"])),
    ("Custo médio", lambda r: _brl(r["metricas"]["geral"]["custo_brl"]["media"])),
    ("Custo p95", lambda r: _brl(r["metricas"]["geral"]["custo_brl"]["p95"])),
    (
        "Latência total p50 / p95",
        lambda r: f"{_ms(_lat(r, 'total', 'p50'))} / {_ms(_lat(r, 'total', 'p95'))}",
    ),
    (
        "Latência da leitura visual p50 / p95",
        lambda r: f"{_ms(_lat(r, 'visual_total', 'p50'))} / {_ms(_lat(r, 'visual_total', 'p95'))}",
    ),
    (
        "Tokens de entrada (média por chamada)",
        lambda r: (
            " · ".join(
                f"{etapa} {_num(t['entrada_media'])}"
                for etapa, t in r["metricas"].get("tokens", {}).items()
            )
            or "—"
        ),
    ),
]


def _lat(relatorio: dict[str, Any], etapa: str, p: str) -> float | None:
    valor = relatorio["metricas"]["geral"]["latencia_ms"].get(etapa, {}).get(p)
    return float(valor) if valor is not None else None


def comparar(relatorios: list[dict[str, Any]]) -> str:
    nomes = [r["execucao"]["nome"] for r in relatorios]
    linhas = [
        "# Comparação de rodadas",
        "",
        "| Métrica | " + " | ".join(nomes) + " |",
        "|---|" + "---|" * len(nomes),
    ]
    for titulo, extrair in LINHAS:
        valores = []
        for r in relatorios:
            try:
                valores.append(str(extrair(r)))
            except (KeyError, TypeError):
                valores.append("—")
        linhas.append(f"| {titulo} | " + " | ".join(valores) + " |")
    particoes = {r["execucao"]["particao"] for r in relatorios}
    if len(particoes) > 1:
        linhas += ["", "> Atenção: as rodadas usam partições diferentes; não compare os números."]
    return "\n".join(linhas) + "\n"


def carregar(caminhos: list[Path]) -> list[dict[str, Any]]:
    return [json.loads(c.read_text(encoding="utf-8")) for c in caminhos]
