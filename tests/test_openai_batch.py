"""Rodada Batch: envio, acompanhamento, retomada e falhas, com a API simulada."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI

from app.config import Settings
from app.providers.interfaces import ErroProvider, ModoChamada
from app.providers.openai import LeitorVisualOpenAI
from app.providers.openai.batch import ColetorBatch
from tests.test_openai import DADOS, pedido, resposta_gravada


class ApiBatch:
    """Simula /files, /batches e o conteúdo do arquivo de saída."""

    def __init__(self, status_lote: str = "completed", falhar: set[int] | None = None) -> None:
        self.status_lote = status_lote
        self.falhar = falhar or set()
        self.linhas_enviadas: list[dict[str, Any]] = []
        self.lotes_criados = 0
        self.consultas = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        caminho = request.url.path
        if request.method == "POST" and caminho.endswith("/files"):
            corpo = request.content.decode("utf-8", errors="ignore")
            for linha in corpo.splitlines():
                if linha.startswith("{"):
                    self.linhas_enviadas.append(json.loads(linha))
            return httpx2.Response(
                200,
                json={
                    "id": "file_entrada",
                    "object": "file",
                    "purpose": "batch",
                    "bytes": 1,
                    "created_at": 0,
                    "filename": "lote.jsonl",
                    "status": "processed",
                },
            )
        if request.method == "POST" and caminho.endswith("/batches"):
            self.lotes_criados += 1
            return httpx2.Response(200, json=self._lote("validating"))
        if request.method == "GET" and "/batches/" in caminho:
            self.consultas += 1
            return httpx2.Response(200, json=self._lote(self.status_lote))
        if request.method == "GET" and caminho.endswith("/content"):
            linhas = []
            for i, enviada in enumerate(self.linhas_enviadas):
                if i in self.falhar:
                    resposta = {"status_code": 500, "body": {"error": {"message": "x"}}}
                else:
                    resposta = {"status_code": 200, "body": resposta_gravada()}
                linhas.append(
                    json.dumps(
                        {
                            "id": f"r{i}",
                            "custom_id": enviada["custom_id"],
                            "response": resposta,
                            "error": None,
                        }
                    )
                )
            return httpx2.Response(200, content="\n".join(linhas).encode())
        return httpx2.Response(404, json={"error": {"message": caminho}})

    def _lote(self, status: str) -> dict[str, Any]:
        return {
            "id": "batch_1",
            "object": "batch",
            "endpoint": "/v1/responses",
            "input_file_id": "file_entrada",
            "completion_window": "24h",
            "status": status,
            "created_at": 0,
            "output_file_id": "file_saida" if status == "completed" else None,
            "error_file_id": None,
        }


def coletor(api: ApiBatch, diretorio: Path) -> ColetorBatch:
    cliente = AsyncOpenAI(
        api_key="sk-teste",
        base_url="https://api.teste/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
    )
    adaptador = LeitorVisualOpenAI(Settings(_env_file=None), cliente)
    return ColetorBatch(adaptador, diretorio, ociosidade_s=0.05, intervalo_s=0.01)


def _pedidos(n: int):
    return [pedido(modo=ModoChamada.BATCH, tamanho=(400 + i, 260)) for i in range(n)]


def test_lote_unico_para_chamadas_simultaneas(tmp_path: Path) -> None:
    api = ApiBatch()

    async def rodar():
        c = coletor(api, tmp_path)
        return await asyncio.gather(*(c.ler(p) for p in _pedidos(3)))

    respostas = asyncio.run(rodar())
    assert api.lotes_criados == 1
    assert len(api.linhas_enviadas) == 3
    linha = api.linhas_enviadas[0]
    assert linha["method"] == "POST" and linha["url"] == "/v1/responses"
    assert linha["body"]["text"]["format"]["strict"] is True
    assert all(r.dados == DADOS for r in respostas)
    # Preço de Batch: metade do padrão.
    assert respostas[0].metricas.custo_usd == pytest.approx((3420 * 1 + 600 * 5) / 1e6 * 0.5)


def test_retomada_reaproveita_respostas(tmp_path: Path) -> None:
    api = ApiBatch()

    async def primeira():
        c = coletor(api, tmp_path)
        return await asyncio.gather(*(c.ler(p) for p in _pedidos(2)))

    asyncio.run(primeira())

    outra = ApiBatch()

    async def de_novo():
        c = coletor(outra, tmp_path)
        return await asyncio.gather(*(c.ler(p) for p in _pedidos(2)))

    respostas = asyncio.run(de_novo())
    assert outra.lotes_criados == 0 and outra.consultas == 0
    assert all(r.dados == DADOS for r in respostas)


def test_retomada_acompanha_lote_em_andamento(tmp_path: Path) -> None:
    api = ApiBatch(status_lote="in_progress")

    async def interromper():
        c = coletor(api, tmp_path)
        tarefas = [asyncio.create_task(c.ler(p)) for p in _pedidos(2)]
        while api.lotes_criados == 0 or api.consultas < 2:  # noqa: ASYNC110 - simulação de API
            await asyncio.sleep(0.01)
        for t in tarefas:
            t.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)

    asyncio.run(interromper())
    assert json.loads((tmp_path / "lotes.json").read_text())  # lote registrado em disco

    # Processo novo: o lote terminou; nada é reenviado.
    api.status_lote = "completed"
    api_lotes_antes = api.lotes_criados

    async def retomar():
        c = coletor(api, tmp_path)
        return await asyncio.gather(*(c.ler(p) for p in _pedidos(2)))

    respostas = asyncio.run(retomar())
    assert api.lotes_criados == api_lotes_antes
    assert all(r.dados == DADOS for r in respostas)
    assert json.loads((tmp_path / "lotes.json").read_text()) == {}


def test_linha_com_erro_falha_so_aquela_esteira_e_e_reenviada_depois(tmp_path: Path) -> None:
    api = ApiBatch(falhar={1})

    async def rodar():
        c = coletor(api, tmp_path)
        return await asyncio.gather(*(c.ler(p) for p in _pedidos(2)), return_exceptions=True)

    resultados = asyncio.run(rodar())
    assert sum(isinstance(r, ErroProvider) for r in resultados) == 1
    outra = ApiBatch()

    async def de_novo():
        c = coletor(outra, tmp_path)
        return await asyncio.gather(*(c.ler(p) for p in _pedidos(2)))

    asyncio.run(de_novo())
    assert len(outra.linhas_enviadas) == 1  # só a que falhou volta para o lote


def test_lote_expirado_falha_e_libera_reenvio(tmp_path: Path) -> None:
    api = ApiBatch(status_lote="expired")
    with pytest.raises(ErroProvider):
        asyncio.run(coletor(api, tmp_path).ler(_pedidos(1)[0]))
    assert json.loads((tmp_path / "lotes.json").read_text()) == {}


def test_respostas_ficam_so_no_diretorio_de_estado(tmp_path: Path) -> None:
    api = ApiBatch()
    asyncio.run(coletor(api, tmp_path / "estado").ler(_pedidos(1)[0]))
    assert {p.name for p in (tmp_path / "estado").iterdir()} == {"respostas.jsonl", "lotes.json"}
