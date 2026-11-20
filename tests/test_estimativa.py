"""Estimativa de custo e orçamento do avaliador. Nada aqui chama provider real."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.core.pipeline import Esteira
from app.providers.fabrica import criar_dependencias
from app.providers.interfaces import LeitorVisual, PedidoVisual, RespostaVisual
from app.providers.openai import LeitorVisualOpenAI
from avaliacao import campo_apagado, estimativa, executor, sintetico
from avaliacao.cli import main_avaliar
from avaliacao.estimativa import (
    TAXA_RELEITURA_PADRAO,
    OrcamentoInsuficiente,
    estimar,
)
from avaliacao.executor import ConfigAvaliacao, avaliar
from avaliacao.gabarito import ler_manifesto
from avaliacao.metricas import PLANO_TOKENS_SAIDA


@pytest.fixture(scope="module")
def gabarito(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destino = tmp_path_factory.mktemp("gabarito")
    sintetico.gerar(destino, quantidade=24, semente=3)
    return destino


def _fake(gabarito: Path) -> dict[str, Any]:
    return {
        "leitor_visual_provider": "fake",
        "leitor_texto_provider": "fake",
        "localizador_provider": "fake",
        "fake_gravacoes_dir": str(gabarito / "gravacoes"),
    }


def _openai(**extra: Any) -> dict[str, Any]:
    # Provider real só no nome: nenhum teste chega a criar o adaptador ou chamar a rede.
    return {
        "leitor_visual_provider": "openai",
        "leitor_visual_modelo": "gpt-6.1-terra",
        "leitor_visual_modelo_escalada": "gpt-6.1-sol",
        "leitor_texto_provider": "paddleocr",
        "localizador_provider": "ocr",
        **extra,
    }


def _config(gabarito: Path, saida: Path, settings: dict[str, Any], **extra: Any) -> ConfigAvaliacao:
    dados: dict[str, Any] = {
        "nome": "teste",
        "gabarito": gabarito / "manifesto.jsonl",
        "saida": saida,
        "concorrencia": 1,
        "settings": settings,
    }
    dados.update(extra)
    return ConfigAvaliacao.model_validate(dados)


def _settings(config: ConfigAvaliacao) -> Settings:
    return Settings(_env_file=None, **config.settings)


def _itens(gabarito: Path) -> list[Any]:
    return [i for i in ler_manifesto(gabarito / "manifesto.jsonl") if i.particao == "ajuste"]


@pytest.fixture
def sem_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Qualquer tentativa de montar a esteira ou chamar a OpenAI derruba o teste."""

    def proibido(*_: Any, **__: Any) -> Any:
        raise AssertionError("provider chamado")

    monkeypatch.setattr(executor, "criar_esteira", proibido)
    monkeypatch.setattr(executor, "criar_dependencias_batch", proibido)
    monkeypatch.setattr(LeitorVisualOpenAI, "ler", proibido)
    monkeypatch.setattr(LeitorVisualOpenAI, "__init__", proibido)


# --- Contagens por desenho --------------------------------------------------------------------


def test_contagens_desenho_unico_batem_com_as_variantes_geradas(
    gabarito: Path, tmp_path: Path
) -> None:
    config = _config(gabarito, tmp_path, _openai())
    e = estimar(config, "ajuste", _settings(config))

    itens = _itens(gabarito)
    geradas = asyncio.run(
        campo_apagado.gerar_variantes(itens, gabarito, tmp_path / "v", ["borrar", "apagar"])
    )
    assert e.imagens == len(itens)
    # O sintético tem caixa em todo campo: a contagem é exata.
    assert e.variantes.baixa == e.variantes.alta == len(geradas.itens) > 0
    docs = len(itens) + len(geradas.itens)
    assert e.documentos.central == docs

    assert [et.etapa for et in e.etapas] == ["leitura_visual", "releitura"]
    leitura, releitura = e.etapas
    assert leitura.modelo == "gpt-6.1-terra" and releitura.modelo == "gpt-6.1-sol"
    assert leitura.chamadas.central == docs
    assert leitura.saida_por_chamada == PLANO_TOKENS_SAIDA
    assert releitura.chamadas.central == pytest.approx(docs * TAXA_RELEITURA_PADRAO)
    assert releitura.chamadas.baixa < releitura.chamadas.central < releitura.chamadas.alta
    assert e.brl.baixa < e.brl.central < e.brl.alta
    assert e.brl.central == pytest.approx(e.usd.central * 5.40)
    assert e.brl_por_documento.central == pytest.approx(e.brl.central / docs)


