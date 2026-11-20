"""Rascunho do relatório do Portão 2+3 gerado a partir dos relatorio.json do avaliador."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any, Literal

import pytest

from app.config import Settings
from avaliacao import portao, sintetico
from avaliacao.executor import ConfigAvaliacao, avaliar
from avaliacao.gabarito import ler_manifesto

SECOES = (
    "# Portão 2+3 · Integração (rascunho)",
    "## Rodadas",
    "## Veredito",
    "## Erro escapado e alucinação em críticos aprovados por dimensão",
    "## Comparação com os baselines",
    "## Revisão, 422, ancoragem e releitura",
    "## Custo e latência",
    "## Erros escapados e alucinações: casos",
    "## Efeito das opções em aberto: NAO_VERIFICAVEL e campo AUSENTE",
    "## Análise",
    "## Próximos passos",
)


@pytest.fixture(scope="module")
def gabarito(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destino = tmp_path_factory.mktemp("gabarito-portao")
    sintetico.gerar(destino, quantidade=24, semente=5)
    return destino


def _rodar(
    gabarito: Path, saida: Path, particao: Literal["ajuste", "holdout"], leitor_texto: str
) -> dict[str, Any]:
    config = ConfigAvaliacao.model_validate(
        {
            "nome": f"teste-{leitor_texto}",
            "gabarito": gabarito / "manifesto.jsonl",
            "saida": saida,
            "settings": {
                "leitor_visual_provider": "fake",
                "leitor_texto_provider": leitor_texto,
                "localizador_provider": "fake",
                "fake_gravacoes_dir": str(gabarito / "gravacoes"),
            },
        }
    )
    relatorio, _ = asyncio.run(
        avaliar(config, particao, settings=Settings(_env_file=None, **config.settings))
    )
    return relatorio


@pytest.fixture(scope="module")
def rodadas(gabarito: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    saida = tmp_path_factory.mktemp("rodadas-portao")
    # Ida e volta pelo JSON, como o comando lê.
    combinacoes: list[tuple[str, Literal["ajuste", "holdout"], str]] = [
        ("ajuste", "ajuste", "fake"),
        ("holdout", "holdout", "fake"),
        ("baseline", "ajuste", "nenhum"),
    ]
    return {
        nome: json.loads(json.dumps(_rodar(gabarito, saida, particao, leitor)))
        for nome, particao, leitor in combinacoes
    }


def _caso(relatorio: dict[str, Any]) -> dict[str, Any]:
    """Relatório com um caso de diagnóstico, que o sintético não produz."""
    r = copy.deepcopy(relatorio)
    r["metricas"]["diagnostico"]["casos"] = [
        {
            "id": "abc123def456",
            "campo": "cpf",
            "subtipo": "CNH",
            "uf": "SP",
            "qualidade": "foto",
            "tipo": "valor_errado",
            "categoria": "VLM_ERROU_OCR_CONFIRMOU",
            "sugestao": "sugestão fixa",
            "casamento_ancoragem": "exato",
            "status_validacao": "VALIDO",
            "regras": ["cpf.dv"],
        }
    ]
    r["metricas"]["diagnostico"]["por_categoria"]["VLM_ERROU_OCR_CONFIRMOU"] = 1
    return r


def test_todas_as_secoes(rodadas: dict[str, dict[str, Any]]) -> None:
    texto, avisos = portao.gerar(rodadas["ajuste"], rodadas["holdout"], [rodadas["baseline"]])

    posicoes = [texto.index(s) for s in SECOES]
    assert posicoes == sorted(posicoes)
    assert avisos == []
    assert "nada é ajustado olhando o holdout" in texto
    assert "**Veredito do portão (holdout):" in texto
    assert "provisório" not in texto
    for r in rodadas.values():
        assert f"`{r['execucao']['versao_pipeline']}`" in texto
    assert "versao_pipeline` diferente" not in texto
    # Medição do baseline lado a lado e nas tabelas por dimensão do ajuste.
    assert "Medição baseline 1 · teste-nenhum" in texto
    assert "| Dimensão | Grupo |" in texto and "Medição baseline 1 |" in texto
    # O baseline é do ajuste: fica fora da tabela por dimensão do holdout.
    assert "Baselines de outra partição ficam fora desta tabela (1 de 1)" in texto
    # As 4 opções de decisão, com o efeito na revisão.
    assert texto.count("NAO_VERIFICAVEL aprova · AUSENTE") == 4
    assert "Δ revisão" in texto
    assert "Taxa de ancoragem efetiva" in texto and "Taxa de releitura (críticos)" in texto
    assert "Custo p95" in texto and "Latência total p50 · p95" in texto
    analise = texto[texto.index("## Análise") :]
    assert analise.count("_a escrever_") == 2


def test_sem_holdout_veredito_provisorio(rodadas: dict[str, dict[str, Any]]) -> None:
    texto, _ = portao.gerar(rodadas["ajuste"], None, [rodadas["baseline"]])

    assert "**Veredito provisório (ajuste; holdout ainda não rodado):" in texto
    assert "Veredito do portão (holdout)" not in texto
    assert "nada é ajustado olhando o holdout" in texto


def test_veredito_violado_por_alucinacao(rodadas: dict[str, dict[str, Any]]) -> None:
    ajuste = copy.deepcopy(rodadas["ajuste"])
    ajuste["metricas"]["confianca"]["esteira"]["alucinacao_criticos_aprovados"]["status"] = (
        "violada"
    )
    texto, _ = portao.gerar(ajuste)
    assert "NÃO PASSOU (meta violada)" in texto


def test_aviso_de_versao_divergente(rodadas: dict[str, dict[str, Any]]) -> None:
    holdout = copy.deepcopy(rodadas["holdout"])
    holdout["execucao"]["versao_pipeline"] += "-outra"

    texto, _ = portao.gerar(rodadas["ajuste"], holdout, [])

    assert "ATENÇÃO: `versao_pipeline` diferente entre ajuste e holdout" in texto
    assert "configuração congelada" in texto
    assert "Inválido enquanto o `versao_pipeline` do holdout diferir do ajuste" in texto


def test_aviso_de_particao_trocada(rodadas: dict[str, dict[str, Any]]) -> None:
    texto, _ = portao.gerar(rodadas["holdout"], rodadas["ajuste"], [])
    assert "passado como ajuste é da partição `holdout`" in texto


def test_casos_com_proposta_a_escrever(rodadas: dict[str, dict[str, Any]]) -> None:
    texto, _ = portao.gerar(_caso(rodadas["ajuste"]))

    assert "Proposta de correção" in texto
    linha = next(x for x in texto.splitlines() if x.startswith("| abc123def456 |"))
    assert "VLM_ERROU_OCR_CONFIRMOU" in linha and "cpf.dv" in linha
    assert linha.endswith("| _a escrever_ |")


def test_json_antigo_omite_secoes_e_avisa(rodadas: dict[str, dict[str, Any]]) -> None:
    antigos = []
    for nome in ("ajuste", "holdout", "baseline"):
        r = copy.deepcopy(rodadas[nome])
        for chave in ("confianca", "comparacao_medicao", "diagnostico", "opcoes_decisao"):
            del r["metricas"][chave]
        del r["metricas"]["geral"]["segundo_leitor"]
        antigos.append(r)

    texto, avisos = portao.gerar(antigos[0], antigos[1], [antigos[2]])

    for chave in ("confianca", "comparacao_medicao", "diagnostico", "opcoes_decisao"):
        assert any(f"metricas.{chave}" in a for a in avisos), chave
    assert any("segundo_leitor" in a for a in avisos)
    assert texto.count("> **Aviso:**") == len(avisos)
    assert "Veredito do portão" not in texto and "Veredito provisório" not in texto
    assert "| Opção |" not in texto and "Taxa de ancoragem efetiva" not in texto
    # O resto continua: lado a lado, taxas e custo vêm de chaves antigas.
    for secao in SECOES:
        assert secao in texto
    assert "Taxa de revisão" in texto and "Custo p95" in texto


def test_sem_dados_pessoais(rodadas: dict[str, dict[str, Any]], gabarito: Path) -> None:
    texto, _ = portao.gerar(_caso(rodadas["ajuste"]), rodadas["holdout"], [rodadas["baseline"]])
    digitos_texto = re.sub(r"\D", "", texto)
    valores = [
        v.valor
        for item in ler_manifesto(gabarito / "manifesto.jsonl")
        for v in item.campos.values()
        if v.valor
    ]
    assert valores
    for valor in valores:
        if len(valor) >= 5:
            assert valor not in texto
        digitos = re.sub(r"\D", "", valor)
        if len(digitos) >= 8:
            assert digitos not in digitos_texto
    assert "imagens/" not in texto and ".png" not in texto


def test_cli(rodadas: dict[str, dict[str, Any]], tmp_path: Path, capsys) -> None:
    caminhos = {}
    for nome, r in rodadas.items():
        caminhos[nome] = tmp_path / f"{nome}.json"
        caminhos[nome].write_text(json.dumps(r), encoding="utf-8")
    antigo = copy.deepcopy(rodadas["holdout"])
    del antigo["metricas"]["opcoes_decisao"]
    caminhos["antigo"] = tmp_path / "antigo.json"
    caminhos["antigo"].write_text(json.dumps(antigo), encoding="utf-8")
    saida = tmp_path / "docs" / "portao.md"

    codigo = portao.main(
        [
            "--ajuste",
            str(caminhos["ajuste"]),
            "--holdout",
            str(caminhos["antigo"]),
            "--baseline",
            str(caminhos["baseline"]),
            "--baseline",
            str(caminhos["baseline"]),
            "--saida",
            str(saida),
        ]
    )

    assert codigo == 0
    texto = saida.read_text(encoding="utf-8")
    assert "Medição baseline 2" in texto
    erro = capsys.readouterr().err
    assert "aviso:" in erro and "opcoes_decisao" in erro
    assert portao.main(["--ajuste", str(tmp_path / "nao-existe.json"), "--saida", str(saida)]) == 2
