"""Adaptador OpenAI com respostas gravadas (transporte HTTP simulado do SDK).

Nada aqui chama a API real. O teste marcado `provider_real` só roda com a chave presente.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI

from app.api.contrato import Subtipo, TipoDocumento
from app.config import Settings
from app.providers.interfaces import (
    ErroProvider,
    EtapaVisual,
    Imagem,
    ModoChamada,
    PedidoVisual,
    TempoEsgotadoProvider,
)
from app.providers.openai import ImagemAcimaDoLimite, LeitorVisualOpenAI, ProviderMalConfigurado
from app.schemas.campos import schema_extracao, schema_leitura
from tests.conftest import imagem_png, sha256

DADOS = {"campos": {"cpf": {"status": "ILEGIVEL", "rotulo_visto": "", "texto_literal": ""}}}


def resposta_gravada(
    dados: dict[str, Any] | None = None,
    *,
    status: str = "completed",
    entrada: int = 3420,
    saida: int = 600,
    raciocinio: int = 0,
    recusa: bool = False,
    texto: str | None = None,
) -> dict[str, Any]:
    conteudo: dict[str, Any] = (
        {"type": "refusal", "refusal": "não posso"}
        if recusa
        else {
            "type": "output_text",
            "text": texto if texto is not None else json.dumps(dados or DADOS),
            "annotations": [],
        }
    )
    return {
        "id": "resp_teste",
        "object": "response",
        "created_at": 1_790_000_000,
        "status": status,
        "model": "gpt-6.1-terra-2026-09-01",
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [
            {
                "type": "message",
                "id": "msg_teste",
                "status": "completed",
                "role": "assistant",
                "content": [conteudo],
            }
        ],
        "usage": {
            "input_tokens": entrada,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": saida,
            "output_tokens_details": {"reasoning_tokens": raciocinio},
            "total_tokens": entrada + saida,
        },
    }


class Servidor:
    """Fila de respostas (status, corpo) e registro das requisições recebidas."""

    def __init__(self, *respostas: tuple[int, dict[str, Any]] | Exception) -> None:
        self.respostas = list(respostas)
        self.requisicoes: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requisicoes.append(json.loads(request.content))
        proxima = self.respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        status, corpo = proxima
        return httpx2.Response(status, json=corpo, headers={"x-request-id": "req_teste"})


def leitor(servidor: Servidor, **config: Any) -> tuple[LeitorVisualOpenAI, list[float]]:
    settings = Settings(_env_file=None, provider_espera_inicial_s=0.01, **config)
    cliente = AsyncOpenAI(
        api_key="sk-teste",
        base_url="https://api.teste/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(servidor)),
    )
    pausas: list[float] = []

    async def dormir(segundos: float) -> None:
        pausas.append(segundos)

    return LeitorVisualOpenAI(settings, cliente, dormir=dormir, aleatorio=lambda: 0.5), pausas


def pedido(
    etapa: EtapaVisual = EtapaVisual.EXTRACAO,
    modo: ModoChamada = ModoChamada.PADRAO,
    tamanho: tuple[int, int] = (400, 260),
) -> PedidoVisual:
    conteudo = imagem_png(1, tamanho=tamanho)
    imagem = Imagem(
        conteudo, "image/png", tamanho[0], tamanho[1], sha256(conteudo), sha256(conteudo)
    )
    schema = (
        schema_leitura(TipoDocumento.CPF)
        if etapa is EtapaVisual.LEITURA
        else schema_extracao(Subtipo.CPF_CARTAO)
    )
    return PedidoVisual(
        etapa,
        imagem,
        TipoDocumento.CPF,
        schema,
        "extracao_cpf",
        "PROMPT",
        "gpt-6.1-terra",
        modo,
        Subtipo.CPF_CARTAO,
    )


def test_requisicao_estruturada_estrita() -> None:
    servidor = Servidor((200, resposta_gravada()))
    adaptador, _ = leitor(servidor)
    resposta = asyncio.run(adaptador.ler(pedido()))
    [corpo] = servidor.requisicoes
    assert corpo["model"] == "gpt-6.1-terra"
    assert corpo["instructions"] == "PROMPT"
    assert corpo["store"] is False
    assert corpo["reasoning"] == {"effort": "none"}
    assert "service_tier" not in corpo
    imagem = corpo["input"][0]["content"][0]
    assert imagem["type"] == "input_image"
    assert imagem["detail"] == "original"
    assert imagem["image_url"].startswith("data:image/png;base64,")
    formato = corpo["text"]["format"]
    assert formato["type"] == "json_schema" and formato["strict"] is True
    assert formato["schema"] == schema_extracao(Subtipo.CPF_CARTAO)
    assert resposta.dados == DADOS


def test_custo_tokens_e_raciocinio() -> None:
    servidor = Servidor((200, resposta_gravada(entrada=3420, saida=700, raciocinio=100)))
    adaptador, _ = leitor(servidor)
    metricas = asyncio.run(adaptador.ler(pedido(tamanho=(1600, 1000)))).metricas
    assert metricas.provider == "openai"
    assert metricas.modelo == "gpt-6.1-terra"
    assert (metricas.tokens_entrada, metricas.tokens_saida, metricas.tokens_raciocinio) == (
        3420,
        700,
        100,
    )
    # Raciocínio é cobrado como saída (já incluído em output_tokens).
    assert metricas.custo_usd == pytest.approx((3420 * 1 + 700 * 5) / 1e6)
    assert metricas.custo_brl == pytest.approx(metricas.custo_usd * 5.40)
    assert metricas.tokens_imagem_estimados == 1920  # 1.600 patches × 1,2 (plano)


def test_flex_e_esforco_configuravel() -> None:
    servidor = Servidor((200, resposta_gravada()))
    adaptador, _ = leitor(
        servidor, leitor_visual_esforco_raciocinio="low", leitor_visual_detalhe="high"
    )
    metricas = asyncio.run(adaptador.ler(pedido(modo=ModoChamada.FLEX))).metricas
    corpo = servidor.requisicoes[0]
    assert corpo["service_tier"] == "flex"
    assert corpo["reasoning"] == {"effort": "low"}
    assert corpo["input"][0]["content"][0]["detail"] == "high"
    assert metricas.custo_usd == pytest.approx((3420 * 1 + 600 * 5) / 1e6 * 0.5)


def test_sem_esforco_omite_parametro() -> None:
    servidor = Servidor((200, resposta_gravada()))
    adaptador, _ = leitor(servidor, leitor_visual_esforco_raciocinio=None)
    asyncio.run(adaptador.ler(pedido()))
    assert "reasoning" not in servidor.requisicoes[0]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_nova_tentativa_em_429_e_5xx(status) -> None:
    servidor = Servidor(
        (status, {"error": {"message": "x"}}),
        (status, {"error": {"message": "x"}}),
        (200, resposta_gravada()),
    )
    adaptador, pausas = leitor(servidor)
    resposta = asyncio.run(adaptador.ler(pedido()))
    assert resposta.metricas.tentativas == 3
    assert len(servidor.requisicoes) == 3
    assert pausas == pytest.approx([0.01, 0.02])  # exponencial (jitter fixado em 1,0×)


def test_desiste_depois_das_tentativas() -> None:
    servidor = Servidor(*[(503, {"error": {"message": "x"}})] * 4)
    adaptador, _ = leitor(servidor)
    with pytest.raises(ErroProvider) as erro:
        asyncio.run(adaptador.ler(pedido()))
    assert not isinstance(erro.value, TempoEsgotadoProvider)
    assert len(servidor.requisicoes) == 4


def test_erro_4xx_nao_tenta_de_novo() -> None:
    servidor = Servidor((400, {"error": {"message": "schema inválido"}}))
    adaptador, _ = leitor(servidor)
    with pytest.raises(ErroProvider):
        asyncio.run(adaptador.ler(pedido()))
    assert len(servidor.requisicoes) == 1


def test_timeout_vira_tempo_esgotado() -> None:
    erro = httpx2.ReadTimeout("lento")
    servidor = Servidor(erro, erro, erro, erro)
    adaptador, _ = leitor(servidor)
    with pytest.raises(TempoEsgotadoProvider):
        asyncio.run(adaptador.ler(pedido()))


@pytest.mark.parametrize(
    "resposta",
    [
        resposta_gravada(status="incomplete"),
        resposta_gravada(recusa=True),
        resposta_gravada(texto="{nao e json"),
        resposta_gravada(texto="[1, 2]"),
    ],
)
def test_resposta_nao_utilizavel_e_falha_do_provider(resposta) -> None:
    adaptador, _ = leitor(Servidor((200, resposta)))
    with pytest.raises(ErroProvider):
        asyncio.run(adaptador.ler(pedido()))


def test_imagem_acima_do_limite_e_recusada_antes_de_enviar() -> None:
    servidor = Servidor()
    adaptador, _ = leitor(
        servidor,
        modelos_imagem={
            "gpt-6.1-terra": {"patch_px": 32, "multiplicador": 1.0, "patches_max": 100}
        },
    )
    with pytest.raises(ImagemAcimaDoLimite):
        asyncio.run(adaptador.ler(pedido(tamanho=(800, 600))))
    assert servidor.requisicoes == []


def test_sem_chave_falha_na_montagem() -> None:
    with pytest.raises(ProviderMalConfigurado):
        LeitorVisualOpenAI(Settings(_env_file=None))


def test_esteira_ponta_a_ponta_com_openai_gravado(settings, monkeypatch) -> None:
    """A esteira inteira com o adaptador OpenAI e resposta gravada: nunca aprova (sem OCR)."""
    import base64

    from app.api.contrato import RequisicaoExtracao, StatusDocumento
    from app.core.pipeline import Esteira
    from app.providers import fabrica

    dados = {
        "tipo_documento_detectado": "CPF",
        "uf": "SP",
        "lado": "FRENTE",
        "qualidade_captura": "FOTO",
        "rotacao": "0",
        "modelo_documento": "cartao",
        "documento": {
            "subtipo": "CPF_CARTAO",
            "campos": {
                "cpf": {
                    "status": "LEGIVEL",
                    "rotulo_visto": "CPF",
                    "texto_literal": "111.444.777-35",
                },
                "nome": {"status": "AUSENTE", "rotulo_visto": "", "texto_literal": ""},
                "data_nascimento": {"status": "AUSENTE", "rotulo_visto": "", "texto_literal": ""},
            },
        },
    }
    servidor = Servidor((200, resposta_gravada(dados)))
    adaptador, _ = leitor(servidor)
    config = settings.model_copy(
        update={
            "leitor_visual_provider": "openai",
            "leitor_texto_provider": "nenhum",
            "localizador_provider": "ocr",
        }
    )
    monkeypatch.setattr(fabrica, "_visual", lambda nome, s: adaptador)
    esteira = Esteira(config, fabrica.criar_dependencias(config))
    conteudo = imagem_png(9)
    resultado = asyncio.run(
        esteira.processar(
            "id",
            RequisicaoExtracao(
                tipo_documento="CPF", imagem_base64=base64.b64encode(conteudo).decode()
            ),
        )
    )
    assert resultado.resposta is not None
    assert resultado.resposta.status is StatusDocumento.REVISAO
    assert resultado.resposta.campos["cpf"].valor_normalizado == "11144477735"
    assert "+openai:gpt-6.1-terra+" in resultado.resposta.versao_pipeline
    assert "+ocr:nenhum+" in resultado.resposta.versao_pipeline


@pytest.mark.provider_real
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="sem OPENAI_API_KEY")
def test_api_real_responde_no_schema() -> None:  # pragma: no cover - só com chave
    """Imagem sintética (sem dado real): confere que a API aceita o schema estrito."""
    from pydantic import SecretStr

    settings = Settings(openai_api_key=SecretStr(os.environ["OPENAI_API_KEY"]))
    resposta = asyncio.run(LeitorVisualOpenAI(settings).ler(pedido(EtapaVisual.LEITURA)))
    assert "documento" in resposta.dados
