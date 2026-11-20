"""Recálculo da decisão nas opções em aberto, sem novas chamadas a provider.

A prova central roda o avaliador de verdade nas quatro combinações e confere que o
recálculo feito a partir de qualquer uma das rodadas reproduz as outras três.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.api.contrato import MotivoErro, StatusDocumento, StatusLeitura, TipoDocumento
from app.config import Settings
from app.core.erros import FalhaExtracao
from app.core.pipeline import ResultadoPipeline
from app.montagem import criar_esteira
from app.providers.fake import gravacoes
from app.providers.fake.gravacoes import CampoGravado
from app.schemas.campos import nomes_criticos
from avaliacao import metricas, redecisao, relatorio, sintetico
from avaliacao.executor import ConfigAvaliacao, _processar, avaliar
from avaliacao.gabarito import ItemGabarito, ler_manifesto

CHAVES = [o.chave for o in redecisao.OPCOES]


def _config(gabarito: Path, saida: Path, **settings: Any) -> ConfigAvaliacao:
    return ConfigAvaliacao.model_validate(
        {
            "nome": "redecisao",
            "gabarito": gabarito / "manifesto.jsonl",
            "saida": saida,
            "settings": {
                "leitor_visual_provider": "fake",
                "leitor_texto_provider": "fake",
                "localizador_provider": "fake",
                "fake_gravacoes_dir": str(gabarito / "gravacoes"),
                **settings,
            },
            "campo_apagado": {"metodos": ["apagar"]},
        }
    )


def _com_critico_ausente(gabarito: Path) -> list[ItemGabarito]:
    """Em dois itens com mais de um crítico, o fake passa a ler um crítico como AUSENTE: é
    o caso em que `CRITICO_AUSENTE` muda o desfecho (o gabarito sintético não tem)."""
    alterados = []
    for item in ler_manifesto(gabarito / "manifesto.jsonl"):
        if item.particao != "ajuste" or item.variante_de is not None:
            continue
        criticos = [n for n in nomes_criticos(item.subtipo) if n in item.campos]
        if len(criticos) < 2:
            continue
        gravacao = gravacoes.carregar(gabarito / "gravacoes", item.sha256)
        if gravacao is None or gravacao.leitura_visual is None:
            continue
        campos = dict(gravacao.leitura_visual.campos)
        campos[criticos[0]] = CampoGravado(status=StatusLeitura.AUSENTE)
        lv = gravacao.leitura_visual.model_copy(update={"campos": campos})
        gravacoes.salvar(
            gabarito / "gravacoes", item.sha256, gravacao.model_copy(update={"leitura_visual": lv})
        )
        alterados.append(item)
        if len(alterados) == 2:
            break
    assert len(alterados) == 2
    return alterados


@pytest.fixture(scope="module")
def gabarito(tmp_path_factory) -> Path:
    destino = tmp_path_factory.mktemp("gabarito_redecisao")
    sintetico.gerar(destino, quantidade=24, semente=3)
    _com_critico_ausente(destino)
    return destino


@pytest.fixture(scope="module")
def rodadas(gabarito: Path, tmp_path_factory) -> dict[str, dict[str, Any]]:
    """As quatro rodadas reais, uma por combinação, indexadas pela chave da opção."""
    saida = tmp_path_factory.mktemp("saidas_redecisao")
    resultado = {}
    for opcao in redecisao.OPCOES:
        config = _config(
            gabarito,
            saida / opcao.chave.replace(";", "_").replace("=", "-"),
            aprovar_critico_nao_verificavel=opcao.aprovar_nao_verificavel,
            critico_ausente=opcao.critico_ausente,
        )
        settings = Settings(_env_file=None, **config.settings)
        rel, _ = asyncio.run(avaliar(config, "ajuste", settings=settings))
        resultado[opcao.chave] = rel
    return resultado


def _sem_rodada(bloco: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in bloco.items() if k != "da_rodada"}


def test_opcoes_sao_as_quatro_combinacoes() -> None:
    assert len(set(CHAVES)) == 4
    assert {(o.aprovar_nao_verificavel, o.critico_ausente) for o in redecisao.OPCOES} == {
        (False, "REVISAO"),
        (False, "422"),
        (True, "REVISAO"),
        (True, "422"),
    }
    padrao = redecisao.Opcao.de(Settings(_env_file=None))
    assert padrao.chave == "aprovar_nao_verificavel=false;critico_ausente=REVISAO"


def test_recalculo_reproduz_as_quatro_rodadas_reais(rodadas: dict[str, dict[str, Any]]) -> None:
    for chave_real, real in rodadas.items():
        od_real = real["metricas"]["opcoes_decisao"]
        assert od_real["rodada"] == chave_real
        assert od_real["divergencias_com_a_rodada"] == []
        # A opção da rodada, recalculada, bate com as métricas gerais calculadas por
        # metricas.py sobre o que a esteira devolveu.
        g, proprio = real["metricas"]["geral"], od_real["opcoes"][chave_real]
        assert proprio["da_rodada"] is True
        assert proprio["desfechos"] == dict(sorted(g["desfechos"].items()))
        for k in (
            "taxa_aprovacao",
            "taxa_revisao",
            "taxa_422",
            "criticos_aprovados",
            "erros_escapados",
            "taxa_erro_escapado",
            "alucinacoes_criticos_aprovados",
            "alucinacoes_criticos_aprovados_incluindo_campo_apagado",
        ):
            assert proprio[k] == g[k], k
        for subtipo, bloco in real["metricas"]["por"]["subtipo"].items():
            sub = proprio["por_subtipo"][subtipo]
            assert sub["desfechos"] == dict(sorted(bloco["desfechos"].items()))
            for k in ("taxa_revisao", "criticos_aprovados", "erros_escapados"):
                assert sub[k] == bloco[k], (subtipo, k)
        # Recalculada a partir de qualquer outra rodada, dá exatamente o mesmo bloco.
        for chave_outra, outra in rodadas.items():
            recalculado = outra["metricas"]["opcoes_decisao"]["opcoes"][chave_real]
            assert _sem_rodada(recalculado) == _sem_rodada(proprio), (chave_outra, chave_real)


def test_as_opcoes_mudam_o_desfecho(rodadas: dict[str, dict[str, Any]]) -> None:
    opcoes = rodadas[CHAVES[0]]["metricas"]["opcoes_decisao"]["opcoes"]
    revisao = opcoes["aprovar_nao_verificavel=false;critico_ausente=REVISAO"]
    recusa = opcoes["aprovar_nao_verificavel=false;critico_ausente=422"]
    libera = opcoes["aprovar_nao_verificavel=true;critico_ausente=REVISAO"]
    # Os dois críticos AUSENTE ganham o motivo próprio; o que estava em revisão vira 422
    # (um deles já era 422 por outro crítico ilegível).
    assert recusa["422_por_motivo"].get(MotivoErro.CAMPO_CRITICO_AUSENTE.value) == 2
    assert MotivoErro.CAMPO_CRITICO_AUSENTE.value not in revisao["422_por_motivo"]
    saiu_da_revisao = revisao["desfechos"]["REVISAO"] - recusa["desfechos"].get("REVISAO", 0)
    assert saiu_da_revisao >= 1
    assert recusa["desfechos"]["RECUSADO"] == revisao["desfechos"]["RECUSADO"] + saiu_da_revisao
    assert recusa["desfechos"].get("APROVADO") == revisao["desfechos"].get("APROVADO")
    # NAO_VERIFICAVEL liberado aprova mais (RG fora de SP no sintético).
    assert libera["desfechos"]["APROVADO"] > revisao["desfechos"]["APROVADO"]
    assert libera["criticos_aprovados"] > revisao["criticos_aprovados"]
    for o in opcoes.values():
        assert o["documentos"] == revisao["documentos"]


def test_relatorio_tem_a_secao_das_opcoes(rodadas: dict[str, dict[str, Any]]) -> None:
    texto = relatorio.markdown(rodadas[CHAVES[0]])
    secao = texto.split("## Efeito das opções de decisão", 1)[1]
    assert secao.count("(rodada)") >= 1
    assert "NAO_VERIFICAVEL aprova · AUSENTE 422" in secao
    assert "CAMPO_CRITICO_AUSENTE: 2" in secao
    assert "Atenção" not in secao


def test_relatorio_sem_opcoes_nao_tem_secao(rodadas: dict[str, dict[str, Any]]) -> None:
    rel = rodadas[CHAVES[0]]
    sem = {**rel, "metricas": {**rel["metricas"], "opcoes_decisao": None}}
    assert "Efeito das opções de decisão" not in relatorio.markdown(sem)


def test_calcular_sem_recalculo() -> None:
    assert redecisao.calcular(None) is None
    assert metricas.calcular(metricas.Registros())["opcoes_decisao"] is None


def _resultado_real(gabarito: Path) -> tuple[ItemGabarito, ResultadoPipeline, Settings]:
    config = _config(gabarito, gabarito / "nao_usado")
    settings = Settings(_env_file=None, **config.settings)
    esteira = criar_esteira(settings)
    for item in ler_manifesto(gabarito / "manifesto.jsonl"):
        if item.particao != "ajuste" or item.variante_de is not None:
            continue
        resultado = asyncio.run(_processar(esteira, gabarito, item, config.modo))
        if resultado.resposta is not None:
            return item, resultado, settings
    raise AssertionError("nenhum documento com resposta 200")


def test_divergencia_com_a_rodada_aparece(gabarito: Path) -> None:
    item, resultado, settings = _resultado_real(gabarito)
    registros = metricas.Registros()
    redecisao.registrar(registros, item, resultado, settings)
    assert registros.opcoes_decisao is not None
    assert registros.opcoes_decisao.divergencias == []
    assert all(len(r.documentos) == 1 for r in registros.opcoes_decisao.por_opcao.values())

    # Uma resposta que a decisão não daria com esses intermediários é apontada.
    assert resultado.resposta is not None
    outro = (
        StatusDocumento.REVISAO
        if resultado.resposta.status is StatusDocumento.APROVADO
        else StatusDocumento.APROVADO
    )
    adulterado = ResultadoPipeline(
        rastro=resultado.rastro, resposta=resultado.resposta.model_copy(update={"status": outro})
    )
    registros = metricas.Registros()
    redecisao.registrar(registros, item, adulterado, settings)
    assert registros.opcoes_decisao is not None
    assert registros.opcoes_decisao.divergencias == [item.sha256[:12]]


def test_redecidir_reproduz_a_resposta(gabarito: Path) -> None:
    _, resultado, settings = _resultado_real(gabarito)
    novo = redecisao.redecidir(
        resultado, redecisao.Opcao.de(settings), settings.sinalizacao_captura_fonte
    )
    assert novo.resposta == resultado.resposta
    assert novo.rastro is resultado.rastro


def test_documento_que_parou_antes_da_decisao_nao_muda(tmp_path: Path) -> None:
    esteira = criar_esteira(Settings(_env_file=None, leitor_visual_provider="fake"))
    rastro = esteira.novo_rastro(str(uuid.uuid4()), TipoDocumento.CPF, None)
    resultado = ResultadoPipeline(
        rastro=rastro, falha=FalhaExtracao.de(MotivoErro.NENHUM_DOCUMENTO)
    )
    assert not redecisao.chegou_a_decisao(resultado)
    for opcao in redecisao.OPCOES:
        assert redecisao.redecidir(resultado, opcao, "vlm") is resultado
