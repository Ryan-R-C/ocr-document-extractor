"""Avaliador ponta a ponta com os fakes, gabarito, partição, estratificação e campo apagado."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from app.api.contrato import StatusLeitura
from app.config import Settings
from app.providers.fake import gravacoes
from app.providers.fake.gravacoes import CampoGravado
from avaliacao import campo_apagado, estratificacao, metricas, sintetico
from avaliacao.cli import main_avaliar, main_gabarito
from avaliacao.executor import ConfigAvaliacao, ModoNaoImplementado, avaliar
from avaliacao.gabarito import ItemGabarito, escrever_manifesto, ler_manifesto
from avaliacao.importador import importar_csv
from avaliacao.particao import atribuir_particoes


@pytest.fixture(scope="module")
def gabarito(tmp_path_factory) -> Path:
    destino = tmp_path_factory.mktemp("gabarito")
    sintetico.gerar(destino, quantidade=24, semente=3)
    return destino


def _config(gabarito: Path, saida: Path, **extra) -> ConfigAvaliacao:
    dados = {
        "nome": "teste",
        "gabarito": gabarito / "manifesto.jsonl",
        "saida": saida,
        "settings": {
            "leitor_visual_provider": "fake",
            "leitor_texto_provider": "fake",
            "localizador_provider": "fake",
            "fake_gravacoes_dir": str(gabarito / "gravacoes"),
        },
    }
    dados.update(extra)
    return ConfigAvaliacao.model_validate(dados)


def _settings(config: ConfigAvaliacao) -> Settings:
    return Settings(_env_file=None, **config.settings)


def test_avaliador_ponta_a_ponta(gabarito: Path, tmp_path: Path) -> None:
    config = _config(gabarito, tmp_path)
    relatorio, pasta = asyncio.run(avaliar(config, "ajuste", settings=_settings(config)))

    assert (pasta / "relatorio.md").is_file()
    assert json.loads((pasta / "relatorio.json").read_text())["metricas"]
    itens = [i for i in ler_manifesto(gabarito / "manifesto.jsonl") if i.particao == "ajuste"]
    execucao, m = relatorio["execucao"], relatorio["metricas"]
    assert execucao["itens"] == len(itens)
    assert execucao["variantes"] > 0
    assert set(execucao["stubs_ativos"]) == {"ancoragem", "releitura", "regras"}

    geral = m["geral"]
    assert geral["documentos"] == len(itens)
    # Stubs: nada aprovado, logo nenhum erro escapado possível.
    assert geral["desfechos"].get("APROVADO", 0) == 0
    assert geral["criticos_aprovados"] == 0
    assert m["metas"]["alucinacao_criticos_aprovados"]["atingida"] is True
    # Custo do fake: 3420 tokens de entrada e 600 de saída no gpt-6.1-terra.
    esperado = (3420 * 1 + 600 * 5) / 1e6 * 5.40
    docs_lidos = [d for d in geral["desfechos"]]
    assert docs_lidos
    assert geral["custo_brl"]["p95"] == pytest.approx(esperado)
    assert m["metas"]["custo_medio_brl"]["atingida"] is True
    for dimensao in ("subtipo", "uf", "qualidade", "campo"):
        assert m["por"][dimensao]
    assert "total" in geral["latencia_ms"] and "leituras" in geral["latencia_ms"]

    # Alucinações gravadas no gabarito sintético aparecem no relatório.
    origens = [i.origem for i in itens]
    if "sintetico:alucina_ausente" in origens:
        assert geral["alucinacoes"] >= 1
    # Variantes sem gravação viram 422 NENHUM_DOCUMENTO: sem valor, sem alucinação.
    assert m["campo_apagado"]["alucinacoes"] == 0


def test_avaliador_detecta_alucinacao_no_campo_apagado(gabarito: Path, tmp_path: Path) -> None:
    gabarito = Path(shutil.copytree(gabarito, tmp_path / "copia"))  # não suja o gabarito comum
    config = _config(gabarito, tmp_path)
    itens = [
        i
        for i in ler_manifesto(gabarito / "manifesto.jsonl")
        if i.particao == "ajuste" and i.origem == "sintetico:exato"
    ][:2]
    assert itens
    # Gera as mesmas variantes que o avaliador vai gerar e grava, para elas, um fake que
    # "lê" o valor original no campo apagado: isso tem de contar como alucinação.
    previa = asyncio.run(
        campo_apagado.gerar_variantes(itens, gabarito, tmp_path / "previa", ["borrar"])
    )
    for variante in previa.itens:
        original = next(i for i in itens if i.arquivo == variante.variante_de)
        gravacao = gravacoes.carregar(gabarito / "gravacoes", original.sha256)
        assert gravacao and gravacao.leitura_visual and variante.campo_apagado
        campos = dict(gravacao.leitura_visual.campos)
        campos[variante.campo_apagado] = CampoGravado(
            status=StatusLeitura.LEGIVEL,
            texto_literal=original.campos[variante.campo_apagado].valor or "",
        )
        lv = gravacao.leitura_visual.model_copy(update={"campos": campos})
        gravacoes.salvar(
            gabarito / "gravacoes",
            variante.sha256,
            gravacao.model_copy(update={"leitura_visual": lv}),
        )

    config.campo_apagado.metodos = ["borrar"]
    relatorio, _ = asyncio.run(avaliar(config, "ajuste", settings=_settings(config)))
    ca = relatorio["metricas"]["campo_apagado"]
    assert ca["alucinacoes"] >= len(previa.itens)
    assert ca["alucinacoes_em_aprovados"] == 0  # stubs não aprovam


def test_variantes_sao_deterministicas_e_nao_tocam_originais(
    gabarito: Path, tmp_path: Path
) -> None:
    itens = ler_manifesto(gabarito / "manifesto.jsonl")[:4]
    antes = {i.arquivo: (gabarito / i.arquivo).read_bytes() for i in itens}
    a = asyncio.run(campo_apagado.gerar_variantes(itens, gabarito, tmp_path / "a"))
    b = asyncio.run(campo_apagado.gerar_variantes(itens, gabarito, tmp_path / "b"))
    assert [v.sha256 for v in a.itens] == [v.sha256 for v in b.itens]
    assert {i.arquivo: (gabarito / i.arquivo).read_bytes() for i in itens} == antes
    for variante in a.itens:
        assert variante.campo_apagado is not None
        assert variante.campos[variante.campo_apagado].status in {"ILEGIVEL", "AUSENTE"}
        assert variante.sha256 != next(i.sha256 for i in itens if i.arquivo == variante.variante_de)


def test_localizador_por_ocr_acha_o_valor_sem_caixa(gabarito: Path, tmp_path: Path) -> None:
    from app.providers.fake import FakeLeitorTexto

    itens = [
        i for i in ler_manifesto(gabarito / "manifesto.jsonl") if i.origem == "sintetico:exato"
    ][:2]
    sem_caixa = [
        i.model_copy(
            update={
                "campos": {k: v.model_copy(update={"caixa": None}) for k, v in i.campos.items()}
            }
        )
        for i in itens
    ]
    settings = Settings(_env_file=None, fake_gravacoes_dir=str(gabarito / "gravacoes"))
    localizador = campo_apagado.LocalizadorValorPorOCR(FakeLeitorTexto(settings))
    sem = asyncio.run(campo_apagado.gerar_variantes(sem_caixa, gabarito, tmp_path / "s"))
    com = asyncio.run(
        campo_apagado.gerar_variantes(sem_caixa, gabarito, tmp_path / "c", localizador=localizador)
    )
    assert sem.itens == [] and sem.pulados_sem_caixa >= len(itens)
    assert len(com.itens) == 2 * len(itens)


def test_modo_batch_e_flex_previstos_mas_nao_implementados(gabarito: Path, tmp_path: Path) -> None:
    for modo in ("batch", "flex"):
        config = _config(gabarito, tmp_path, modo=modo)
        with pytest.raises(ModoNaoImplementado, match="Fase 1"):
            asyncio.run(avaliar(config, "ajuste", settings=_settings(config)))


def test_cli_avaliar(gabarito: Path, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
nome = "cli"
gabarito = "{(gabarito / "manifesto.jsonl").as_posix()}"
saida = "{(tmp_path / "saida").as_posix()}"
[settings]
leitor_visual_provider = "fake"
leitor_texto_provider = "fake"
localizador_provider = "fake"
fake_gravacoes_dir = "{(gabarito / "gravacoes").as_posix()}"
[campo_apagado]
ativo = false
""",
        encoding="utf-8",
    )
    assert main_avaliar(["--config", str(config), "--particao", "holdout"]) == 0
    [relatorio] = list((tmp_path / "saida").glob("cli-holdout-*/relatorio.md"))
    assert "Holdout." in relatorio.read_text(encoding="utf-8")
    assert main_avaliar(["--config", str(config), "--particao", "ajuste", "--modo", "batch"]) == 2