def test_contagens_duas_etapas_e_sem_ocr_nao_rele(gabarito: Path, tmp_path: Path) -> None:
    config = _config(
        gabarito,
        tmp_path,
        _openai(
            leitor_visual_desenho="duas_etapas",
            leitor_visual_modelo_classificacao="gpt-6-luna",
            leitor_texto_provider="nenhum",
        ),
    )
    e = estimar(config, "ajuste", _settings(config))
    assert [et.etapa for et in e.etapas] == ["classificacao", "extracao"]
    classificacao, extracao = e.etapas
    assert classificacao.modelo == "gpt-6-luna" and extracao.modelo == "gpt-6.1-terra"
    assert classificacao.chamadas == extracao.chamadas
    assert e.taxa_releitura.central == 0


def test_campo_apagado_inativo_e_taxa_configurada(gabarito: Path, tmp_path: Path) -> None:
    config = _config(
        gabarito,
        tmp_path,
        _openai(),
        campo_apagado={"ativo": False},
        estimativa={"taxa_releitura": 1.5},
    )
    e = estimar(config, "ajuste", _settings(config))
    assert e.variantes.central == 0 and e.documentos.central == e.imagens
    assert e.etapas[1].chamadas.central == pytest.approx(1.5 * e.imagens)
    assert e.fonte_taxa_releitura == "configurada"


def test_sem_caixa_com_ocr_vira_faixa_de_variantes(gabarito: Path, tmp_path: Path) -> None:
    from avaliacao.gabarito import escrever_manifesto

    itens = [
        i.model_copy(
            update={
                "campos": {n: v.model_copy(update={"caixa": None}) for n, v in i.campos.items()}
            }
        )
        for i in ler_manifesto(gabarito / "manifesto.jsonl")
    ]
    copia = tmp_path / "g"
    escrever_manifesto(copia / "manifesto.jsonl", itens)
    for item in itens:
        destino = copia / item.arquivo
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes((gabarito / item.arquivo).read_bytes())
    sem_ocr = _config(copia, tmp_path, _openai())
    com_ocr = _config(copia, tmp_path, _openai(), campo_apagado={"usar_ocr_sem_caixa": True})
    assert estimar(sem_ocr, "ajuste", _settings(sem_ocr)).variantes.alta == 0
    e = estimar(com_ocr, "ajuste", _settings(com_ocr))
    assert e.variantes.baixa == 0 < e.variantes.alta
    assert any("OCR" in a for a in e.avisos)


# --- Base medida ------------------------------------------------------------------------------


def _relatorio_base(caminho: Path, modelo: str = "gpt-6.1-terra") -> Path:
    dados = {
        "execucao": {"itens": 10, "variantes": 10},
        "metricas": {
            "tokens": {
                "leitura_visual": {
                    "chamadas": 20,
                    "modelos": [modelo],
                    "entrada_media": 5000.0,
                    "entrada_p95": 7000.0,
                    "saida_media": 800.0,
                },
                "releitura": {
                    "chamadas": 5,
                    "modelos": ["gpt-6.1-sol"],
                    "entrada_media": 700.0,
                    "entrada_p95": 800.0,
                    "saida_media": 90.0,
                },
            }
        },
    }
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return caminho


