"""Roda a mesma esteira de produção sobre o gabarito e monta o relatório."""

from __future__ import annotations

import asyncio
import json
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.contrato import MotivoErro
from app.config import Settings
from app.core.erros import FalhaRequisicao
from app.core.pipeline import Esteira, ResultadoPipeline
from app.montagem import criar_esteira
from app.preprocess.decodificacao import ArquivoRecebido, detectar_formato
from app.providers.interfaces import ModoChamada
from avaliacao import campo_apagado, metricas, relatorio
from avaliacao.campo_apagado import METODOS, LocalizadorValorPorOCR, Metodo
from avaliacao.gabarito import ItemGabarito, Particao, ler_manifesto, sha256_arquivo


class ConfigCampoApagado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ativo: bool = True
    metodos: list[Metodo] = Field(default_factory=lambda: list(METODOS))
    # Campos críticos apagados por item (escolhidos pelo hash); 0 ou ausente = todos.
    campos_por_item: int = Field(default=1, ge=0)
    # Sem caixa no gabarito, procura o valor conferido nas palavras do OCR configurado.
    usar_ocr_sem_caixa: bool = False


class ConfigAvaliacao(BaseModel):
    """Arquivo TOML de uma rodada de avaliação."""

    model_config = ConfigDict(extra="forbid")

    nome: str
    gabarito: Path
    saida: Path = Path("saidas/avaliacoes")
    modo: ModoChamada = ModoChamada.PADRAO
    concorrencia: int = Field(default=4, ge=1)
    # Sobrescreve variáveis de app.config (mesmos nomes, em minúsculas).
    settings: dict[str, Any] = Field(default_factory=dict)
    campo_apagado: ConfigCampoApagado = Field(default_factory=ConfigCampoApagado)

    @classmethod
    def carregar(cls, caminho: Path) -> ConfigAvaliacao:
        dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
        config = cls.model_validate(dados)
        base = caminho.parent
        if not config.gabarito.is_absolute():
            config.gabarito = (base / config.gabarito).resolve()
        return config


class ModoNaoImplementado(RuntimeError):
    pass


def _arquivo(caminho: Path, sha256: str) -> ArquivoRecebido:
    conteudo = caminho.read_bytes()
    formato = detectar_formato(conteudo)
    if formato is None:
        raise FalhaRequisicao.de(MotivoErro.FORMATO_NAO_SUPORTADO)
    return ArquivoRecebido(conteudo, formato, sha256)


async def _processar(
    esteira: Esteira, diretorio: Path, item: ItemGabarito, modo: ModoChamada
) -> ResultadoPipeline:
    rastro = esteira.novo_rastro(str(uuid.uuid4()), item.tipo_documento, None)
    try:
        arquivo = _arquivo(diretorio / item.arquivo, item.sha256)
    except FalhaRequisicao as falha:
        return ResultadoPipeline(rastro=rastro, falha=falha)
    return await esteira.processar_arquivo(rastro, arquivo, modo)


async def avaliar(
    config: ConfigAvaliacao,
    particao: Particao,
    settings: Settings | None = None,
    agora: datetime | None = None,
) -> tuple[dict[str, Any], Path]:
    """Roda a avaliação e grava `relatorio.md` e `relatorio.json`. Devolve (relatório, pasta)."""
    if config.modo is not ModoChamada.PADRAO:
        raise ModoNaoImplementado(
            f"modo {config.modo.value}: a rodada Batch/Flex será implementada na Fase 1"
        )
    settings = settings or Settings(**config.settings)
    esteira = criar_esteira(settings)
    inicio = agora or datetime.now(UTC)
    relogio = time.perf_counter()

    dir_gabarito = config.gabarito.parent
    todos = ler_manifesto(config.gabarito)
    sem_particao = [i.arquivo for i in todos if i.particao is None]
    if sem_particao:
        raise ValueError(
            f"{len(sem_particao)} itens sem partição; rode `gabarito particionar` antes"
        )
    itens = [i for i in todos if i.particao == particao and i.variante_de is None]
    for item in itens:
        if sha256_arquivo(dir_gabarito / item.arquivo) != item.sha256:
            raise ValueError(f"{item.arquivo}: sha256 diferente do manifesto")

    pasta = config.saida / f"{config.nome}-{particao}-{inicio.strftime('%Y%m%dT%H%M%SZ')}"
    pasta.mkdir(parents=True, exist_ok=True)

    variantes = campo_apagado.ResultadoVariantes()
    dir_variantes = pasta / "variantes"
    if config.campo_apagado.ativo:
        localizador = (
            LocalizadorValorPorOCR(esteira.deps.leitor_texto)
            if config.campo_apagado.usar_ocr_sem_caixa
            else None
        )
        variantes = await campo_apagado.gerar_variantes(
            itens,
            dir_gabarito,
            dir_variantes,
            config.campo_apagado.metodos,
            localizador,
            config.campo_apagado.campos_por_item or None,
        )

    semaforo = asyncio.Semaphore(config.concorrencia)

    async def um(diretorio: Path, item: ItemGabarito) -> tuple[ItemGabarito, ResultadoPipeline]:
        async with semaforo:
            return item, await _processar(esteira, diretorio, item, config.modo)

    tarefas = [um(dir_gabarito, i) for i in itens] + [um(dir_variantes, v) for v in variantes.itens]
    registros = metricas.Registros()
    for item, resultado in await asyncio.gather(*tarefas):
        metricas.registrar(registros, item, resultado)

    resultado_final = {
        "execucao": {
            "nome": config.nome,
            "particao": particao,
            "modo": config.modo.value,
            "inicio": inicio.isoformat(),
            "duracao_s": time.perf_counter() - relogio,
            "versao_pipeline": esteira.versao,
            "stubs_ativos": esteira.novo_rastro("-", itens[0].tipo_documento, None).stubs
            if itens
            else [],
            "gabarito": str(config.gabarito.name),
            "itens": len(itens),
            "variantes": len(variantes.itens),
            "campo_apagado_pulados_sem_caixa": variantes.pulados_sem_caixa,
            "campo_apagado_pulados_formato": variantes.pulados_formato,
        },
        "metricas": metricas.calcular(registros),
    }
    (pasta / "relatorio.json").write_text(
        json.dumps(resultado_final, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (pasta / "relatorio.md").write_text(relatorio.markdown(resultado_final), encoding="utf-8")
    return resultado_final, pasta
