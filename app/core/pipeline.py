"""Esteira de extração, independente do FastAPI.

Etapas: pré-processamento → leituras em paralelo (VLM ∥ OCR) → ancoragem → localização e
recortes → releitura dirigida → regras → decisão. O mesmo código roda na API e no avaliador.

Na Fase 0, qualidade, ancoragem, releitura e regras são stubs que nunca levam a APROVADO.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.api.contrato import (
    Documento,
    MotivoErro,
    RequisicaoExtracao,
    RespostaExtracao,
    TipoDocumento,
)
from app.config import Settings
from app.core import ancoragem as mod_ancoragem
from app.core import decisao as mod_decisao
from app.core import releitura as mod_releitura
from app.core.ancoragem import ResultadoAncoragem
from app.core.decisao import Decisao
from app.core.erros import (
    FalhaExtracao,
    FalhaProcessamento,
    FalhaProviderIndisponivel,
    FalhaTempoEsgotado,
)
from app.core.releitura import ResultadoReleitura
from app.core.versao import versao_pipeline
from app.preprocess import decodificacao, qualidade
from app.preprocess.decodificacao import ArquivoRecebido
from app.prompts import carregar as carregar_prompt
from app.providers.interfaces import (
    Caixa,
    Dependencias,
    ErroProvider,
    Imagem,
    LeituraTexto,
    LeituraVisual,
    MetricasChamada,
    ModoChamada,
    PedidoLeituraVisual,
    TempoEsgotadoProvider,
)
from app.regras import motor
from app.regras.motor import ResultadoValidacao
from app.schemas.campos import schema_leitura


@dataclass
class Rastro:
    """Tudo o que entra no pacote de auditoria. Contém dados pessoais: nunca vai para log."""

    id_processamento: str
    tipo_documento: TipoDocumento
    id_externo: str | None
    recebido_em: datetime
    versao_pipeline: str
    concluido_em: datetime | None = None
    arquivo: ArquivoRecebido | None = None
    imagem_normalizada: Imagem | None = None
    paginas_pdf: int | None = None
    qualidade: qualidade.AvaliacaoQualidade | None = None
    leitura_visual: LeituraVisual | None = None
    leitura_texto: LeituraTexto | None = None
    ancoragens: dict[str, ResultadoAncoragem] = field(default_factory=dict)
    caixas: dict[str, Caixa] = field(default_factory=dict)
    recortes: dict[str, bytes] = field(default_factory=dict)
    releituras: dict[str, ResultadoReleitura] = field(default_factory=dict)
    validacao: ResultadoValidacao | None = None
    decisao: Decisao | None = None
    chamadas: list[MetricasChamada] = field(default_factory=list)
    tempos_ms: dict[str, float] = field(default_factory=dict)
    stubs: list[str] = field(default_factory=list)

    @contextmanager
    def cronometro(self, etapa: str) -> Iterator[None]:
        inicio = time.perf_counter()
        try:
            yield
        finally:
            self.tempos_ms[etapa] = (time.perf_counter() - inicio) * 1000

    @property
    def custo_brl(self) -> float:
        return sum(c.custo_brl for c in self.chamadas)


@dataclass
class ResultadoPipeline:
    rastro: Rastro
    resposta: RespostaExtracao | None = None
    falha: FalhaProcessamento | None = None

    def __post_init__(self) -> None:
        if (self.resposta is None) == (self.falha is None):
            raise ValueError("resultado precisa ter resposta ou falha, nunca as duas")


def _stubs_ativos() -> list[str]:
    stubs = []
    if not qualidade.IMPLEMENTADO:
        stubs.append("qualidade")
    if not mod_ancoragem.IMPLEMENTADO:
        stubs.append("ancoragem")
    if not mod_releitura.IMPLEMENTADO:
        stubs.append("releitura")
    if not motor.IMPLEMENTADO:
        stubs.append("regras")
    return stubs


def _recortar(imagem: Imagem, caixa: Caixa, margem: float = 0.15) -> bytes:
    from PIL import Image

    pil = Image.open(io.BytesIO(imagem.conteudo))
    mx = max(8, int(caixa.largura * margem))
    my = max(8, int(caixa.altura * margem))
    area = (
        max(0, caixa.x - mx),
        max(0, caixa.y - my),
        min(pil.width, caixa.x + caixa.largura + mx),
        min(pil.height, caixa.y + caixa.altura + my),
    )
    buffer = io.BytesIO()
    pil.crop(area).save(buffer, format="PNG")
    return buffer.getvalue()


class Esteira:
    def __init__(self, settings: Settings, dependencias: Dependencias) -> None:
        self.settings = settings
        self.deps = dependencias

    @property
    def versao(self) -> str:
        return versao_pipeline(self.settings)

    async def processar(
        self, id_processamento: str, requisicao: RequisicaoExtracao
    ) -> ResultadoPipeline:
        """Entrada da API: decodifica o base64 e segue a esteira."""
        rastro = self._novo_rastro(
            id_processamento, requisicao.tipo_documento, requisicao.id_externo
        )
        try:
            with rastro.cronometro("decodificacao"):
                arquivo = decodificacao.decodificar_base64(requisicao.imagem_base64, self.settings)
        except FalhaProcessamento as falha:
            rastro.concluido_em = datetime.now(UTC)
            return ResultadoPipeline(rastro=rastro, falha=falha)
        return await self.processar_arquivo(rastro, arquivo)

    def novo_rastro(
        self, id_processamento: str, tipo: TipoDocumento, id_externo: str | None
    ) -> Rastro:
        return self._novo_rastro(id_processamento, tipo, id_externo)

    def _novo_rastro(
        self, id_processamento: str, tipo: TipoDocumento, id_externo: str | None
    ) -> Rastro:
        return Rastro(
            id_processamento=id_processamento,
            tipo_documento=tipo,
            id_externo=id_externo,
            recebido_em=datetime.now(UTC),
            versao_pipeline=self.versao,
            stubs=_stubs_ativos(),
        )

    async def processar_arquivo(
        self,
        rastro: Rastro,
        arquivo: ArquivoRecebido,
        modo: ModoChamada = ModoChamada.PADRAO,
    ) -> ResultadoPipeline:
        rastro.arquivo = arquivo
        inicio = time.perf_counter()
        try:
            resposta = await self._executar(rastro, arquivo, modo)
            resultado = ResultadoPipeline(rastro=rastro, resposta=resposta)
        except FalhaProcessamento as falha:
            resultado = ResultadoPipeline(rastro=rastro, falha=falha)
        rastro.tempos_ms["total"] = (time.perf_counter() - inicio) * 1000 + rastro.tempos_ms.get(
            "decodificacao", 0.0
        )
        rastro.concluido_em = datetime.now(UTC)
        return resultado

    async def _executar(
        self, rastro: Rastro, arquivo: ArquivoRecebido, modo: ModoChamada
    ) -> RespostaExtracao:
        settings = self.settings

        # 1. Pré-processamento (CPU)
        with rastro.cronometro("preprocessamento"):
            normalizada = await asyncio.to_thread(decodificacao.normalizar, arquivo, settings)
            imagem = normalizada.imagem
            rastro.imagem_normalizada = imagem
            rastro.paginas_pdf = normalizada.paginas_pdf
            rastro.qualidade = qualidade.avaliar(imagem)
        if rastro.qualidade.reprovacoes:
            raise FalhaExtracao(rastro.qualidade.reprovacoes)

        # 2 e 3. Leitura visual ∥ leitura de texto
        pedido = PedidoLeituraVisual(
            imagem=imagem,
            tipo_documento=rastro.tipo_documento,
            schema=schema_leitura(rastro.tipo_documento),
            prompt=carregar_prompt("leitura_visual"),
            modelo=settings.leitor_visual_modelo,
            modo=modo,
        )
        with rastro.cronometro("leituras"):
            leitura_visual, leitura_texto = await self._ler_em_paralelo(rastro, pedido, imagem)
        rastro.leitura_visual = leitura_visual
        rastro.leitura_texto = leitura_texto
        rastro.chamadas.extend([leitura_visual.metricas, leitura_texto.metricas])
        rastro.versao_pipeline = versao_pipeline(
            settings,
            provider_visual=leitura_visual.metricas.provider,
            modelo_visual=leitura_visual.metricas.modelo,
            provider_texto=leitura_texto.metricas.provider,
        )

        # Classificação dentro do tipo declarado
        mod_decisao.verificar_classificacao(rastro.tipo_documento, leitura_visual)
        subtipo = leitura_visual.subtipo

        # 4. Ancoragem
        with rastro.cronometro("ancoragem"):
            rastro.ancoragens = mod_ancoragem.ancorar(leitura_visual.campos, leitura_texto)

        # Localização e recortes (auditoria e releitura)
        with rastro.cronometro("localizacao"):
            preparados = mod_decisao.preparar_campos(subtipo, leitura_visual.campos)
            com_valor = [n for n, p in preparados.items() if p.valor is not None]
            localizacao = await self.deps.localizador.localizar(
                imagem, leitura_visual, leitura_texto, com_valor
            )
            if localizacao.metricas:
                rastro.chamadas.append(localizacao.metricas)
            rastro.caixas = localizacao.caixas
            rastro.recortes = {
                nome: await asyncio.to_thread(_recortar, imagem, caixa)
                for nome, caixa in localizacao.caixas.items()
            }

        # 5. Releitura dirigida
        with rastro.cronometro("releitura"):
            rastro.releituras = await mod_releitura.reler(
                mod_releitura.campos_para_reler(rastro.ancoragens)
            )
            for r in rastro.releituras.values():
                if r.releitura is not None:
                    rastro.chamadas.append(r.releitura.metricas)

        # 6. Regras e decisão
        with rastro.cronometro("regras"):
            rastro.validacao = motor.validar(
                subtipo, {n: p.entrada_regra() for n, p in preparados.items()}
            )
        with rastro.cronometro("decisao"):
            rastro.decisao = mod_decisao.decidir(
                leitura_visual,
                preparados,
                rastro.ancoragens,
                rastro.releituras,
                rastro.validacao,
                aprovar_nao_verificavel=settings.aprovar_critico_nao_verificavel,
            )

        return RespostaExtracao(
            id_processamento=rastro.id_processamento,
            status=rastro.decisao.status,
            documento=Documento(subtipo=subtipo, uf=leitura_visual.uf, lado=leitura_visual.lado),
            campos=rastro.decisao.campos,
            sinalizacoes=rastro.decisao.sinalizacoes,
            versao_pipeline=rastro.versao_pipeline,
        )

    async def _ler_em_paralelo(
        self, rastro: Rastro, pedido: PedidoLeituraVisual, imagem: Imagem
    ) -> tuple[LeituraVisual, LeituraTexto]:
        tempos: dict[str, float] = {}

        async def cronometrar(etapa: str, coro: Any) -> Any:
            inicio = time.perf_counter()
            try:
                return await coro
            finally:
                tempos[etapa] = (time.perf_counter() - inicio) * 1000

        try:
            async with asyncio.TaskGroup() as grupo:
                visual = grupo.create_task(
                    cronometrar("leitura_visual", self.deps.leitor_visual.ler(pedido))
                )
                texto = grupo.create_task(
                    cronometrar("leitura_texto", self.deps.leitor_texto.ler(imagem))
                )
        except ExceptionGroup as grupo_erros:
            if grupo_erros.subgroup(TempoEsgotadoProvider) is not None:
                raise FalhaTempoEsgotado.de(MotivoErro.TEMPO_ESGOTADO) from None
            if grupo_erros.subgroup(ErroProvider) is not None:
                raise FalhaProviderIndisponivel.de(MotivoErro.PROVIDER_INDISPONIVEL) from None
            raise
        finally:
            rastro.tempos_ms.update(tempos)
        return visual.result(), texto.result()