def test_manifesto_sem_particao_e_recusado(gabarito: Path, tmp_path: Path) -> None:
    itens = [
        i.model_copy(update={"particao": None}) for i in ler_manifesto(gabarito / "manifesto.jsonl")
    ]
    manifesto = gabarito / "sem_particao.jsonl"
    escrever_manifesto(manifesto, itens)
    config = _config(gabarito, tmp_path)
    config.gabarito = manifesto
    with pytest.raises(ValueError, match="sem partição"):
        asyncio.run(avaliar(config, "ajuste", settings=_settings(config)))


# --- Partição -----------------------------------------------------------------------------


def test_particao_deterministica_estratificada_e_estavel(gabarito: Path) -> None:
    itens = [
        i.model_copy(update={"particao": None}) for i in ler_manifesto(gabarito / "manifesto.jsonl")
    ]
    a = atribuir_particoes(itens)
    b = atribuir_particoes(list(reversed(itens)))
    por_arquivo = {i.arquivo: i.particao for i in b}
    assert all(por_arquivo[i.arquivo] == i.particao for i in a)
    holdout = sum(1 for i in a if i.particao == "holdout")
    assert holdout == int(len(itens) * 0.2 + 0.5)

    # Casos novos não movem os antigos.
    novos = [
        i.model_copy(update={"arquivo": f"novo_{n}.png", "sha256": f"{n:064x}", "particao": None})
        for n, i in enumerate(itens[:10])
    ]
    c = atribuir_particoes(a + novos)
    assert [i.particao for i in c[: len(a)]] == [i.particao for i in a]
    assert all(i.particao in {"ajuste", "holdout"} for i in c)


