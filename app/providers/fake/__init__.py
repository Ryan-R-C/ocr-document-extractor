"""Providers fake determinísticos: devolvem respostas gravadas por hash do arquivo recebido.

Servem para os testes e para o avaliador rodar sem custo. Imagem sem gravação devolve
documento NAO_IDENTIFICADO e nenhum campo: o fake nunca inventa valor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.api.contrato import UF, Lado, StatusLeitura, Subtipo
from app.config import Settings
from app.providers.custo import calcular_custo
from app.providers.fake import gravacoes
from app.providers.fake.gravacoes import Falha, Gravacao
from app.providers.interfaces import (
    Caixa,
    CampoLido,
    ErroProvider,
    Imagem,
    LeituraTexto,
    LeituraVisual,
    Localizacao,
    MetricasChamada,
    ModoChamada,
    Palavra,
    PedidoLeituraVisual,
    PedidoReleitura,
    QualidadeCaptura,
    Releitura,
)

NOME = "fake"


class _BaseFake:
    nome = NOME

    def __init__(self, settings: Settings, diretorio: Path | None = None) -> None:
        self.settings = settings
        self.diretorio = Path(diretorio or settings.fake_gravacoes_dir)

    def _gravacao(self, sha256: str) -> Gravacao | None:
        return gravacoes.carregar(self.diretorio, sha256)

    @staticmethod
    async def _simular_falha(falha: Falha | None) -> None:
        if falha == "indisponivel":
            raise ErroProvider("falha simulada")
        if falha == "tempo":
            await asyncio.sleep(3600)  # o tempo limite da composição interrompe antes

    def _metricas(
        self, etapa: str, modelo: str, entrada: int, saida: int, latencia: float, modo: ModoChamada
    ) -> MetricasChamada:
        usd, brl = calcular_custo(self.settings, modelo, entrada, saida, modo)
        return MetricasChamada(
            provider=NOME,
            modelo=modelo,
            etapa=etapa,
            tokens_entrada=entrada,
            tokens_saida=saida,
            custo_usd=usd,
            custo_brl=brl,
            latencia_ms=latencia,
            modo=modo,
        )


class FakeLeitorVisual(_BaseFake):
    async def ler(self, pedido: PedidoLeituraVisual) -> LeituraVisual:
        gravacao = self._gravacao(pedido.imagem.sha256_origem)
        await self._simular_falha(gravacao.falhas.get("leitura_visual") if gravacao else None)
        lv = gravacao.leitura_visual if gravacao else None
        if lv is None:
            return LeituraVisual(
                subtipo=Subtipo.NAO_IDENTIFICADO,
                uf=UF.NAO_IDENTIFICADO,
                lado=Lado.NAO_IDENTIFICADO,
                tipo_documento_detectado=None,
                qualidade_captura=QualidadeCaptura.NAO_IDENTIFICADO,
                campos={},
                resposta_bruta={"gravacao": None},
                metricas=self._metricas("leitura_visual", pedido.modelo, 0, 0, 0.0, pedido.modo),
            )
        modelo = lv.modelo or pedido.modelo
        return LeituraVisual(
            subtipo=lv.subtipo,
            uf=lv.uf,
            lado=lv.lado,
            tipo_documento_detectado=lv.tipo_documento_detectado,
            qualidade_captura=lv.qualidade_captura,
            campos={
                nome: CampoLido(c.status, c.rotulo_visto, c.texto_literal)
                for nome, c in lv.campos.items()
            },
            resposta_bruta=lv.model_dump(mode="json"),
            metricas=self._metricas(
                "leitura_visual",
                modelo,
                lv.tokens_entrada,
                lv.tokens_saida,
                lv.latencia_ms,
                pedido.modo,
            ),
        )

    async def reler(self, pedido: PedidoReleitura) -> Releitura:
        gravacao = self._gravacao(pedido.recorte.sha256_origem)
        await self._simular_falha(gravacao.falhas.get("releitura") if gravacao else None)
        r = gravacao.releituras.get(pedido.campo) if gravacao else None
        if r is None:
            campo = CampoLido(StatusLeitura.ILEGIVEL, "", "")
            return Releitura(
                campo=campo,
                resposta_bruta={"gravacao": None},
                metricas=self._metricas("releitura", pedido.modelo, 0, 0, 0.0, pedido.modo),
            )
        modelo = r.modelo or pedido.modelo
        return Releitura(
            campo=CampoLido(r.status, r.rotulo_visto, r.texto_literal),
            resposta_bruta=r.model_dump(mode="json"),
            metricas=self._metricas(
                "releitura", modelo, r.tokens_entrada, r.tokens_saida, r.latencia_ms, pedido.modo
            ),
        )


class FakeLeitorTexto(_BaseFake):
    async def ler(self, imagem: Imagem) -> LeituraTexto:
        gravacao = self._gravacao(imagem.sha256_origem)
        await self._simular_falha(gravacao.falhas.get("leitura_texto") if gravacao else None)
        lt = gravacao.leitura_texto if gravacao else None
        palavras = [
            Palavra(p.id, p.texto, Caixa(*p.caixa), p.confianca)
            for p in (lt.palavras if lt else [])
        ]
        return LeituraTexto(
            palavras=palavras,
            resposta_bruta=lt.model_dump(mode="json") if lt else {"gravacao": None},
            metricas=MetricasChamada(
                provider=NOME,
                modelo="fake-ocr",
                etapa="leitura_texto",
                latencia_ms=lt.latencia_ms if lt else 0.0,
            ),
        )


class FakeLocalizador(_BaseFake):
    async def localizar(
        self,
        imagem: Imagem,
        leitura_visual: LeituraVisual,
        leitura_texto: LeituraTexto,
        campos: list[str],
    ) -> Localizacao:
        gravacao = self._gravacao(imagem.sha256_origem)
        caixas = gravacao.caixas if gravacao else {}
        return Localizacao(caixas={c: Caixa(*caixas[c]) for c in campos if c in caixas})