def test_base_medida_da_os_tokens_e_a_taxa(gabarito: Path, tmp_path: Path) -> None:
    base = _relatorio_base(tmp_path / "relatorio.json")
    config = _config(gabarito, tmp_path, _openai(), estimativa={"base": str(base)})
    e = estimar(config, "ajuste", _settings(config))
    leitura, releitura = e.etapas
    assert leitura.entrada_por_chamada == pytest.approx(5000)
    assert leitura.saida_por_chamada == pytest.approx(800)
    assert "medida" in leitura.fonte
    assert releitura.entrada_por_chamada == pytest.approx(700)
    # 5 releituras em 20 documentos.
    assert e.taxa_releitura.central == pytest.approx(0.25)
    assert "medida" in e.fonte_taxa_releitura
    # Alta usa o p95 quando ele passa da folga.
    n = leitura.chamadas.alta
    esperado = (n * 7000 * 1.0 + n * 800 * 1.25 * 5.0) / 1e6
    assert leitura.usd.alta == pytest.approx(esperado, rel=1e-3)


def test_base_de_outro_modelo_cai_no_plano(gabarito: Path, tmp_path: Path) -> None:
    base = _relatorio_base(tmp_path / "relatorio.json", modelo="gpt-6-luna")
    config = _config(gabarito, tmp_path, _openai(), estimativa={"base": str(base)})
    e = estimar(config, "ajuste", _settings(config))
    assert "plano" in e.etapas[0].fonte
    assert any("sem medida na base" in a for a in e.avisos)


def test_base_relativa_ao_arquivo_da_config(gabarito: Path, tmp_path: Path) -> None:
    _relatorio_base(tmp_path / "relatorio.json")
    toml = tmp_path / "rodada.toml"
    toml.write_text(
        f'nome = "x"\ngabarito = "{gabarito / "manifesto.jsonl"}"\n'
        '[estimativa]\nbase = "relatorio.json"\n',
        encoding="utf-8",
    )
    config = ConfigAvaliacao.carregar(toml)
    assert config.estimativa.base == (tmp_path / "relatorio.json").resolve()


# --- --estimar não chama provider -------------------------------------------------------------


def test_estimar_na_cli_nao_chama_provider(
    gabarito: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], sem_provider: None
) -> None:
    toml = tmp_path / "rodada.toml"
    toml.write_text(
        f'nome = "openai-x"\ngabarito = "{gabarito / "manifesto.jsonl"}"\n'
        f'saida = "{tmp_path / "saidas"}"\n'
        '[settings]\nleitor_visual_provider = "openai"\nleitor_texto_provider = "paddleocr"\n',
        encoding="utf-8",
    )
    codigo = main_avaliar(["--config", str(toml), "--particao", "ajuste", "--estimar"])
    saida = capsys.readouterr().out
    assert codigo == 0
    assert "Nenhum provider foi chamado" in saida
    assert "leitura_visual · gpt-6.1-terra" in saida and "releitura · gpt-6.1-sol" in saida
    assert not (tmp_path / "saidas").exists()

    # Com orçamento abaixo da alta, o --estimar avisa e sai com código 3.
    codigo = main_avaliar(
        ["--config", str(toml), "--particao", "ajuste", "--estimar", "--orcamento-brl", "0.01"]
    )
    assert codigo == 3
    assert "PASSA DO ORÇAMENTO" in capsys.readouterr().out


# --- Orçamento --------------------------------------------------------------------------------


def test_orcamento_aborta_antes_de_qualquer_chamada(
    gabarito: Path, tmp_path: Path, sem_provider: None
) -> None:
    config = _config(gabarito, tmp_path, _openai(), orcamento_brl=0.01)
    with pytest.raises(OrcamentoInsuficiente, match="nenhuma chamada"):
        asyncio.run(avaliar(config, "ajuste", settings=_settings(config)))
    assert not any(tmp_path.iterdir())  # nem a pasta da rodada foi criada


def test_orcamento_na_cli_aborta_com_codigo_3(
    gabarito: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], sem_provider: None
) -> None:
    toml = tmp_path / "rodada.toml"
    toml.write_text(
        f'nome = "openai-x"\ngabarito = "{gabarito / "manifesto.jsonl"}"\n'
        "orcamento_brl = 0.01\n"
        '[settings]\nleitor_visual_provider = "openai"\n',
        encoding="utf-8",
    )
    assert main_avaliar(["--config", str(toml), "--particao", "ajuste"]) == 3
    assert "passa do orçamento" in capsys.readouterr().err


