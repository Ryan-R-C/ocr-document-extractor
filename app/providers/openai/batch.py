"""Rodada Batch da OpenAI para o avaliador (metade do preço, assíncrona, até 24 h).

`ColetorBatch` implementa a mesma interface `LeitorVisual`: a esteira roda igual, mas cada
chamada vira uma linha de um lote. Quando nenhuma chamada nova chega por alguns segundos,
o lote é enviado (`/v1/responses`), acompanhado e, ao terminar, cada esteira recebe sua
resposta. No desenho em duas etapas isso gera dois lotes: classificação e extração.

Retomada: o estado (lotes enviados e respostas recebidas, por hash do corpo da requisição)
fica em disco. Rodar o avaliador de novo com o mesmo diretório reaproveita as respostas
prontas e volta a acompanhar os lotes ainda em andamento, sem reenviar nada. Requisições
que falharam (ou de lotes expirados/cancelados) são reenviadas na rodada seguinte.

O estado em disco contém respostas com dados do gabarito: fica em `saidas/`, fora do git.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.providers.interfaces import ErroProvider, PedidoVisual, RespostaVisual
from app.providers.openai.adaptador import LeitorVisualOpenAI

FINAIS = {"completed", "failed", "expired", "cancelled"}


@dataclass
class _Estado:
    diretorio: Path
    respostas: dict[str, dict[str, Any]] = field(default_factory=dict)
    # id do lote → chaves das requisições dentro dele
    lotes: dict[str, list[str]] = field(default_factory=dict)

    @property
    def _arquivo_respostas(self) -> Path:
        return self.diretorio / "respostas.jsonl"

    @property
    def _arquivo_lotes(self) -> Path:
        return self.diretorio / "lotes.json"

    def carregar(self) -> None:
        self.diretorio.mkdir(parents=True, exist_ok=True)
        if self._arquivo_respostas.is_file():
            for linha in self._arquivo_respostas.read_text(encoding="utf-8").splitlines():
                if linha.strip():
                    registro = json.loads(linha)
                    self.respostas[registro["chave"]] = registro["resultado"]
        if self._arquivo_lotes.is_file():
            self.lotes = json.loads(self._arquivo_lotes.read_text(encoding="utf-8"))

    def guardar_resposta(self, chave: str, resultado: dict[str, Any]) -> None:
        self.respostas[chave] = resultado
        with self._arquivo_respostas.open("a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps({"chave": chave, "resultado": resultado}) + "\n")

    def guardar_lotes(self) -> None:
        self._arquivo_lotes.write_text(json.dumps(self.lotes, indent=2), encoding="utf-8")


def chave_requisicao(corpo: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(corpo, sort_keys=True).encode()).hexdigest()


class ColetorBatch:
    nome = "openai"

    def __init__(
        self,
        adaptador: LeitorVisualOpenAI,
        diretorio: Path,
        cliente: AsyncOpenAI | None = None,
        ociosidade_s: float = 3.0,
        intervalo_s: float = 30.0,
    ) -> None:
        self.adaptador = adaptador
        self.cliente = cliente or adaptador.cliente
        self.estado = _Estado(diretorio)
        self.estado.carregar()
        self.ociosidade_s = ociosidade_s
        self.intervalo_s = intervalo_s
        self._pendentes: dict[str, tuple[dict[str, Any], list[asyncio.Future[dict[str, Any]]]]] = {}
        self._em_lote: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}
        self._sinal = asyncio.Event()
        self._tarefa: asyncio.Task[None] | None = None
        self._acompanhando: set[str] = set()

    async def ler(self, pedido: PedidoVisual) -> RespostaVisual:
        corpo = self.adaptador.montar_requisicao(pedido)
        chave = chave_requisicao(corpo)
        if chave in self.estado.respostas:
            return self._interpretar(self.estado.respostas[chave], pedido)
        futuro: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        lote_anterior = self._lote_de(chave)
        if lote_anterior is not None:
            # Retomada: a requisição já está num lote enviado antes.
            self._em_lote.setdefault(chave, []).append(futuro)
            self._acompanhar(lote_anterior)
        else:
            self._pendentes.setdefault(chave, (corpo, []))[1].append(futuro)
            self._sinal.set()
            if self._tarefa is None or self._tarefa.done():
                self._tarefa = asyncio.create_task(self._enviar_quando_ocioso())
        return self._interpretar(await futuro, pedido)

    def _interpretar(self, resultado: dict[str, Any], pedido: PedidoVisual) -> RespostaVisual:
        if resultado.get("erro"):
            raise ErroProvider(f"batch: {resultado['erro']}")
        return self.adaptador.interpretar_resposta(resultado["corpo"], pedido)

    def _lote_de(self, chave: str) -> str | None:
        return next((lote for lote, chaves in self.estado.lotes.items() if chave in chaves), None)

    async def _enviar_quando_ocioso(self) -> None:
        while True:
            self._sinal.clear()
            try:
                await asyncio.wait_for(self._sinal.wait(), timeout=self.ociosidade_s)
            except TimeoutError:
                break
        pendentes, self._pendentes = self._pendentes, {}
        if not pendentes:
            return
        linhas = [
            json.dumps(
                {"custom_id": chave, "method": "POST", "url": "/v1/responses", "body": corpo}
            )
            for chave, (corpo, _) in pendentes.items()
        ]
        try:
            arquivo = await self.cliente.files.create(
                file=("lote.jsonl", io.BytesIO("\n".join(linhas).encode()), "application/jsonl"),
                purpose="batch",
            )
            lote = await self.cliente.batches.create(
                input_file_id=arquivo.id, endpoint="/v1/responses", completion_window="24h"
            )
        except Exception as erro:  # o envio falhou: todas as esteiras do lote recebem a falha
            for _, futuros in pendentes.values():
                for futuro in futuros:
                    futuro.set_exception(
                        ErroProvider(f"batch: envio falhou ({type(erro).__name__})")
                    )
            return
        self.estado.lotes[lote.id] = list(pendentes)
        self.estado.guardar_lotes()
        for chave, (_, futuros) in pendentes.items():
            self._em_lote.setdefault(chave, []).extend(futuros)
        self._acompanhar(lote.id)

    def _acompanhar(self, lote_id: str) -> None:
        if lote_id not in self._acompanhando:
            self._acompanhando.add(lote_id)
            asyncio.get_running_loop().create_task(self._acompanhar_lote(lote_id))

    async def _acompanhar_lote(self, lote_id: str) -> None:
        while True:
            lote = await self.cliente.batches.retrieve(lote_id)
            if lote.status in FINAIS:
                break
            await asyncio.sleep(self.intervalo_s)
        falhas: dict[str, dict[str, Any]] = {}
        for arquivo_id in (lote.output_file_id, lote.error_file_id):
            if not arquivo_id:
                continue
            conteudo = await self.cliente.files.content(arquivo_id)
            for linha in conteudo.text.splitlines():
                if not linha.strip():
                    continue
                registro = json.loads(linha)
                resposta = registro.get("response") or {}
                if registro.get("error") or resposta.get("status_code", 200) >= 400:
                    # Falhas não ficam guardadas: a próxima rodada reenvia a requisição.
                    falhas[registro["custom_id"]] = {
                        "erro": f"status {resposta.get('status_code', '?')}"
                    }
                else:
                    self.estado.guardar_resposta(
                        registro["custom_id"], {"corpo": resposta.get("body", {})}
                    )
        chaves = self.estado.lotes.pop(lote_id, [])
        self.estado.guardar_lotes()
        for chave in chaves:
            resultado = self.estado.respostas.get(chave) or falhas.get(
                chave, {"erro": f"lote {lote.status}"}
            )
            for futuro in self._em_lote.pop(chave, []):
                if not futuro.done():
                    futuro.set_result(resultado)
