"""Providers fake determinísticos: devolvem respostas gravadas por hash do arquivo recebido.

Servem para os testes e para o avaliador rodar sem custo. Imagem sem gravação devolve
documento NAO_IDENTIFICADO e nenhum campo: o fake nunca inventa valor. As caixas gravadas
são relativas à imagem preparada (documento recortado e redimensionado).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from app.api.contrato import UF, Lado, StatusLeitura, Subtipo
from app.config import Settings
from app.providers.custo import calcular_custo
from app.providers.fake import gravacoes
from app.providers.fake.gravacoes import CampoGravado, Falha, Gravacao, LeituraVisualGravada
from app.providers.interfaces import (
    Caixa,
    ErroProvider,
    EtapaVisual,
    Imagem,
    LeituraTexto,
    LeituraVisual,
    Localizacao,
    MetricasChamada,
    ModoChamada,
    Palavra,
    PedidoVisual,
    QualidadeCaptura,
    RespostaVisual,
    Rotacao,
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
    """Monta o JSON que um provider real devolveria para cada etapa, a partir da gravação.

    Sem gravação: documento NAO_IDENTIFICADO na leitura e na classificação; na extração e
    na releitura, todos os campos ILEGIVEL. Nunca inventa valor.
    """

    async def ler(self, pedido: PedidoVisual) -> RespostaVisual:
        gravacao = self._gravacao(pedido.imagem.sha256_origem)
        chave: Literal["releitura", "leitura_visual"] = (
            "releitura" if pedido.etapa is EtapaVisual.RELEITURA else "leitura_visual"
        )
        await self._simular_falha(gravacao.falhas.get(chave) if gravacao else None)
        lv = gravacao.leitura_visual if gravacao else None
        modelo = (lv.modelo if lv else None) or pedido.modelo
        entrada, saida, latencia = (
            (lv.tokens_entrada, lv.tokens_saida, lv.latencia_ms) if lv else (0, 0, 0.0)
        )

        dados: dict[str, Any]
        if pedido.etapa is EtapaVisual.LEITURA:
            dados = {**self._classificacao(lv), "documento": self._documento(lv)}
        elif pedido.etapa is EtapaVisual.CLASSIFICACAO:
            dados = {
                **self._classificacao(lv),
                "subtipo": lv.subtipo.value if lv else "NAO_IDENTIFICADO",
            }
            modelo = pedido.modelo
            if lv:
                entrada, saida, latencia = (
                    lv.classificacao_tokens_entrada,
                    lv.classificacao_tokens_saida,
                    lv.classificacao_latencia_ms,
                )
        elif pedido.etapa is EtapaVisual.EXTRACAO:
            nomes = list(pedido.schema["properties"]["campos"]["properties"])
            gravados = lv.campos if lv and lv.subtipo is pedido.subtipo else {}
            dados = {"campos": {n: self._campo(gravados.get(n)) for n in nomes}}
        else:
            r = gravacao.releituras.get(pedido.campo or "") if gravacao else None
            dados = self._campo(r)
            modelo = (r.modelo if r else None) or pedido.modelo
            entrada, saida, latencia = (
                (r.tokens_entrada, r.tokens_saida, r.latencia_ms) if r else (0, 0, 0.0)
            )

        return RespostaVisual(
            dados=dados,
            resposta_bruta={"fake": True, "dados": dados},
            metricas=self._metricas(
                pedido.etapa.value, modelo, entrada, saida, latencia, pedido.modo
            ),
        )

    @staticmethod
    def _campo(gravado: CampoGravado | None) -> dict[str, str]:
        if gravado is None:
            return {"status": StatusLeitura.ILEGIVEL.value, "rotulo_visto": "", "texto_literal": ""}
        return {
            "status": gravado.status.value,
            "rotulo_visto": gravado.rotulo_visto,
            "texto_literal": gravado.texto_literal,
        }

    @staticmethod
    def _classificacao(lv: LeituraVisualGravada | None) -> dict[str, str]:
        if lv is None:
            return {
                "tipo_documento_detectado": "NAO_IDENTIFICADO",
                "uf": UF.NAO_IDENTIFICADO.value,
                "lado": Lado.NAO_IDENTIFICADO.value,
                "qualidade_captura": QualidadeCaptura.NAO_IDENTIFICADO.value,
                "rotacao": Rotacao.NAO_IDENTIFICADO.value,
                "modelo_documento": "",
            }
        return {
            "tipo_documento_detectado": lv.tipo_documento_detectado.value
            if lv.tipo_documento_detectado
            else "NAO_IDENTIFICADO",
            "uf": lv.uf.value,
            "lado": lv.lado.value,
            "qualidade_captura": lv.qualidade_captura.value,
            "rotacao": lv.rotacao.value,
            "modelo_documento": lv.modelo_documento,
        }

    def _documento(self, lv: LeituraVisualGravada | None) -> dict[str, object]:
        if lv is None:
            return {"subtipo": Subtipo.NAO_IDENTIFICADO.value, "campos": {}}
        return {
            "subtipo": lv.subtipo.value,
            "campos": {n: self._campo(c) for n, c in lv.campos.items()},
        }


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