def test_fake_nao_e_bloqueado(gabarito: Path, tmp_path: Path) -> None:
    config = _config(gabarito, tmp_path, _fake(gabarito), orcamento_brl=0.0001)
    relatorio, _ = asyncio.run(avaliar(config, "ajuste", settings=_settings(config)))
    assert relatorio["metricas"]["geral"]["documentos"] == len(_itens(gabarito))
    assert "orcamento" not in relatorio["execucao"]
    e = estimar(config, "ajuste", _settings(config), 0.0001)
    assert e.brl.alta == 0 and e.cabe_no_orcamento is True


class _DubleCaro:
    """Leitor fake que cobra um valor fixo e alto por chamada."""

    def __init__(self, interno: LeitorVisual, custo_brl: float) -> None:
        self.interno = interno
        self.nome = interno.nome
        self.custo_brl = custo_brl
        self.chamadas = 0

    async def ler(self, pedido: PedidoVisual) -> RespostaVisual:
        self.chamadas += 1
        resposta = await self.interno.ler(pedido)
        metricas = dataclasses.replace(resposta.metricas, custo_brl=self.custo_brl)
        return dataclasses.replace(resposta, metricas=metricas)


def test_orcamento_interrompe_a_rodada_pelo_custo_medido(
    gabarito: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A config diz openai (custa); a esteira usa o fake com um dublê que cobra caro.
    config = _config(gabarito, tmp_path, _openai(leitor_texto_provider="fake"))
    settings = _settings(config)
    orcamento = estimar(config, "ajuste", settings).brl.alta * 1.01
    config.orcamento_brl = orcamento
    custo_chamada = orcamento / 2.5  # a 3ª chamada passa do teto

    settings_fake = Settings(_env_file=None, **_fake(gabarito))
    deps = criar_dependencias(settings_fake)
    duble = _DubleCaro(deps.leitor_visual, custo_chamada)

    def esteira_com_duble(_: Settings) -> Esteira:
        return Esteira(settings_fake, dataclasses.replace(deps, leitor_visual=duble))

    monkeypatch.setattr(executor, "criar_esteira", esteira_com_duble)
    relatorio, pasta = asyncio.run(avaliar(config, "ajuste", settings=settings))

    o = relatorio["execucao"]["orcamento"]
    assert o["interrompida"] is True
    assert o["custo_medido_brl"] > orcamento
    # Concorrência 1: o excedente é só o das releituras em voo de um documento.
    assert duble.chamadas == o["chamadas_medidas"] <= 3 + 6
    assert o["documentos_processados"] < o["documentos_previstos"]
    assert relatorio["metricas"]["geral"]["documentos"] == o["documentos_processados"]
    assert (pasta / "relatorio.md").read_text().startswith("> **Rodada interrompida")


def test_guarda_recusa_depois_de_passar_do_limite() -> None:
    from app.providers.interfaces import MetricasChamada

    class Fixo:
        nome = "fixo"

        async def ler(self, pedido: PedidoVisual) -> RespostaVisual:
            return RespostaVisual({}, {}, MetricasChamada("x", "m", "e", custo_brl=1.0))

    guarda = estimativa.GuardaOrcamento(Fixo(), limite_brl=1.5)
    pedido: Any = None
    asyncio.run(guarda.ler(pedido))
    asyncio.run(guarda.ler(pedido))  # 1,0 não passou do limite: a 2ª sai
    assert guarda.estourado and guarda.gasto_brl == 2.0
    with pytest.raises(estimativa.OrcamentoEsgotado):
        asyncio.run(guarda.ler(pedido))
    assert guarda.chamadas == 2 and guarda.recusadas == 1
    grupo = ExceptionGroup("g", [estimativa.OrcamentoEsgotado("x")])
    assert estimativa.eh_orcamento_esgotado(grupo)
    assert not estimativa.eh_orcamento_esgotado(ExceptionGroup("g", [ValueError("x")]))