def test_particao_em_estrato_grande_segue_a_fracao() -> None:
    base = ler_manifesto_fake(50)
    resultado = atribuir_particoes(base)
    assert sum(1 for i in resultado if i.particao == "holdout") == 10


def ler_manifesto_fake(n: int) -> list[ItemGabarito]:
    return [
        ItemGabarito.model_validate(
            {
                "arquivo": f"x{k}.png",
                "sha256": f"{k:064x}",
                "tipo_documento": "CPF",
                "subtipo": "CPF_CARTAO",
                "uf": "SP",
                "lado": "FRENTE",
                "qualidade": "foto",
                "campos": {"cpf": {"status": "ILEGIVEL"}},
            }
        )
        for k in range(n)
    ]


# --- Estratificação, gabarito e importador ------------------------------------------------


def test_relatorio_de_estratificacao(gabarito: Path) -> None:
    itens = ler_manifesto(gabarito / "manifesto.jsonl")
    relatorio = estratificacao.gerar(itens, minimo=5)
    resumo = relatorio["resumo"]
    assert resumo["itens"] == len(itens)
    assert resumo["celulas_vazias"] > 0  # o sintético usa poucas UFs de propósito
    assert resumo["celulas_com_poucos"] > 0
    assert sum(c["total"] for c in relatorio["celulas"]) == len(itens)
    assert "CTPS_FISICA" in resumo["subtipos_sem_exemplo"]
    texto = estratificacao.markdown(relatorio)
    assert "## CPF_CARTAO" in texto and "| TO |" in texto


