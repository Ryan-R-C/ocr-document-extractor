"""Composição de providers (tempo limite, fallback, limites), custo e isolamento do núcleo."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from app.api.contrato import Subtipo, TipoDocumento
from app.config import LimiteProvider, Settings
from app.providers.composicao import LeitorVisualComposto
from app.providers.custo import PrecoDesconhecido, calcular_custo
from app.providers.fabrica import ProviderNaoImplementado, criar_dependencias
from app.providers.interfaces import (
    ErroProvider,
    EtapaVisual,
    Imagem,
    MetricasChamada,
    ModoChamada,
    PedidoVisual,
    RespostaVisual,
    TempoEsgotadoProvider,
)
from app.providers.limites import BaldeTokens, LimitadorProvider

RAIZ = Path(__file__).resolve().parents[1]

IMAGEM = Imagem(b"", "image/png", 1, 1, "a", "a")
PEDIDO = PedidoVisual(EtapaVisual.LEITURA, IMAGEM, TipoDocumento.CPF, {}, "teste", "", "m")


class Leitor:
    def __init__(self, nome: str, comportamento: str = "ok", tokens: int = 100) -> None:
        self.nome = nome
        self.comportamento = comportamento
        self.chamadas = 0
        self.tokens = tokens

    async def ler(self, pedido):
        self.chamadas += 1
        if self.comportamento == "erro":
            raise ErroProvider("x")
        if self.comportamento == "lento":
            await asyncio.sleep(5)
        return RespostaVisual(
            {}, {}, MetricasChamada(self.nome, "m", "leitura_visual", tokens_entrada=self.tokens)
        )


@pytest.mark.anyio
async def test_fallback_quando_principal_falha() -> None:
    principal, reserva = Leitor("a", "erro"), Leitor("b")
    leitor = LeitorVisualComposto(principal, reserva, 1.0, {})
    resultado = await leitor.ler(PEDIDO)
    assert resultado.metricas.provider == "b"
    assert principal.chamadas == reserva.chamadas == 1


@pytest.mark.anyio
async def test_fallback_quando_principal_estoura_tempo() -> None:
    leitor = LeitorVisualComposto(Leitor("a", "lento"), Leitor("b"), 0.05, {})
    assert (await leitor.ler(PEDIDO)).metricas.provider == "b"


@pytest.mark.anyio
async def test_sem_fallback_tempo_vira_tempo_esgotado() -> None:
    leitor = LeitorVisualComposto(Leitor("a", "lento"), None, 0.05, {})
    with pytest.raises(TempoEsgotadoProvider):
        await leitor.ler(PEDIDO)


@pytest.mark.anyio
async def test_ambos_falham() -> None:
    leitor = LeitorVisualComposto(Leitor("a", "erro"), Leitor("b", "erro"), 1.0, {})
    with pytest.raises(ErroProvider) as erro:
        await leitor.ler(PEDIDO)
    assert not isinstance(erro.value, TempoEsgotadoProvider)


@pytest.mark.anyio
async def test_limite_de_concorrencia() -> None:
    ativos, pico = 0, 0

    class Contador(Leitor):
        async def ler(self, pedido):
            nonlocal ativos, pico
            ativos += 1
            pico = max(pico, ativos)
            await asyncio.sleep(0.02)
            ativos -= 1
            return await super().ler(pedido)

    limitador = LimitadorProvider(LimiteProvider(concorrencia=2, tokens_por_minuto=10**9))
    leitor = LeitorVisualComposto(Contador("a"), None, 2.0, {"a": limitador})
    await asyncio.gather(*(leitor.ler(PEDIDO) for _ in range(8)))
    assert pico == 2


@pytest.mark.anyio
async def test_balde_de_tokens_espera_e_acerta() -> None:
    agora = [0.0]
    balde = BaldeTokens(600, relogio=lambda: agora[0])  # 10 tokens/s
    await balde.reservar(600)
    assert balde.disponivel == 0
    balde.acertar(reservado=600, usado=100)  # devolve a sobra da estimativa
    assert balde.disponivel == 500
    agora[0] = 30.0
    balde._repor()
    assert balde.disponivel == 600  # não passa da capacidade


def test_custo_por_modelo_e_modo() -> None:
    s = Settings(_env_file=None)
    usd, brl = calcular_custo(s, "gpt-6.1-terra", 3420, 600)
    assert usd == pytest.approx(0.00642)
    assert brl == pytest.approx(0.00642 * 5.40)
    usd_batch, _ = calcular_custo(s, "gpt-6.1-terra", 3420, 600, ModoChamada.BATCH)
    assert usd_batch == pytest.approx(usd / 2)
    with pytest.raises(PrecoDesconhecido):
        calcular_custo(s, "modelo-sem-preco", 1, 1)


def test_provider_nao_implementado_falha_cedo() -> None:
    with pytest.raises(ProviderNaoImplementado, match="Fase 4"):
        criar_dependencias(
            Settings(
                _env_file=None, leitor_visual_provider="fake", leitor_texto_provider="tesseract"
            )
        )


def test_somente_providers_importam_sdk() -> None:
    """Invariante 8: só app/providers/<provider>/ importa SDK de provider."""
    sdks = re.compile(
        r"^\s*(from|import)\s+(openai|anthropic|paddleocr|paddle|google|azure)\b", re.M
    )
    for arquivo in [*RAIZ.joinpath("app").rglob("*.py"), *RAIZ.joinpath("avaliacao").rglob("*.py")]:
        relativo = arquivo.relative_to(RAIZ).as_posix()
        if relativo.startswith("app/providers/") and relativo.count("/") >= 3:
            continue
        assert not sdks.search(arquivo.read_text(encoding="utf-8")), relativo


def test_nucleo_nao_conhece_providers_concretos() -> None:
    for pasta in ("core", "regras", "schemas", "preprocess"):
        for arquivo in RAIZ.joinpath("app", pasta).rglob("*.py"):
            texto = arquivo.read_text(encoding="utf-8")
            assert "app.providers.fake" not in texto, arquivo
            assert "app.providers.fabrica" not in texto, arquivo


@pytest.mark.parametrize("etapa", list(EtapaVisual))
def test_fake_sem_gravacao_nunca_gera_valor(settings, etapa) -> None:
    from app.providers.fake import FakeLeitorVisual
    from app.schemas.campos import schema_extracao

    pedido = PedidoVisual(
        etapa,
        IMAGEM,
        TipoDocumento.CPF,
        schema_extracao(Subtipo.CPF_CARTAO),
        "teste",
        "",
        "m",
        subtipo=Subtipo.CPF_CARTAO,
        campo="cpf",
    )
    resposta = asyncio.run(FakeLeitorVisual(settings).ler(pedido))
    texto = str(resposta.dados)
    assert "LEGIVEL'" not in texto.replace("ILEGIVEL'", "")
    if etapa in (EtapaVisual.LEITURA, EtapaVisual.CLASSIFICACAO):
        assert "NAO_IDENTIFICADO" in texto
