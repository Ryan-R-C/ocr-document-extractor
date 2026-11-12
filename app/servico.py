"""Serviço de extração: cache → esteira → resposta → pacote de auditoria e metadados.

Fica fora do FastAPI. Dois modos usam o mesmo fluxo:
- síncrono (`processar`): a requisição espera a esteira;
- assíncrono (`enfileirar` → trabalhador chama `processar_tarefa` → `consultar`): o POST
  responde 202 na hora e o resultado é lido do pacote de auditoria quando concluir.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.api.contrato import (
    AcaoSugerida,
    CodigoErro,
    MotivoErro,
    MotivoErroDetalhado,
    RequisicaoExtracao,
    RespostaAceita,
    RespostaErro,
    SituacaoExtracao,
    StatusDocumento,
    TipoDocumento,
)
from app.api.erros import corpo_erro, status_http
from app.armazenamento import auditoria, retencao
from app.armazenamento.fila import CONCLUIDA, PENDENTE, Fila, utc
from app.armazenamento.modelos import CasoRevisao, Processamento, TarefaExtracao
from app.armazenamento.objetos import Objetos
from app.armazenamento.repositorio import Repositorio, chave_cache
from app.config import Settings
from app.core.erros import (
    FalhaArquivoGrande,
    FalhaExtracao,
    FalhaProviderIndisponivel,
    FalhaRequisicao,
    FalhaTempoEsgotado,
)
from app.core.pipeline import Esteira, ResultadoPipeline
from app.logs import obter_logger
from app.preprocess import decodificacao
from app.preprocess.decodificacao import EXTENSAO, ArquivoRecebido, Formato

log = obter_logger(__name__)


@dataclass(frozen=True)
class RespostaServico:
    id_processamento: str
    http_status: int
    corpo: dict[str, Any]
    do_cache: bool = False
    # Pacote de auditoria gravado (None quando a gravação falhou ou não havia arquivo).
    prefixo_pacote: str | None = None
    headers: dict[str, str] | None = None


def url_consulta(id_processamento: str) -> str:
    return f"/v1/extracoes/{id_processamento}"


def erro_simples(
    id_processamento: str, codigo: CodigoErro, motivo: MotivoErro, acao: AcaoSugerida
) -> dict[str, Any]:
    return RespostaErro(
        id_processamento=id_processamento,
        erro=codigo,
        motivos=[MotivoErroDetalhado(codigo=motivo)],
        acao_sugerida=acao,
    ).model_dump(mode="json", exclude_none=True)


def _status_registro(resultado: ResultadoPipeline) -> str:
    if resultado.resposta is not None:
        return resultado.resposta.status.value
    falha = resultado.falha
    if isinstance(falha, FalhaExtracao):
        return "RECUSADO"
    if isinstance(falha, FalhaRequisicao | FalhaArquivoGrande):
        return "REQUISICAO_INVALIDA"
    return "ERRO_PROVIDER"


class ServicoExtracao:
    def __init__(
        self,
        settings: Settings,
        esteira: Esteira,
        repositorio: Repositorio,
        objetos: Objetos,
        fila: Fila | None = None,
    ) -> None:
        self.settings = settings
        self.esteira = esteira
        self.repositorio = repositorio
        self.objetos = objetos
        self.fila = fila or Fila(repositorio.sessoes)

    async def processar(
        self, requisicao: RequisicaoExtracao, id_processamento: str | None = None
    ) -> RespostaServico:
        id_proc = id_processamento or str(uuid.uuid4())
        rastro = self.esteira.novo_rastro(id_proc, requisicao.tipo_documento, requisicao.id_externo)

        try:
            arquivo = decodificacao.decodificar_base64(requisicao.imagem_base64, self.settings)
        except (FalhaRequisicao, FalhaArquivoGrande) as falha:
            rastro.concluido_em = datetime.now(UTC)
            resultado = ResultadoPipeline(rastro=rastro, falha=falha)
            return await self._concluir(resultado)

        do_cache = await self._do_cache(
            id_proc, requisicao.tipo_documento, requisicao.id_externo, arquivo.sha256
        )
        if do_cache is not None:
            return do_cache

        resultado = await self.esteira.processar_arquivo(rastro, arquivo)
        return await self._concluir(resultado)

    # --- Modo assíncrono ------------------------------------------------------------------

    async def enfileirar(
        self, requisicao: RequisicaoExtracao, id_processamento: str | None = None
    ) -> RespostaServico:
        """Valida o arquivo (400/413 na hora), confere o cache e põe na fila: 202."""
        id_proc = id_processamento or str(uuid.uuid4())
        rastro = self.esteira.novo_rastro(id_proc, requisicao.tipo_documento, requisicao.id_externo)
        try:
            arquivo = decodificacao.decodificar_base64(requisicao.imagem_base64, self.settings)
        except (FalhaRequisicao, FalhaArquivoGrande) as falha:
            rastro.concluido_em = datetime.now(UTC)
            return await self._concluir(ResultadoPipeline(rastro=rastro, falha=falha))

        agora = datetime.now(UTC)
        do_cache = await self._do_cache(
            id_proc, requisicao.tipo_documento, requisicao.id_externo, arquivo.sha256
        )
        if do_cache is not None:
            return self._aceita(id_proc, SituacaoExtracao.CONCLUIDA, agora)

        chave_imagem = retencao.chave_imagem_fila(id_proc, EXTENSAO[arquivo.formato])
        await asyncio.to_thread(self.objetos.gravar, chave_imagem, arquivo.conteudo, arquivo.mime)
        tarefa = TarefaExtracao(
            id=id_proc,
            id_externo=requisicao.id_externo,
            tipo_documento=requisicao.tipo_documento.value,
            sha256_imagem=arquivo.sha256,
            formato=arquivo.formato.value,
            chave_imagem=chave_imagem,
            situacao=PENDENTE,
            tentativas=0,
            disponivel_em=agora,
            criado_em=agora,
            expira_em=retencao.expira_em(agora, self.settings.ttl_artefatos),
        )
        await asyncio.to_thread(self.fila.enfileirar, tarefa)
        posicao = await asyncio.to_thread(self.fila.posicao, tarefa)
        log.info("extracao_enfileirada", id_processamento=id_proc, posicao_fila=posicao)
        return self._aceita(id_proc, SituacaoExtracao.PENDENTE, agora, posicao)

    def _aceita(
        self,
        id_proc: str,
        situacao: SituacaoExtracao,
        recebido_em: datetime,
        posicao: int | None = None,
    ) -> RespostaServico:
        corpo = RespostaAceita(
            id_processamento=id_proc,
            situacao=situacao,
            url_consulta=url_consulta(id_proc),
            posicao_fila=posicao,
            recebido_em=recebido_em,
        ).model_dump(mode="json", exclude_none=True)
        headers = {"Location": url_consulta(id_proc)}
        if situacao is not SituacaoExtracao.CONCLUIDA:
            headers["Retry-After"] = str(self.settings.consulta_retry_after_s)
        return RespostaServico(id_proc, 202, corpo, headers=headers)

    async def consultar(self, id_processamento: str) -> RespostaServico:
        """Resultado concluído (mesmo corpo e código do síncrono), 202 enquanto não
        conclui, ou 404."""
        processamento = await asyncio.to_thread(self.repositorio.obter, id_processamento)
        if processamento is not None and processamento.prefixo_pacote:
            bruto = await asyncio.to_thread(
                self.objetos.ler, f"{processamento.prefixo_pacote}/{auditoria.ARQUIVO_RESPOSTA}"
            )
            if bruto is not None:
                guardado = json.loads(bruto)
                corpo = dict(guardado["corpo"])
                corpo["id_processamento"] = id_processamento  # resultado vindo do cache
                return RespostaServico(id_processamento, guardado["http_status"], corpo)

        tarefa = await asyncio.to_thread(self.fila.obter, id_processamento)
        if tarefa is not None and tarefa.situacao != CONCLUIDA:
            posicao = None
            if tarefa.situacao == PENDENTE:
                posicao = await asyncio.to_thread(self.fila.posicao, tarefa)
            return self._aceita(
                id_processamento, SituacaoExtracao(tarefa.situacao), utc(tarefa.criado_em), posicao
            )
        if tarefa is not None and tarefa.corpo_erro and tarefa.http_status:
            return RespostaServico(
                id_processamento, tarefa.http_status, json.loads(tarefa.corpo_erro)
            )
        return RespostaServico(
            id_processamento,
            404,
            erro_simples(
                id_processamento,
                CodigoErro.NAO_ENCONTRADO,
                MotivoErro.EXTRACAO_NAO_ENCONTRADA,
                AcaoSugerida.CORRIGIR_REQUISICAO,
            ),
        )

    async def processar_tarefa(self, tarefa: TarefaExtracao) -> str:
        """Roda uma tarefa reservada. Devolve `concluida` ou `devolvida` (erro transitório
        do provider, ainda com tentativas: volta para a fila com espera crescente)."""
        id_proc = tarefa.id
        tipo = TipoDocumento(tarefa.tipo_documento)
        conteudo = await asyncio.to_thread(self.objetos.ler, tarefa.chave_imagem)
        if conteudo is None:
            await self._concluir_tarefa_com_erro(id_proc, "imagem_ausente")
            return "concluida"

        do_cache = await self._do_cache(id_proc, tipo, tarefa.id_externo, tarefa.sha256_imagem)
        if do_cache is not None:
            await asyncio.to_thread(
                self.fila.concluir, id_proc, datetime.now(UTC), do_cache.http_status
            )
            await asyncio.to_thread(
                self.objetos.apagar_prefixo, f"{retencao.PREFIXO_FILA}/{id_proc}"
            )
            return "concluida"

        rastro = self.esteira.novo_rastro(id_proc, tipo, tarefa.id_externo)
        rastro.recebido_em = utc(tarefa.criado_em)  # o TTL conta da chegada
        arquivo = ArquivoRecebido(conteudo, Formato(tarefa.formato), tarefa.sha256_imagem)
        resultado = await self.esteira.processar_arquivo(rastro, arquivo)

        transitorio = isinstance(resultado.falha, FalhaProviderIndisponivel | FalhaTempoEsgotado)
        if transitorio and tarefa.tentativas < self.settings.fila_tentativas_max:
            espera = min(
                self.settings.fila_espera_max_s,
                self.settings.fila_espera_inicial_s * 2 ** (tarefa.tentativas - 1),
            )
            await asyncio.to_thread(
                self.fila.devolver, id_proc, datetime.now(UTC) + timedelta(seconds=espera)
            )
            log.warning(
                "tarefa_devolvida",
                id_processamento=id_proc,
                tentativa=tarefa.tentativas,
                espera_s=espera,
                motivos=[m.codigo.value for m in resultado.falha.motivos]
                if resultado.falha
                else [],
                custo_brl=round(rastro.custo_brl, 6),
            )
            return "devolvida"

        resposta = await self._concluir(resultado)
        if resposta.prefixo_pacote is None:
            await self._concluir_tarefa_com_erro(id_proc, "auditoria_falhou")
            return "concluida"
        await asyncio.to_thread(
            self.fila.concluir, id_proc, datetime.now(UTC), resposta.http_status
        )
        await asyncio.to_thread(self.objetos.apagar_prefixo, f"{retencao.PREFIXO_FILA}/{id_proc}")
        return "concluida"

    async def _concluir_tarefa_com_erro(self, id_proc: str, motivo_log: str) -> None:
        """Sem resultado gravado para devolver: a consulta recebe 500 ERRO_INTERNO."""
        corpo = erro_simples(
            id_proc, CodigoErro.ERRO_INTERNO, MotivoErro.ERRO_INTERNO, AcaoSugerida.TENTAR_NOVAMENTE
        )
        await asyncio.to_thread(
            self.fila.concluir, id_proc, datetime.now(UTC), 500, json.dumps(corpo)
        )
        await asyncio.to_thread(self.objetos.apagar_prefixo, f"{retencao.PREFIXO_FILA}/{id_proc}")
        log.error("tarefa_sem_resultado", id_processamento=id_proc, motivo=motivo_log)

    # --- Comum aos dois modos ---------------------------------------------------------------

    async def _do_cache(
        self, id_proc: str, tipo: TipoDocumento, id_externo: str | None, sha256: str
    ) -> RespostaServico | None:
        chave = chave_cache(sha256, tipo.value, self.esteira.versao)
        agora = datetime.now(UTC)
        original = await asyncio.to_thread(self.repositorio.buscar_cache, chave, agora)
        if original is None or not original.prefixo_pacote:
            return None
        bruto = await asyncio.to_thread(
            self.objetos.ler, f"{original.prefixo_pacote}/{auditoria.ARQUIVO_RESPOSTA}"
        )
        if bruto is None:
            return None
        guardado = json.loads(bruto)
        corpo = dict(guardado["corpo"])
        corpo["id_processamento"] = id_proc
        registro = Processamento(
            id=id_proc,
            id_externo=id_externo,
            sha256_imagem=sha256,
            tipo_documento=tipo.value,
            versao_pipeline=original.versao_pipeline,
            status=original.status,
            http_status=guardado["http_status"],
            chave_cache=None,
            origem_cache_id=original.id,
            prefixo_pacote=original.prefixo_pacote,
            custo_brl=Decimal(0),
            recebido_em=agora,
            concluido_em=datetime.now(UTC),
            expira_em=original.expira_em,
        )
        await asyncio.to_thread(self.repositorio.registrar, registro)
        log.info(
            "extracao_concluida",
            id_processamento=id_proc,
            origem_cache_id=original.id,
            http_status=guardado["http_status"],
            status=original.status,
        )
        return RespostaServico(
            id_proc,
            guardado["http_status"],
            corpo,
            do_cache=True,
            prefixo_pacote=original.prefixo_pacote,
        )

    async def _concluir(self, resultado: ResultadoPipeline) -> RespostaServico:
        rastro = resultado.rastro
        if resultado.resposta is not None:
            http, corpo = 200, resultado.resposta.model_dump(mode="json")
        else:
            assert resultado.falha is not None
            http = status_http(resultado.falha, self.settings)
            corpo = corpo_erro(rastro.id_processamento, resultado.falha)

        prefixo = await asyncio.to_thread(self._persistir, resultado, http, corpo)
        log.info(
            "extracao_concluida",
            id_processamento=rastro.id_processamento,
            http_status=http,
            status=_status_registro(resultado),
            motivos=[m.codigo.value for m in resultado.falha.motivos] if resultado.falha else [],
            versao_pipeline=rastro.versao_pipeline,
            custo_brl=round(rastro.custo_brl, 6),
            tempos_ms={k: round(v, 1) for k, v in rastro.tempos_ms.items()},
        )
        return RespostaServico(rastro.id_processamento, http, corpo, prefixo_pacote=prefixo)

    def _persistir(
        self, resultado: ResultadoPipeline, http: int, corpo: dict[str, Any]
    ) -> str | None:
        rastro = resultado.rastro
        agora = datetime.now(UTC)
        expira = retencao.expira_em(rastro.recebido_em, self.settings.ttl_artefatos)
        prefixo: str | None = None

        if rastro.arquivo is not None:
            prefixo = retencao.prefixo_pacote(self.settings, rastro.id_processamento)
            try:
                for caminho, (conteudo, tipo) in auditoria.montar_pacote(
                    resultado, http, corpo
                ).items():
                    self.objetos.gravar(f"{prefixo}/{caminho}", conteudo, tipo)
            except Exception as erro:
                # Decisão registrada: falha de auditoria não bloqueia a resposta, mas o
                # resultado não entra no cache e o erro vai para o log (sem dados pessoais).
                log.error(
                    "auditoria_falhou",
                    id_processamento=rastro.id_processamento,
                    erro=type(erro).__name__,
                )
                prefixo = None

        reaproveitavel = (
            prefixo is not None
            and rastro.arquivo is not None
            and http in (200, self.settings.http_status_falha_extracao)
            and (resultado.resposta is not None or isinstance(resultado.falha, FalhaExtracao))
            and rastro.versao_pipeline == self.esteira.versao
        )
        processamento = Processamento(
            id=rastro.id_processamento,
            id_externo=rastro.id_externo,
            sha256_imagem=rastro.arquivo.sha256 if rastro.arquivo else None,
            tipo_documento=rastro.tipo_documento.value,
            versao_pipeline=rastro.versao_pipeline,
            status=_status_registro(resultado),
            http_status=http,
            chave_cache=chave_cache(
                rastro.arquivo.sha256, rastro.tipo_documento.value, rastro.versao_pipeline
            )
            if reaproveitavel and rastro.arquivo
            else None,
            origem_cache_id=None,
            prefixo_pacote=prefixo,
            custo_brl=Decimal(str(round(rastro.custo_brl, 6))),
            recebido_em=rastro.recebido_em,
            concluido_em=rastro.concluido_em or agora,
            expira_em=expira,
        )

        caso: CasoRevisao | None = None
        resposta = resultado.resposta
        if resposta is not None and resposta.status is StatusDocumento.REVISAO and prefixo:
            id_caso = str(uuid.uuid4())
            prefixo_caso = retencao.prefixo_caso_revisao(id_caso)
            try:
                self.objetos.copiar_prefixo(prefixo, prefixo_caso)
                caso = CasoRevisao(
                    id=id_caso,
                    processamento_id=rastro.id_processamento,
                    sha256_imagem=rastro.arquivo.sha256 if rastro.arquivo else "",
                    tipo_documento=rastro.tipo_documento.value,
                    subtipo=resposta.documento.subtipo.value,
                    uf=resposta.documento.uf.value,
                    versao_pipeline=rastro.versao_pipeline,
                    prefixo_pacote=prefixo_caso,
                    situacao="PENDENTE",
                    criado_em=agora,
                    expira_em=retencao.expira_em(agora, self.settings.ttl_casos_revisao),
                )
            except Exception as erro:
                log.error(
                    "caso_revisao_falhou",
                    id_processamento=rastro.id_processamento,
                    erro=type(erro).__name__,
                )

        try:
            self.repositorio.registrar(processamento, rastro.chamadas, caso)
        except Exception as erro:
            log.error(
                "registro_falhou",
                id_processamento=rastro.id_processamento,
                erro=type(erro).__name__,
            )
            return None
        return prefixo
