"""Rascunho do relatório do Portão 2+3 a partir dos `relatorio.json` das rodadas.

Uso:
    portao --ajuste <relatorio.json> [--holdout <relatorio.json>]
           [--baseline <relatorio.json> ...] --saida <arquivo.md>

Só lê os JSON: não roda a esteira nem recalcula métricas. As chaves vêm de
`metricas.calcular` (`por`, `medicao`, `confianca`, `comparacao_medicao`, `diagnostico`,
`opcoes_decisao`, `segundo_leitor`, custo, latência); um relatório antigo sem alguma delas
tem a seção omitida, com aviso no documento e na saída de erro.

Os baselines são as rodadas da Fase 1, sem segundo leitor: deles só se lê o modo medição.

Invariante 10: o documento traz só contagens, taxas, nomes de campo, categorias e ids de
caso (sha256 curto), nunca valor de campo nem nome de arquivo.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from avaliacao.estatistica import DEMONSTRADA, VIOLADA
from avaliacao.relatorio import _brl, _contagem, _ms, _pct, _rotulo_opcao
from avaliacao.relatorio_confianca import _ic

A_ESCREVER = "_a escrever_"
LEMBRETE_HOLDOUT = (
    "> **Holdout:** nada é ajustado olhando o holdout. Se o portão não passar, as mudanças "
    "são propostas, validadas na partição de ajuste e mostradas antes de aplicar."
)
_DIMENSOES = (("campo", "Campo"), ("subtipo", "Subtipo"), ("uf", "UF"), ("qualidade", "Qualidade"))


@dataclass
class Rodada:
    papel: str  # "ajuste", "holdout" ou "baseline N"
    relatorio: dict[str, Any]

    @property
    def execucao(self) -> dict[str, Any]:
        execucao: dict[str, Any] = self.relatorio.get("execucao", {})
        return execucao

    @property
    def metricas(self) -> dict[str, Any]:
        metricas: dict[str, Any] = self.relatorio.get("metricas", {})
        return metricas

    @property
    def nome(self) -> str:
        return str(self.execucao.get("nome", "—"))

    @property
    def particao(self) -> str:
        return str(self.execucao.get("particao", "—"))

    @property
    def rotulo(self) -> str:
        return f"{self.papel} · {self.nome} ({self.particao})"


@dataclass
class Documento:
    """Acumula as linhas e os avisos de chave ausente."""

    linhas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def omitir(self, secao: str, rodada: Rodada, chave: str) -> None:
        aviso = (
            f'Seção "{secao}" omitida para {rodada.rotulo}: o relatorio.json não tem '
            f"`{chave}` (gerado por um avaliador anterior). Rode o avaliador de novo."
        )
        self.avisos.append(aviso)
        self.linhas += ["", f"> **Aviso:** {aviso}"]


def _tem(d: dict[str, Any], *caminho: str) -> bool:
    atual: Any = d
    for chave in caminho:
        if not isinstance(atual, dict) or chave not in atual:
            return False
        atual = atual[chave]
    return True


# ---------------------------------------------------------------- 1. Cabeçalho


def _cabecalho(
    doc: Documento, ajuste: Rodada, holdout: Rodada | None, baselines: list[Rodada]
) -> None:
    doc.linhas += [
        "# Portão 2+3 · Integração (rascunho)",
        "",
        f"Rascunho gerado em {date.today().isoformat()} por `uv run portao` a partir dos "
        "`relatorio.json` das rodadas. Os números são lidos dos relatórios, sem recálculo. "
        f"As células e seções marcadas {A_ESCREVER} são preenchidas à mão.",
        "",
        "Meta do portão: **zero alucinação em campos críticos aprovados** e **erro escapado "
        "abaixo de 0,5%**.",
        "",
        LEMBRETE_HOLDOUT,
        "",
        "## Rodadas",
        "",
        "| Papel | Nome | Partição | Data | Documentos | Variantes | versao_pipeline |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in [ajuste, *([holdout] if holdout else []), *baselines]:
        e = r.execucao
        doc.linhas.append(
            f"| {r.papel} | {r.nome} | {r.particao} | {e.get('inicio', '—')} | "
            f"{e.get('itens', '—')} | {e.get('variantes', '—')} | "
            f"`{e.get('versao_pipeline', '—')}` |"
        )
    if _versoes_divergem(ajuste, holdout):
        doc.linhas += [
            "",
            "> **⚠ ATENÇÃO: `versao_pipeline` diferente entre ajuste e holdout.** O holdout "
            "precisa rodar com a configuração congelada da rodada de ajuste (mesmo provider, "
            "modelo, prompt, schemas e regras). Este holdout não vale como número final do "
            "portão.",
        ]
    esperado = [(ajuste, "ajuste")] + ([(holdout, "holdout")] if holdout else [])
    for r, particao in esperado:
        if r.particao != particao:
            doc.linhas += [
                "",
                f"> **Atenção:** o relatório passado como {r.papel} é da partição "
                f"`{r.particao}`, não `{particao}`.",
            ]
    for r in baselines:
        if r.particao != ajuste.particao:
            doc.linhas += [
                "",
                f"> **Atenção:** o {r.papel} é da partição `{r.particao}` e o ajuste da "
                f"`{ajuste.particao}`: a comparação mistura populações.",
            ]
    for r in [ajuste, *([holdout] if holdout else [])]:
        stubs = r.execucao.get("stubs_ativos")
        if stubs:
            doc.linhas += [
                "",
                f"> **Stubs ativos em {r.rotulo}:** {', '.join(stubs)}. A rodada mede a "
                "esteira, não a qualidade final.",
            ]


# ---------------------------------------------------------------- 2. Veredito


_PONTUAL = {True: "atingida", False: "não atingida", None: "—"}


def _versoes_divergem(ajuste: Rodada, holdout: Rodada | None) -> bool:
    return holdout is not None and ajuste.execucao.get("versao_pipeline") != holdout.execucao.get(
        "versao_pipeline"
    )


def _veredito_estatistico(conf: dict[str, Any]) -> str:
    status = [
        conf["esteira"]["erro_escapado"]["status"],
        conf["esteira"]["alucinacao_criticos_aprovados"]["status"],
    ]
    if VIOLADA in status:
        return "NÃO PASSOU (meta violada)"
    if all(s == DEMONSTRADA for s in status):
        return "PASSOU (metas demonstradas)"
    return "INCONCLUSIVO (meta não demonstrada por amostra pequena)"


def _veredito(doc: Documento, ajuste: Rodada, holdout: Rodada | None) -> None:
    doc.linhas += [
        "",
        "## Veredito",
        "",
        "Veredito estatístico (decisões 119 a 121): limite superior unilateral de 95% "
        "(Clopper-Pearson) abaixo de 0,5% para **demonstrar** a meta; qualquer alucinação em "
        "crítico aprovado, ou taxa observada ≥ meta, a **viola**. O veredito pontual (taxa "
        "observada × meta) aparece só como referência.",
    ]
    rodadas = [ajuste, *([holdout] if holdout else [])]
    linhas_tabela: list[str] = []
    for r in rodadas:
        conf = r.metricas.get("confianca")
        if conf is None:
            doc.omitir("Veredito estatístico", r, "metricas.confianca")
            continue
        metas = r.metricas.get("metas", {})
        for chave, titulo in (
            ("erro_escapado", "Erro escapado"),
            ("alucinacao_criticos_aprovados", "Alucinação em críticos aprovados"),
        ):
            s = conf["esteira"][chave]
            pontual = _PONTUAL[metas.get(chave, {}).get("atingida")]
            linhas_tabela.append(
                f"| {r.papel} | {titulo} | {s['eventos']}/{s['total']} | {_pct(s['taxa'])} | "
                f"{_pct(s['limite_superior_95'])} | **{s['status']}** | "
                f"{pontual} |"
            )
    if linhas_tabela:
        doc.linhas += [
            "",
            "| Partição | Meta | Eventos / críticos aprovados | Taxa | Limite sup. 95% | "
            "Status estatístico | Pontual |",
            "|---|---|---|---|---|---|---|",
            *linhas_tabela,
        ]
    final = holdout if holdout is not None else ajuste
    conf = final.metricas.get("confianca")
    if conf is None:
        return
    veredito = _veredito_estatistico(conf)
    if holdout is not None:
        invalido = (
            " Inválido enquanto o `versao_pipeline` do holdout diferir do ajuste."
            if _versoes_divergem(ajuste, holdout)
            else ""
        )
        doc.linhas += ["", f"**Veredito do portão (holdout): {veredito}.**{invalido}"]
    else:
        doc.linhas += [
            "",
            f"**Veredito provisório (ajuste; holdout ainda não rodado): {veredito}.** "
            "O veredito do portão sai do holdout, rodado com a configuração congelada.",
        ]
    faltam = conf["n_minimo_sem_erro"] - conf["esteira"]["erro_escapado"]["total"]
    if conf["esteira"]["erro_escapado"]["eventos"] == 0 and faltam > 0:
        doc.linhas.append(
            f"Sem erro observado, a demonstração exige {conf['n_minimo_sem_erro']} críticos "
            f"aprovados: faltam {faltam}."
        )


# ---------------------------------------------------------------- 3. Por dimensão


def _medicao_grupo(r: Rodada, dimensao: str | None, grupo: str | None) -> dict[str, Any] | None:
    """Modo medição de uma rodada num grupo: `comparacao_medicao` ou, se faltar, `por`."""
    m = r.metricas
    med: Any
    if dimensao is None:
        if _tem(m, "comparacao_medicao", "geral"):
            med = m["comparacao_medicao"]["geral"]["medicao"]
        else:
            med = m.get("geral", {}).get("medicao")
    elif grupo in m.get("comparacao_medicao", {}).get(dimensao, {}):
        med = m["comparacao_medicao"][dimensao][grupo]["medicao"]
    else:
        med = m.get("por", {}).get(dimensao, {}).get(grupo, {}).get("medicao")
    return med if isinstance(med, dict) else None


def _celula_medicao(med: dict[str, Any] | None) -> str:
    if not med or not med.get("criticos_aprovados"):
        return "—"
    return (
        f"{_pct(med['taxa_erro_escapado'])} ({med['erros_escapados']}/"
        f"{med['criticos_aprovados']}) · aluc. {med.get('alucinacoes_criticos', '—')}"
    )


def _por_dimensao(doc: Documento, rodadas: list[Rodada], baselines: list[Rodada]) -> None:
    doc.linhas += [
        "",
        "## Erro escapado e alucinação em críticos aprovados por dimensão",
        "",
        "Campos críticos dos documentos originais (sem as variantes do campo apagado). "
        "Esteira: documento `APROVADO`, com IC 95% de Clopper-Pearson e status do erro "
        "escapado. Medição: todo campo `LEGIVEL` do VLM principal aprovado direto, na própria "
        "rodada e em cada baseline da mesma partição. Célula de medição: erro escapado "
        "(erros/aprovados) · alucinações em críticos.",
    ]
    for r in rodadas:
        comp = r.metricas.get("comparacao_medicao")
        doc.linhas += ["", f"### {r.papel.capitalize()} · {r.nome}"]
        if comp is None:
            doc.omitir("Por dimensão", r, "metricas.comparacao_medicao")
            continue
        # Baseline de outra partição mediria outra população: fica fora desta tabela.
        bases = [b for b in baselines if b.particao == r.particao]
        if len(bases) < len(baselines):
            doc.linhas += [
                "",
                f"Baselines de outra partição ficam fora desta tabela "
                f"({len(baselines) - len(bases)} de {len(baselines)}).",
            ]
        cab_base = "".join(f" Medição {b.papel} |" for b in bases)
        doc.linhas += [
            "",
            "| Dimensão | Grupo | Aprov. esteira | Erro escapado esteira | IC 95% | Status | "
            f"Aluc. esteira | Medição da rodada |{cab_base}",
            "|---|---|---|---|---|---|---|---|" + "---|" * len(bases),
        ]
        grupos: list[tuple[str, str | None, str | None, dict[str, Any]]] = [
            ("geral", None, None, comp["geral"])
        ]
        for chave, titulo in _DIMENSOES:
            grupos += [(titulo, chave, g, linha) for g, linha in comp.get(chave, {}).items()]
        for titulo, dimensao, grupo, linha in grupos:
            e = linha["esteira"]
            sem = e["criticos_aprovados"] == 0
            base = "".join(
                f" {_celula_medicao(_medicao_grupo(b, dimensao, grupo))} |" for b in bases
            )
            doc.linhas.append(
                f"| {titulo} | {grupo or '—'} | {e['criticos_aprovados']} | "
                f"{_pct(e['taxa_erro_escapado'])} ({e['erros_escapados']}/"
                f"{e['criticos_aprovados']}) | "
                f"{'—' if sem else _ic(e['ic95_inferior'], e['ic95_superior'])} | "
                f"{'sem aprovados' if sem else e['status']} | {e['alucinacoes_criticos']} | "
                f"{_celula_medicao(linha['medicao'])} |{base}"
            )


# ---------------------------------------------------------------- 4. Baselines


def _lat(r: Rodada, etapa: str, p: str) -> float | None:
    valor = r.metricas.get("geral", {}).get("latencia_ms", {}).get(etapa, {}).get(p)
    return None if valor is None else float(valor)


def _conf(r: Rodada, modo: str, chave: str) -> dict[str, Any] | None:
    if not _tem(r.metricas, "confianca", modo, chave):
        return None
    valor: dict[str, Any] = r.metricas["confianca"][modo][chave]
    return valor


def _linhas_lado_a_lado() -> list[tuple[str, Callable[[Rodada, str], str]]]:
    def escapado(r: Rodada, modo: str) -> str:
        g = r.metricas["geral"]
        base = g if modo == "esteira" else g["medicao"]
        return (
            f"{_pct(base['taxa_erro_escapado'])} ({base['erros_escapados']}/"
            f"{base['criticos_aprovados']})"
        )

    def limite(r: Rodada, modo: str) -> str:
        c = _conf(r, modo, "erro_escapado")
        return "—" if c is None else f"{_pct(c['limite_superior_95'])} · {c['status']}"

    def alucinacao(r: Rodada, modo: str) -> str:
        g = r.metricas["geral"]
        if modo == "esteira":
            return str(g.get("alucinacoes_criticos_aprovados_incluindo_campo_apagado", "—"))
        return str(g["medicao"]["alucinacoes_criticos"])

    def limite_aluc(r: Rodada, modo: str) -> str:
        c = _conf(r, modo, "alucinacao_criticos_aprovados")
        return "—" if c is None else f"{_pct(c['limite_superior_95'])} · {c['status']}"

    def so_esteira(f: Callable[[Rodada], str]) -> Callable[[Rodada, str], str]:
        return lambda r, modo: f(r) if modo == "esteira" else "—"

    g: Callable[[Rodada], dict[str, Any]] = lambda r: r.metricas["geral"]  # noqa: E731
    return [
        ("Partição", lambda r, _m: r.particao),
        ("Leitor visual", lambda r, _m: str(r.execucao.get("leitor_visual", "—"))),
        ("Documentos", lambda r, _m: str(g(r)["documentos"])),
        ("Erro escapado (críticos aprovados)", escapado),
        ("Limite sup. 95% · status", limite),
        ("Alucinações em críticos aprovados", alucinacao),
        ("Alucinação: limite sup. 95% · status", limite_aluc),
        ("Acurácia dos críticos", lambda r, _m: _pct(g(r)["acuracia_criticos"])),
        ("Taxa de aprovação", so_esteira(lambda r: _pct(g(r).get("taxa_aprovacao")))),
        ("Taxa de revisão", so_esteira(lambda r: _pct(g(r)["taxa_revisao"]))),
        ("Taxa de 422", lambda r, _m: _pct(g(r)["taxa_422"])),
        (
            "Custo médio · p95",
            lambda r, _m: f"{_brl(g(r)['custo_brl']['media'])} · {_brl(g(r)['custo_brl']['p95'])}",
        ),
        (
            "Latência total p50 · p95",
            lambda r, _m: f"{_ms(_lat(r, 'total', 'p50'))} · {_ms(_lat(r, 'total', 'p95'))}",
        ),
    ]


def _lado_a_lado(doc: Documento, rodadas: list[Rodada], baselines: list[Rodada]) -> None:
    doc.linhas += [
        "",
        "## Comparação com os baselines",
        "",
        "Esteira completa de cada partição × modo medição da mesma rodada e de cada "
        'baseline da Fase 1 (sem segundo leitor). Na medição, "aprovado" é todo campo '
        "`LEGIVEL` do VLM principal; taxa de aprovação e de revisão só valem para a esteira.",
    ]
    if not baselines:
        doc.linhas += ["", "> **Aviso:** nenhum baseline informado (`--baseline`)."]
        doc.avisos.append("nenhum baseline informado")
    colunas: list[tuple[str, Rodada, str]] = [(f"Esteira {r.papel}", r, "esteira") for r in rodadas]
    colunas += [(f"Medição {r.papel}", r, "medicao") for r in rodadas]
    colunas += [(f"Medição {b.papel} · {b.nome}", b, "medicao") for b in baselines]
    doc.linhas += [
        "",
        "| Métrica | " + " | ".join(t for t, _, _ in colunas) + " |",
        "|---|" + "---|" * len(colunas),
    ]
    for titulo, extrair in _linhas_lado_a_lado():
        valores = []
        for _, r, modo in colunas:
            try:
                valores.append(extrair(r, modo))
            except (KeyError, TypeError):
                valores.append("—")
        doc.linhas.append(f"| {titulo} | " + " | ".join(valores) + " |")


# ---------------------------------------------------------------- 5. Taxas


def _taxa_motivo(motivo: str) -> Callable[[Rodada], str]:
    return lambda r: _pct(r.metricas["geral"]["taxa_422_por_motivo"].get(motivo, 0.0))


def _latencia(etapa: str) -> Callable[[Rodada], str]:
    return lambda r: f"{_ms(_lat(r, etapa, 'p50'))} · {_ms(_lat(r, etapa, 'p95'))}"


def _taxas(doc: Documento, rodadas: list[Rodada]) -> None:
    doc.linhas += [
        "",
        "## Revisão, 422, ancoragem e releitura",
        "",
        "422 por motivo conta ocorrências de motivo ÷ documentos (um documento pode ter mais "
        "de um motivo). Ancoragem: campos impressos lidos pelo VLM e ancorados no OCR, antes "
        "e depois da releitura. Releitura: críticos relidos ÷ críticos lidos.",
    ]
    sem_sl = [r for r in rodadas if not _tem(r.metricas, "geral", "segundo_leitor")]
    for r in sem_sl:
        doc.omitir("Ancoragem e releitura", r, "metricas.geral.segundo_leitor")
    doc.linhas += [
        "",
        "| Métrica | " + " | ".join(r.papel for r in rodadas) + " |",
        "|---|" + "---|" * len(rodadas),
    ]

    def linha(titulo: str, f: Callable[[Rodada], str]) -> None:
        valores = []
        for r in rodadas:
            try:
                valores.append(f(r))
            except (KeyError, TypeError):
                valores.append("—")
        doc.linhas.append(f"| {titulo} | " + " | ".join(valores) + " |")

    def g(r: Rodada) -> dict[str, Any]:
        geral: dict[str, Any] = r.metricas["geral"]
        return geral

    def sl(r: Rodada) -> dict[str, Any]:
        segundo: dict[str, Any] = r.metricas["geral"]["segundo_leitor"]
        return segundo

    linha("Documentos", lambda r: str(g(r)["documentos"]))
    linha("Desfechos", lambda r: _contagem(g(r)["desfechos"]))
    linha("Taxa de aprovação", lambda r: _pct(g(r).get("taxa_aprovacao")))
    linha("Taxa de revisão", lambda r: _pct(g(r)["taxa_revisao"]))
    linha("Taxa de 422", lambda r: _pct(g(r)["taxa_422"]))
    motivos = sorted({k for r in rodadas for k in g(r).get("taxa_422_por_motivo", {})})
    for motivo in motivos:
        linha(f"422 · {motivo}", _taxa_motivo(motivo))
    linha("Taxa de erro de provider", lambda r: _pct(g(r).get("taxa_erro_provider")))
    if len(sem_sl) == len(rodadas):
        return
    linha("Taxa de ancoragem (antes da releitura)", lambda r: _pct(sl(r)["taxa_ancoragem"]))
    linha("Taxa de ancoragem dos críticos", lambda r: _pct(sl(r)["taxa_ancoragem_criticos"]))
    linha("Taxa de ancoragem efetiva", lambda r: _pct(sl(r)["taxa_ancoragem_efetiva"]))
    linha("Ancorados com valor errado", lambda r: str(sl(r)["ancorados_errados"]))
    linha(
        "Taxa de releitura (críticos)",
        lambda r: (
            f"{_pct(sl(r)['taxa_releitura'])} ({sl(r)['releituras']}/{sl(r)['criticos_lidos']})"
        ),
    )
    linha(
        "Documentos com releitura",
        lambda r: _pct(r.metricas.get("escalada", {}).get("taxa_documentos_com_releitura")),
    )
    linha("Releituras não executadas", lambda r: str(sl(r)["releituras_nao_executadas"]))
    linha("Corrigidos pela releitura", lambda r: str(sl(r)["corrigidos_pela_releitura"]))


# ---------------------------------------------------------------- 6. Custo e latência

_ETAPAS = ("total", "preprocessamento", "visual_total", "leitura_texto", "ancoragem", "releitura")


def _custo_latencia(doc: Documento, rodadas: list[Rodada]) -> None:
    doc.linhas += [
        "",
        "## Custo e latência",
        "",
        "| Métrica | " + " | ".join(r.papel for r in rodadas) + " |",
        "|---|" + "---|" * len(rodadas),
    ]

    def celula(f: Callable[[Rodada], str], r: Rodada) -> str:
        try:
            return f(r)
        except (KeyError, TypeError):
            return "—"

    def custo(r: Rodada) -> dict[str, Any]:
        c: dict[str, Any] = r.metricas["geral"]["custo_brl"]
        return c

    linhas: list[tuple[str, Callable[[Rodada], str]]] = [
        ("Custo médio por documento (meta < R$ 0,15)", lambda r: _brl(custo(r)["media"])),
        ("Custo p95", lambda r: _brl(custo(r)["p95"])),
        ("Custo total", lambda r: _brl(custo(r)["total"])),
        (
            "Custo do plano (mesma configuração)",
            lambda r: _brl(r.metricas["custo_plano"]["plano_brl"]),
        ),
        (
            "Custo da escalada por documento",
            lambda r: _brl(r.metricas["escalada"]["custo_medio_por_documento_brl"]),
        ),
    ]
    for etapa in _ETAPAS:
        linhas.append(
            (
                f"Latência {etapa} p50 · p95",
                _latencia(etapa),
            )
        )
    for titulo, f in linhas:
        doc.linhas.append(f"| {titulo} | " + " | ".join(celula(f, r) for r in rodadas) + " |")


# ---------------------------------------------------------------- 7. Casos


def _casos(doc: Documento, rodadas: list[Rodada]) -> None:
    doc.linhas += [
        "",
        "## Erros escapados e alucinações: casos",
        "",
        "Cada campo crítico errado ou inventado num documento aprovado (inclui as variantes "
        "do campo apagado), com a etapa que deixou passar (decisão 115). A sugestão é fixa por "
        "categoria; a proposta de correção é escrita à mão e validada na partição de ajuste.",
    ]
    for r in rodadas:
        diag = r.metricas.get("diagnostico")
        doc.linhas += ["", f"### {r.papel.capitalize()} · {r.nome}"]
        if diag is None:
            doc.omitir("Casos (diagnóstico)", r, "metricas.diagnostico")
            continue
        doc.linhas += [
            "",
            "| Etapa que deixou passar | Casos |",
            "|---|---|",
            *[f"| {cat} | {n} |" for cat, n in diag["por_categoria"].items()],
        ]
        if not diag["casos"]:
            doc.linhas += ["", "Nenhum erro escapado nem alucinação em campo crítico aprovado."]
            continue
        doc.linhas += [
            "",
            "| Id | Campo | Subtipo | UF | Qualidade | Tipo | Etapa que deixou passar | "
            "Casamento | Regras | Sugestão | Proposta de correção |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        doc.linhas += [
            f"| {c['id']} | {c['campo']} | {c['subtipo']} | {c['uf']} | {c['qualidade']} | "
            f"{c['tipo']} | {c['categoria']} | {c.get('casamento_ancoragem') or '—'} | "
            f"{', '.join(c.get('regras', [])) or '—'} | {c['sugestao']} | {A_ESCREVER} |"
            for c in diag["casos"]
        ]


# ---------------------------------------------------------------- 8. Opções de decisão


def _opcoes(doc: Documento, rodadas: list[Rodada]) -> None:
    doc.linhas += [
        "",
        "## Efeito das opções em aberto: NAO_VERIFICAVEL e campo AUSENTE",
        "",
        "Decisão recalculada (decisão 122) em cada combinação de "
        "`APROVAR_CRITICO_NAO_VERIFICAVEL` × `CRITICO_AUSENTE`, com as mesmas leituras da "
        "rodada. Δ revisão é em pontos percentuais contra a opção da rodada. 422 por motivo "
        "conta documentos (decisão 123).",
    ]
    for r in rodadas:
        od = r.metricas.get("opcoes_decisao")
        doc.linhas += ["", f"### {r.papel.capitalize()} · {r.nome}"]
        if not od:
            doc.omitir("Opções de decisão", r, "metricas.opcoes_decisao")
            continue
        if od.get("divergencias_com_a_rodada"):
            doc.linhas += [
                "",
                f"> **Atenção:** o recálculo da opção da rodada divergiu da esteira em "
                f"{len(od['divergencias_com_a_rodada'])} documento(s): "
                f"{', '.join(od['divergencias_com_a_rodada'])}.",
            ]
        da_rodada = next((o for o in od["opcoes"].values() if o["da_rodada"]), None)
        doc.linhas += [
            "",
            "| Opção | Aprovado | Revisão | Δ revisão | 422 | 422 por motivo | "
            "Erro escapado | Alucinações em críticos aprovados |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for o in od["opcoes"].values():
            d = o["desfechos"]
            delta = (
                "—"
                if da_rodada is None
                else f"{(o['taxa_revisao'] - da_rodada['taxa_revisao']) * 100:+.2f} pp"
            )
            doc.linhas.append(
                f"| {_rotulo_opcao(o)} | {_pct(o['taxa_aprovacao'])} ({d.get('APROVADO', 0)}) | "
                f"{_pct(o['taxa_revisao'])} ({d.get('REVISAO', 0)}) | {delta} | "
                f"{_pct(o['taxa_422'])} ({d.get('RECUSADO', 0)}) | "
                f"{_contagem(o['422_por_motivo'])} | "
                f"{_pct(o['taxa_erro_escapado'])} ({o['erros_escapados']}/"
                f"{o['criticos_aprovados']}) | "
                f"{o['alucinacoes_criticos_aprovados_incluindo_campo_apagado']} |"
            )


# ---------------------------------------------------------------- Montagem


def gerar(
    ajuste: dict[str, Any],
    holdout: dict[str, Any] | None = None,
    baselines: list[dict[str, Any]] | None = None,
) -> tuple[str, list[str]]:
    """Devolve o Markdown do rascunho e os avisos (seções omitidas, entradas faltando)."""
    doc = Documento()
    r_ajuste = Rodada("ajuste", ajuste)
    r_holdout = Rodada("holdout", holdout) if holdout is not None else None
    r_bases = [Rodada(f"baseline {i}", b) for i, b in enumerate(baselines or [], start=1)]
    rodadas = [r_ajuste, *([r_holdout] if r_holdout else [])]

    _cabecalho(doc, r_ajuste, r_holdout, r_bases)
    _veredito(doc, r_ajuste, r_holdout)
    _por_dimensao(doc, rodadas, r_bases)
    _lado_a_lado(doc, rodadas, r_bases)
    _taxas(doc, rodadas)
    _custo_latencia(doc, rodadas)
    _casos(doc, rodadas)
    _opcoes(doc, rodadas)
    doc.linhas += [
        "",
        "## Análise",
        "",
        A_ESCREVER,
        "",
        "## Próximos passos",
        "",
        A_ESCREVER,
        "",
        LEMBRETE_HOLDOUT,
    ]
    return "\n".join(doc.linhas) + "\n", doc.avisos


def _carregar(caminho: Path) -> dict[str, Any]:
    dados: dict[str, Any] = json.loads(caminho.read_text(encoding="utf-8"))
    return dados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="portao",
        description="Gera o rascunho do relatório do Portão 2+3 a partir dos relatorio.json.",
    )
    parser.add_argument("--ajuste", type=Path, required=True, help="relatorio.json do ajuste")
    parser.add_argument("--holdout", type=Path, default=None, help="relatorio.json do holdout")
    parser.add_argument(
        "--baseline",
        type=Path,
        action="append",
        default=[],
        help="relatorio.json de uma rodada da Fase 1 sem segundo leitor (repetível)",
    )
    parser.add_argument("--saida", type=Path, required=True, help="arquivo Markdown de saída")
    args = parser.parse_args(argv)

    try:
        ajuste = _carregar(args.ajuste)
        holdout = _carregar(args.holdout) if args.holdout else None
        baselines = [_carregar(b) for b in args.baseline]
    except (OSError, json.JSONDecodeError) as erro:
        print(f"erro: não foi possível ler um relatorio.json: {erro}", file=sys.stderr)
        return 2
    texto, avisos = gerar(ajuste, holdout, baselines)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(texto, encoding="utf-8")
    for aviso in avisos:
        print(f"aviso: {aviso}", file=sys.stderr)
    print(f"rascunho do portão: {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
