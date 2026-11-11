"""Logs estruturados com mascaramento de dados pessoais.

Duas camadas, aplicadas a todo evento (structlog e logging da biblioteca padrão):
1. Chaves sensíveis (nome, cpf, valor, texto_literal, imagem_base64…) têm o valor trocado.
2. Padrões no texto livre: CPF, CNPJ, datas e sequências longas de dígitos.

Nomes próprios em texto livre não têm padrão confiável: a regra é nunca colocar valores de
campo em mensagem; a camada 1 cobre o que vem como dado estruturado.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, TextIO

import structlog

MASCARA = "***"

CHAVES_SENSIVEIS = frozenset(
    {
        "nome",
        "nomes",
        "cpf",
        "cnpj",
        "rg",
        "numero_rg",
        "valor",
        "valor_normalizado",
        "texto_literal",
        "rotulo_visto",
        "filiacao",
        "filiacao_linha_1",
        "filiacao_linha_2",
        "data_nascimento",
        "data_expedicao",
        "naturalidade",
        "imagem_base64",
        "campos",
        "palavras",
        "resposta_bruta",
        "body",
        "input",
    }
)

_PADROES: tuple[re.Pattern[str], ...] = (
    # CNPJ (inclui alfanumérico) antes do CPF
    re.compile(r"\b[0-9A-Z]{2}\.?[0-9A-Z]{3}\.?[0-9A-Z]{3}/?[0-9A-Z]{4}-?\d{2}\b"),
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),  # CPF
    re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"),  # datas dd/mm/aaaa
    re.compile(r"(?<![\d\-T:])\d{4}-\d{2}-\d{2}(?![T\d])"),  # datas ISO soltas (não timestamps)
    re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}-?[\dXx]\b"),  # RG com DV
    re.compile(
        r"(?<![0-9a-f\-])\d{7,}(?![0-9a-f\-])"
    ),  # sequências longas de dígitos (fora de hashes/uuids)
)


def mascarar_texto(texto: str) -> str:
    for padrao in _PADROES:
        texto = padrao.sub(MASCARA, texto)
    return texto


def _mascarar(valor: Any, chave: str | None = None) -> Any:
    if chave is not None and chave.lower() in CHAVES_SENSIVEIS:
        return MASCARA if valor not in (None, "", [], {}) else valor
    if isinstance(valor, str):
        return mascarar_texto(valor)
    if isinstance(valor, Mapping):
        return {k: _mascarar(v, str(k)) for k, v in valor.items()}
    if isinstance(valor, list | tuple | set):
        return [_mascarar(v) for v in valor]
    return valor


def processador_mascaramento(
    _logger: Any, _metodo: str, evento: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for chave in list(evento.keys()):
        evento[chave] = _mascarar(evento[chave], chave if chave != "event" else None)
    return evento


def configurar_logs(nivel: str = "INFO", json: bool = True, stream: TextIO | None = None) -> None:
    """Configura structlog e o logging padrão (uvicorn, sqlalchemy…) com o mesmo mascaramento."""
    destino = stream or sys.stderr
    pre_cadeia: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.format_exc_info,
        processador_mascaramento,
        # O timestamp entra depois do mascaramento para não ser confundido com data.
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderizador: Any = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *pre_cadeia],
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderizador],
    )
    handler = logging.StreamHandler(destino)
    handler.setFormatter(formatter)
    raiz = logging.getLogger()
    raiz.handlers = [handler]
    raiz.setLevel(nivel.upper())
    for nome in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(nome).handlers = []
        logging.getLogger(nome).propagate = True

    structlog.configure(
        processors=[*pre_cadeia, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def obter_logger(nome: str | None = None) -> Any:
    return structlog.get_logger(nome)
