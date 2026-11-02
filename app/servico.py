"""Serviço de extração: cache → esteira → resposta → pacote de auditoria e metadados.

Fica fora do FastAPI para que um modo assíncrono (202 + consulta por id) reaproveite tudo.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.api.contrato import RequisicaoExtracao, StatusDocumento
from app.api.erros import corpo_erro, status_http
from app.armazenamento import auditoria, retencao
from app.armazenamento.modelos import CasoRevisao, Processamento
from app.armazenamento.objetos import Objetos
from app.armazenamento.repositorio import Repositorio, chave_cache
from app.config import Settings
from app.core.erros import FalhaArquivoGrande, FalhaExtracao, FalhaRequisicao
from app.core.pipeline import Esteira, ResultadoPipeline
from app.logs import obter_logger
from app.preprocess import decodificacao

log = obter_logger(__name__)


@dataclass(frozen=True)
class RespostaServico:
    id_processamento: str
    http_status: int
    corpo: dict[str, Any]
    do_cache: bool = False


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
        self, settings: Settings, esteira: Esteira, repositorio: Repositorio, objetos: Objetos
    ) -> None:
        self.settings = settings
        self.esteira = esteira
        self.repositorio = repositorio
        self.objetos = objetos

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

        chave = chave_cache(arquivo.sha256, requisicao.tipo_documento.value, self.esteira.versao)
        do_cache = await self._do_cache(chave, rastro.id_processamento, requisicao, arquivo.sha256)
        if do_cache is not None:
            return do_cache

        resultado = await self.esteira.processar_arquivo(rastro, arquivo)
        return await self._concluir(resultado)

    async def _do_cache(
        self, chave: str, id_proc: str, requisicao: RequisicaoExtracao, sha256: str
    ) -> RespostaServico | None:
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
            id_externo=requisicao.id_externo,
            sha256_imagem=sha256,
            tipo_documento=requisicao.tipo_documento.value,
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
        return RespostaServico(id_proc, guardado["http_status"], corpo, do_cache=True)

    async def _concluir(self, resultado: ResultadoPipeline) -> RespostaServico:
        rastro = resultado.rastro
        if resultado.resposta is not None:
            http, corpo = 200, resultado.resposta.model_dump(mode="json")
        else:
            assert resultado.falha is not None
            http = status_http(resultado.falha, self.settings)
            corpo = corpo_erro(rastro.id_processamento, resultado.falha)

        await asyncio.to_thread(self._persistir, resultado, http, corpo)
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
        return RespostaServico(rastro.id_processamento, http, corpo)

    def _persistir(self, resultado: ResultadoPipeline, http: int, corpo: dict[str, Any]) -> None:
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