def test_manifesto_rejeita_inconsistencias() -> None:
    base = {
        "arquivo": "a.png",
        "sha256": "0" * 64,
        "tipo_documento": "CPF",
        "subtipo": "CPF_CARTAO",
        "uf": "SP",
        "lado": "FRENTE",
        "qualidade": "foto",
        "campos": {},
    }
    with pytest.raises(ValueError, match="não pertence"):
        ItemGabarito.model_validate({**base, "subtipo": "CNH"})
    with pytest.raises(ValueError, match="fora do subtipo"):
        ItemGabarito.model_validate({**base, "campos": {"numero_rg": {"valor": "1"}}})
    with pytest.raises(ValueError, match="precisa de valor"):
        ItemGabarito.model_validate({**base, "campos": {"cpf": {"status": "LEGIVEL"}}})
    with pytest.raises(ValueError, match="não pode ter valor"):
        ItemGabarito.model_validate(
            {**base, "campos": {"cpf": {"status": "AUSENTE", "valor": "1"}}}
        )


def test_importador_csv_nao_altera_originais(tmp_path: Path) -> None:
    from tests.conftest import cpf_sintetico, imagem_png

    imagens = tmp_path / "imgs"
    imagens.mkdir()
    (imagens / "a.png").write_bytes(imagem_png(1))
    (imagens / "b.png").write_bytes(imagem_png(2))
    cpf = cpf_sintetico(5)
    csv = tmp_path / "gabarito.csv"
    conteudo_csv = (
        "arquivo;tipo_documento;subtipo;uf;lado;qualidade;campo:cpf;caixa:cpf;campo:nome\n"
        f"a.png;CPF;CPF_CARTAO;sp;frente;FOTO;{cpf};1,2,3,4;AUSENTE\n"
        "b.png;CPF;CNH;SP;FRENTE;foto;ILEGIVEL;;\n"
        "c.png;CPF;CPF_CARTAO;SP;FRENTE;foto;;;\n"
    )
    csv.write_text(conteudo_csv, encoding="utf-8")
    itens, problemas = importar_csv(csv, imagens)
    assert len(itens) == 1
    assert itens[0].campos["cpf"].valor == cpf and itens[0].campos["cpf"].caixa == (1, 2, 3, 4)
    assert itens[0].campos["nome"].status == "AUSENTE"
    assert len(problemas) == 2
    assert cpf not in " ".join(problemas)
    assert csv.read_text(encoding="utf-8") == conteudo_csv

    saida = tmp_path / "manifesto.jsonl"
    assert (
        main_gabarito(
            ["importar-csv", "--csv", str(csv), "--imagens", str(imagens), "--saida", str(saida)]
        )
        == 1
    )
    assert (
        main_gabarito(
            ["importar-csv", "--csv", str(csv), "--imagens", str(imagens), "--saida", str(csv)]
        )
        == 2
    )
    assert main_gabarito(["particionar", "--manifesto", str(saida)]) == 0
    assert all(i.particao for i in ler_manifesto(saida))


def test_cli_gabarito(tmp_path: Path) -> None:
    destino = tmp_path / "sint"
    assert main_gabarito(["sintetico", "--saida", str(destino), "--quantidade", "6"]) == 0
    manifesto = destino / "manifesto.jsonl"
    assert main_gabarito(["validar", "--manifesto", str(manifesto)]) == 0
    assert (
        main_gabarito(
            ["estratificar", "--manifesto", str(manifesto), "--saida", str(tmp_path / "e")]
        )
        == 0
    )
    assert (tmp_path / "e" / "estratificacao.md").is_file()
    assert (
        main_gabarito(
            ["apagar-campos", "--manifesto", str(manifesto), "--saida", str(tmp_path / "v")]
        )
        == 0
    )
    assert ler_manifesto(tmp_path / "v" / "manifesto.jsonl")
    # Arquivo adulterado é detectado.
    primeiro = ler_manifesto(manifesto)[0]
    (destino / primeiro.arquivo).write_bytes(b"adulterado")
    assert main_gabarito(["validar", "--manifesto", str(manifesto)]) == 1


def test_percentil() -> None:
    assert metricas.percentil([], 95) is None
    assert metricas.percentil([1, 2, 3, 4], 50) == 2
    assert metricas.percentil(list(range(1, 101)), 95) == 95
