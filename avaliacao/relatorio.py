"""Relatório do avaliador em Markdown e JSON.

Só contagens, taxas, nomes de campo e ids de caso (sha256 curto do arquivo): nada de valor
de campo nem nome de arquivo, que pode carregar dado pessoal.
"""

from __future__ import annotations

from typing import Any


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{valor * 100:.2f}%"


def _brl(valor: float | None) -> str:
    return "—" if valor is None else f"R$ {valor:.4f}"


def _ms(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:.0f} ms"


def _num(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:,.0f}".replace(",", ".")


def _ok(atingida: bool | None) -> str:
    return {True: "✅", False: "❌", None: "—"}[atingida]


def _tabela_grupo(titulo: str, grupos: dict[str, dict[str, Any]]) -> list[str]:
    linhas = [
        "",
        f"### Por {titulo}",
        "",
        "| Grupo | Docs | Aprov. | Revisão | 422 | Erro escapado | Alucinações | Acurácia | "
        "Custo médio | Latência p95 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for nome, m in grupos.items():
        total = m["latencia_ms"].get("total", {})
        linhas.append(
            f"| {nome} | {m['documentos']} | {_pct(m['taxa_aprovacao'])} | "
            f"{_pct(m['taxa_revisao'])} | {_pct(m['taxa_422'])} | "
            f"{_pct(m['taxa_erro_escapado'])} ({m['erros_escapados']}/{m['criticos_aprovados']}) | "
            f"{m['alucinacoes']} | {_pct(m['acuracia'])} | {_brl(m['custo_brl']['media'])} | "
            f"{_ms(total.get('p95'))} |"
        )
    return linhas


def markdown(relatorio: dict[str, Any]) -> str:
    meta = relatorio["execucao"]
    m = relatorio["metricas"]
    g = m["geral"]
    metas = m["metas"]
    linhas = [
        f"# Avaliação · {meta['nome']} · partição `{meta['particao']}`",
        "",
    ]
    if meta["particao"] == "holdout":
        linhas += [
            "> **Holdout.** Número final do relatório. Nada pode ser ajustado a partir "
            "destes resultados.",
            "",
        ]
    if meta.get("stubs_ativos"):
        linhas += [
            f"> **Stubs ativos:** {', '.join(meta['stubs_ativos'])}. Nenhum documento pode "
            "ser APROVADO enquanto houver stub na decisão; as taxas abaixo medem a esteira, "
            "não a qualidade final.",
            "",
        ]
    linhas += [
        f"- Data: {meta['inicio']} · duração {meta['duracao_s']:.1f} s",
        f"- versao_pipeline: `{meta['versao_pipeline']}`",
        f"- Modo: {meta['modo']} · gabarito: `{meta['gabarito']}` · itens: {meta['itens']} "
        f"(+ {meta['variantes']} variantes de campo apagado)",
        f"- Leitor visual: {meta.get('leitor_visual', '—')} · desenho: {meta.get('desenho', '—')} "
        f"· detalhe: {meta.get('detalhe', '—')} · raciocínio: {meta.get('raciocinio', '—')}",
        "",
        "## Metas",
        "",
        "| Métrica | Valor | Meta | |",
        "|---|---|---|---|",
        f"| Erro escapado | {_pct(metas['erro_escapado']['valor'])} "
        f"({g['erros_escapados']}/{g['criticos_aprovados']} críticos aprovados) | < 0,5% | "
        f"{_ok(metas['erro_escapado']['atingida'])} |",
        f"| Alucinação em críticos aprovados (com campo apagado) | "
        f"{metas['alucinacao_criticos_aprovados']['valor']} | zero | "
        f"{_ok(metas['alucinacao_criticos_aprovados']['atingida'])} |",
        f"| Custo médio por documento | {_brl(metas['custo_medio_brl']['valor'])} | < R$ 0,15 | "
        f"{_ok(metas['custo_medio_brl']['atingida'])} |",
        "",
        "## Geral",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Documentos | {g['documentos']} |",
        f"| Desfechos | {', '.join(f'{k} {v}' for k, v in sorted(g['desfechos'].items()))} |",
        f"| Taxa de revisão | {_pct(g['taxa_revisao'])} |",
        f"| Taxa de 422 | {_pct(g['taxa_422'])} |",
        f"| Taxa de erro de provider | {_pct(g['taxa_erro_provider'])} |",
        f"| Subtipo correto | {_pct(g['subtipo_correto'])} |",
        f"| Acurácia (todos os campos) | {_pct(g['acuracia'])} |",
        f"| Acurácia (críticos) | {_pct(g['acuracia_criticos'])} |",
        f"| Alucinações (gabarito AUSENTE/ILEGIVEL) | {g['alucinacoes']} "
        f"({_pct(g['taxa_alucinacao'])}) |",
        f"| Custo médio · p95 · total | {_brl(g['custo_brl']['media'])} · "
        f"{_brl(g['custo_brl']['p95'])} · {_brl(g['custo_brl']['total'])} |",
    ]
    if g["taxa_422_por_motivo"]:
        linhas += ["", "### 422 por motivo", "", "| Motivo | Taxa |", "|---|---|"]
        linhas += [f"| {k} | {_pct(v)} |" for k, v in g["taxa_422_por_motivo"].items()]

    linhas += ["", "### Latência por etapa", "", "| Etapa | p50 | p95 |", "|---|---|---|"]
    linhas += [
        f"| {etapa} | {_ms(v['p50'])} | {_ms(v['p95'])} |" for etapa, v in g["latencia_ms"].items()
    ]

    med = g["medicao"]
    linhas += [
        "",
        "## Modo medição (baseline)",
        "",
        "Se todo campo `LEGIVEL` fosse aprovado direto, sem segundo leitor nem regras:",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Erro escapado (críticos) | {_pct(med['taxa_erro_escapado'])} "
        f"({med['erros_escapados']}/{med['criticos_aprovados']}) |",
        f"| Alucinações em críticos | {med['alucinacoes_criticos']} |",
        f"| Alucinações (todos os campos) | {med['alucinacoes']} |",
        f"| Alucinações no campo apagado | {m['campo_apagado'].get('medicao_alucinacoes', 0)} |",
        "",
        "## Classificação",
        "",
        f"- Subtipo correto: {_pct(g['subtipo_correto'])} · UF correta: {_pct(g['uf_correta'])} "
        f"· lado correto: {_pct(g['lado_correto'])}",
        "",
        "### Sinalizações de captura (contra a qualidade do gabarito)",
        "",
        "| Sinalização | Fonte | Precisão | Revocação | VP | FP | FN |",
        "|---|---|---|---|---|---|---|",
    ]
    for sinal, fontes in m.get("captura", {}).items():
        for fonte, r in fontes.items():
            linhas.append(
                f"| {sinal} | {fonte} | {_pct(r['precisao'])} | {_pct(r['revocacao'])} | "
                f"{r['vp']} | {r['fp']} | {r['fn']} |"
            )
    linhas += [
        "",
        "## Tokens por etapa (comparação com o plano)",
        "",
        "Plano: 3.420 de entrada (1.920 de imagem + 1.500 de instruções e schema) e 600 de saída.",
        "",
        "| Etapa | Modelos | Chamadas | Entrada média | Entrada p95 | Imagem estimada | "
        "Saída média | Raciocínio | Custo médio | Latência p50/p95 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for etapa, t in m.get("tokens", {}).items():
        linhas.append(
            f"| {etapa} | {', '.join(t['modelos'])} | {t['chamadas']} | {_num(t['entrada_media'])} | "
            f"{_num(t['entrada_p95'])} | {_num(t['imagem_estimada_media'])} | "
            f"{_num(t['saida_media'])} | {_num(t['raciocinio_medio'])} | "
            f"{_brl(t['custo_medio_brl'])} | {_ms(t['latencia_p50_ms'])} / {_ms(t['latencia_p95_ms'])} |"
        )
    cp = m.get("custo_plano", {})
    linhas += [
        "",
        "## Custo comparado ao plano",
        "",
        f"- Configuração: {' + '.join(cp.get('configuracao') or []) or '—'} · plano: "
        f"{_brl(cp.get('plano_brl'))} · medido: média {_brl(cp.get('medido_medio_brl'))}, "
        f"p95 {_brl(cp.get('medido_p95_brl'))}",
        f"- Observação: {cp.get('observacao', '')}",
    ]
    linhas += [
        "",
        "## Por campo",
        "",
        "| Campo | Crítico | Acurácia | Erro escapado | Erro escapado (medição) | Alucinações |",
        "|---|---|---|---|---|---|",
    ]
    for nome, c in m["por"]["campo"].items():
        cm = c["medicao"]
        linhas.append(
            f"| {nome} | {c['critico']} | {_pct(c['acuracia'])} | "
            f"{_pct(c['taxa_erro_escapado'])} ({c['erros_escapados']}/{c['criticos_aprovados']}) | "
            f"{_pct(cm['taxa_erro_escapado'])} ({cm['erros_escapados']}/{cm['criticos_aprovados']}) | "
            f"{c['alucinacoes']} |"
        )
    for dimensao, titulo in (
        ("subtipo", "subtipo"),
        ("uf", "UF"),
        ("qualidade", "qualidade de imagem"),
    ):
        linhas += _tabela_grupo(titulo, m["por"][dimensao])

    ca = m["campo_apagado"]
    linhas += [
        "",
        "## Teste do campo apagado",
        "",
        f"- Variantes: {ca['variantes']} · desfechos: "
        f"{', '.join(f'{k} {v}' for k, v in sorted(ca['desfechos'].items())) or '—'}",
        f"- Alucinações no campo apagado: **{ca['alucinacoes']}** de {ca['campos_apagados']} "
        f"({_pct(ca['taxa_alucinacao'])}); "
        f"em documentos aprovados: **{ca['alucinacoes_em_aprovados']}**",
        f"- Pulados por falta de caixa: {meta['campo_apagado_pulados_sem_caixa']} · "
        f"por formato (PDF): {meta['campo_apagado_pulados_formato']}",
    ]
    if m.get("piores_casos"):
        linhas += [
            "",
            "## Piores casos",
            "",
            "| Id | Subtipo | UF | Qualidade | Desfecho | Alucinações | Campos errados | Subtipo lido |",
            "|---|---|---|---|---|---|---|---|",
        ]
        linhas += [
            f"| {c['id']} | {c['subtipo']} | {c['uf']} | {c['qualidade']} | {c['desfecho']} | "
            f"{', '.join(c['alucinacoes']) or '—'} | {', '.join(c['campos_errados']) or '—'} | "
            f"{c['subtipo_previsto'] or '—'} |"
            for c in m["piores_casos"]
        ]
    if m["erros_escapados"]:
        linhas += ["", "## Erros escapados", "", "| Id | Campo | Tipo |", "|---|---|---|"]
        linhas += [f"| {e['id']} | {e['campo']} | {e['tipo']} |" for e in m["erros_escapados"]]
    if m["alucinacoes"]:
        linhas += [
            "",
            "## Alucinações",
            "",
            "| Id | Campo | Crítico | Doc. aprovado | Aprovado na medição | Campo apagado |",
            "|---|---|---|---|---|---|",
        ]
        sim = {True: "sim", False: "não"}
        linhas += [
            f"| {a['id']} | {a['campo']} | {sim[a['critico']]} | {sim[a['aprovado']]} | "
            f"{sim[a['aprovado_medicao']]} | {sim[a['campo_apagado']]} |"
            for a in m["alucinacoes"]
        ]
    return "\n".join(linhas) + "\n"
