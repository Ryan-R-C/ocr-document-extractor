"""Esteira de extração, independente do FastAPI.

Etapas: pré-processamento → leitura visual (desenho único ou em duas etapas) ∥ leitura de
texto → ancoragem → localização e recortes → releitura dirigida → regras → decisão. O mesmo
código roda na API e no avaliador.

Ancoragem, releitura e regras ainda são stubs (Fases 2 e 3) e nunca levam a APROVADO.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
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
    FalhaArquivoGrande,
    FalhaExtracao,
    FalhaProcessamento,
    FalhaProviderIndisponivel,
    FalhaTempoEsgotado,
)
from app.core.releitura import ResultadoReleitura
from app.core.versao import versao_pipeline
from app.preprocess import decodificacao, documento, preparacao, qualidade
from app.preprocess.captura import IndiciosCaptura
from app.preprocess.decodificacao import ArquivoRecebido, abrir_pil, para_imagem
from app.preprocess.documento import DocumentoLocalizado
from app.prompts import carregar as carregar_prompt
from app.providers.interfaces import (
    Caixa,
    Dependencias,
    ErroProvider,
    EtapaVisual,
    Imagem,
    ImagemRecusadaLocalmente,
    LeituraTexto,
    LeituraVisual,
    MetricasChamada,
    ModoChamada,
    PedidoVisual,
    RespostaVisual,
    Rotacao,
    TempoEsgotadoProvider,
)
from app.regras import motor
from app.regras.motor import ResultadoValidacao
from app.schemas import respostas
from app.schemas.campos import schema_classificacao, schema_extracao, schema_leitura


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
    imagem_normalizada: Imagem | None = None  # imagem inteira, orientada (EXIF)
    imagem_preparada: Imagem | None = None  # documento recortado, girado e redimensionado
    paginas_pdf: int | None = None
    documento: DocumentoLocalizado | None = None
    qualidade: qualidade.AvaliacaoQualidade | None = None
    captura: IndiciosCaptura | None = None
    # Desenho em duas etapas: a 1ª chamada (só classificação), para auditoria e avaliação.
    classificacao: LeituraVisual | None = None
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


def _girar(imagem: Imagem, rotacao: Rotacao) -> Imagem:
    if rotacao in (Rotacao.GRAUS_0, Rotacao.NAO_IDENTIFICADO):
        return imagem
    return para_imagem(documento.girar(abrir_pil(imagem), int(rotacao.value)), imagem.sha256_origem)


def _recortar(imagem: Imagem, caixa: Caixa, margem: float = 0.15) -> bytes:
    pil = abrir_pil(imagem)
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
        rastro = self.novo_rastro(
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

        # 1. Pré-processamento (CPU): orientação, recorte, qualidade, captura, resolução.
        with rastro.cronometro("preprocessamento"):
            preparada = await asyncio.to_thread(preparacao.preparar, arquivo, settings)
        rastro.imagem_normalizada = preparada.normalizada
        rastro.imagem_preparada = preparada.imagem
        rastro.paginas_pdf = preparada.paginas_pdf
        rastro.documento = preparada.documento
        rastro.qualidade = preparada.qualidade
        rastro.captura = preparada.captura
        if rastro.qualidade.reprovacoes:
            raise FalhaExtracao(rastro.qualidade.reprovacoes)

        # 2 e 3. Leitura visual ∥ leitura de texto
        if settings.leitor_visual_desenho == "duas_etapas":
            leitura_visual, leitura_texto = await self._ler_em_duas_etapas(
                rastro, preparada.imagem, modo
            )
        else:
            leitura_visual, leitura_texto = await self._ler_em_uma_etapa(
                rastro, preparada.imagem, modo
            )
        imagem = rastro.imagem_preparada
        assert imagem is not None
        rastro.leitura_visual = leitura_visual
        rastro.leitura_texto = leitura_texto
        rastro.versao_pipeline = versao_pipeline(
            settings,
            provider_visual=leitura_visual.metricas.provider,
            modelo_visual=leitura_visual.metricas.modelo,
            provider_texto=leitura_texto.metricas.provider,
        )
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
        captura = rastro.captura
        with rastro.cronometro("decisao"):
            rastro.decisao = mod_decisao.decidir(
                leitura_visual,
                preparados,
                rastro.ancoragens,
                rastro.releituras,
                rastro.validacao,
                aprovar_nao_verificavel=settings.aprovar_critico_nao_verificavel,
                sinalizacoes_captura=mod_decisao.sinalizacoes_de_captura(
                    leitura_visual,
                    captura.copia_reprografica if captura else False,
                    captura.print_de_tela if captura else False,
                    settings.sinalizacao_captura_fonte,
                ),
            )

        return RespostaExtracao(
            id_processamento=rastro.id_processamento,
            status=rastro.decisao.status,
            documento=Documento(subtipo=subtipo, uf=leitura_visual.uf, lado=leitura_visual.lado),
            campos=rastro.decisao.campos,
            sinalizacoes=rastro.decisao.sinalizacoes,
            versao_pipeline=rastro.versao_pipeline,
        )

    def _pedido(
        self,
        rastro: Rastro,
        etapa: EtapaVisual,
        imagem: Imagem,
        modo: ModoChamada,
        leitura: LeituraVisual | None = None,
    ) -> PedidoVisual:
        settings, tipo = self.settings, rastro.tipo_documento
        if etapa is EtapaVisual.LEITURA:
            schema, prompt, modelo = (
                schema_leitura(tipo),
                "leitura_visual",
                settings.leitor_visual_modelo,
            )
        elif etapa is EtapaVisual.CLASSIFICACAO:
            schema, prompt = schema_classificacao(tipo), "classificacao"
            modelo = settings.leitor_visual_modelo_classificacao
        else:
            assert leitura is not None
            schema, prompt = schema_extracao(leitura.subtipo), "extracao"
            modelo = settings.leitor_visual_modelo
        return PedidoVisual(
            etapa=etapa,
            imagem=imagem,
            tipo_documento=tipo,
            schema=schema,
            nome_schema=f"{etapa.value}_{tipo.value.lower()}",
            prompt=carregar_prompt(prompt),
            modelo=modelo,
            modo=modo,
            subtipo=leitura.subtipo if leitura else None,
        )

    async def _ler_em_uma_etapa(
        self, rastro: Rastro, imagem: Imagem, modo: ModoChamada
    ) -> tuple[LeituraVisual, LeituraTexto]:
        """Desenho (a): uma chamada com classificação e os campos de todos os subtipos."""
        pedido = self._pedido(rastro, EtapaVisual.LEITURA, imagem, modo)
        with rastro.cronometro("leituras"):
            resposta, texto = await self._em_paralelo(
                rastro, ("leitura_visual", self.deps.leitor_visual.ler(pedido)), imagem
            )
        rastro.chamadas.extend([resposta.metricas, texto.metricas])
        leitura = self._interpretar(
            lambda: respostas.interpretar_leitura(
                resposta.dados, resposta.resposta_bruta, resposta.metricas
            )
        )
        mod_decisao.verificar_classificacao(rastro.tipo_documento, leitura)
        rastro.imagem_preparada = await asyncio.to_thread(_girar, imagem, leitura.rotacao)
        return leitura, texto

    async def _ler_em_duas_etapas(
        self, rastro: Rastro, imagem: Imagem, modo: ModoChamada
    ) -> tuple[LeituraVisual, LeituraTexto]:
        """Desenho (b): classificação barata; recusa cedo se o tipo não confere; gira a
        imagem; extração com o schema do subtipo em paralelo com o OCR na imagem de pé."""
        pedido = self._pedido(rastro, EtapaVisual.CLASSIFICACAO, imagem, modo)
        with rastro.cronometro("classificacao"):
            resposta = await self._chamar(self.deps.leitor_visual.ler(pedido))
        rastro.chamadas.append(resposta.metricas)
        classificacao = self._interpretar(
            lambda: respostas.interpretar_classificacao(
                resposta.dados, resposta.resposta_bruta, resposta.metricas
            )
        )
        rastro.classificacao = classificacao
        mod_decisao.verificar_classificacao(rastro.tipo_documento, classificacao)

        de_pe = await asyncio.to_thread(_girar, imagem, classificacao.rotacao)
        rastro.imagem_preparada = de_pe
        pedido = self._pedido(rastro, EtapaVisual.EXTRACAO, de_pe, modo, classificacao)
        with rastro.cronometro("leituras"):
            extracao, texto = await self._em_paralelo(
                rastro, ("extracao", self.deps.leitor_visual.ler(pedido)), de_pe
            )
        rastro.chamadas.extend([extracao.metricas, texto.metricas])
        campos = self._interpretar(
            lambda: respostas.interpretar_campos(
                extracao.dados.get("campos", {}), classificacao.subtipo
            )
        )
        leitura = replace(
            classificacao,
            campos=campos,
            resposta_bruta={
                "classificacao": resposta.resposta_bruta,
                "extracao": extracao.resposta_bruta,
            },
            metricas=extracao.metricas,
        )
        return leitura, texto

    @staticmethod
    def _interpretar[T](funcao: Callable[[], T]) -> T:
        try:
            return funcao()
        except respostas.RespostaInvalida:
            raise FalhaProviderIndisponivel.de(
                MotivoErro.PROVIDER_INDISPONIVEL, detalhe="resposta fora do schema"
            ) from None

    @staticmethod
    async def _chamar[T](coro: Awaitable[T]) -> T:
        try:
            return await coro
        except ImagemRecusadaLocalmente:
            raise FalhaArquivoGrande.de(
                MotivoErro.ARQUIVO_GRANDE_DEMAIS, detalhe="limite de imagem do provider"
            ) from None
        except TempoEsgotadoProvider:
            raise FalhaTempoEsgotado.de(MotivoErro.TEMPO_ESGOTADO) from None
        except ErroProvider:
            raise FalhaProviderIndisponivel.de(MotivoErro.PROVIDER_INDISPONIVEL) from None

    async def _em_paralelo(
        self,
        rastro: Rastro,
        visual: tuple[str, Awaitable[RespostaVisual]],
        imagem: Imagem,
    ) -> tuple[RespostaVisual, LeituraTexto]:
        tempos: dict[str, float] = {}

        async def cronometrar(etapa: str, coro: Awaitable[Any]) -> Any:
            inicio = time.perf_counter()
            try:
                return await coro
            finally:
                tempos[etapa] = (time.perf_counter() - inicio) * 1000

        try:
            async with asyncio.TaskGroup() as grupo:
                tarefa_visual = grupo.create_task(cronometrar(visual[0], visual[1]))
                tarefa_texto = grupo.create_task(
                    cronometrar("leitura_texto", self.deps.leitor_texto.ler(imagem))
                )
        except ExceptionGroup as grupo_erros:
            if grupo_erros.subgroup(ImagemRecusadaLocalmente) is not None:
                raise FalhaArquivoGrande.de(
                    MotivoErro.ARQUIVO_GRANDE_DEMAIS, detalhe="limite de imagem do provider"
                ) from None
            if grupo_erros.subgroup(TempoEsgotadoProvider) is not None:
                raise FalhaTempoEsgotado.de(MotivoErro.TEMPO_ESGOTADO) from None
            if grupo_erros.subgroup(ErroProvider) is not None:
                raise FalhaProviderIndisponivel.de(MotivoErro.PROVIDER_INDISPONIVEL) from None
            raise
        finally:
            rastro.tempos_ms.update(tempos)
        return tarefa_visual.result(), tarefa_texto.result()
