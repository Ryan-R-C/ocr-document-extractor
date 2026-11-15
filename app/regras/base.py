"""Estrutura das regras: metadados versionados e uma função pura por regra.

Cada regra declara id, versão, vigência, fonte, a que subtipos e campos se aplica e qual
data de referência usa para a vigência (a do documento, como emissão ou expedição, ou a
do processamento). A função recebe o `Contexto` (só o próprio documento) e devolve as
avaliações; nunca corrige valor.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from app.api.contrato import (
    UF,
    MotivoCampo,
    Sinalizacao,
    StatusLeitura,
    StatusValidacao,
    Subtipo,
)
from app.regras.tabelas import Tabelas
from app.schemas.campos import DefinicaoCampo
from app.schemas.normalizacao import INDETERMINADA


class Referencia(StrEnum):
    DOCUMENTO = "DOCUMENTO"
    PROCESSAMENTO = "PROCESSAMENTO"


@dataclass(frozen=True)
class EntradaRegra:
    status_leitura: StatusLeitura
    valor: str | None
    valor_normalizado: str | None


@dataclass(frozen=True)
class Parametros:
    """Limiares das regras (vêm de `app/config.py`)."""

    ano_minimo: int = 1900
    idade_min: int = 14
    idade_max: int = 120
    idade_min_admissao: int = 14
    cin_validade_tolerancia_dias: int = 31
    cnh_validade_tolerancia_dias: int = 31
    moeda_transicao_dias: int = 180


@dataclass(frozen=True)
class Contexto:
    subtipo: Subtipo
    definicoes: tuple[DefinicaoCampo, ...]
    campos: Mapping[str, EntradaRegra]
    data_processamento: date
    parametros: Parametros
    tabelas: Tabelas
    # UF classificada pelo VLM; o campo de UF do próprio documento tem precedência.
    uf_leitura: UF | None = None

    def norm(self, nome: str) -> str | None:
        """Valor normalizado de campo LEGIVEL (as regras nunca usam leitura parcial)."""
        entrada = self.campos.get(nome)
        if entrada is None or entrada.status_leitura is not StatusLeitura.LEGIVEL:
            return None
        return entrada.valor_normalizado

    def bruto(self, nome: str) -> str | None:
        entrada = self.campos.get(nome)
        if entrada is None or entrada.status_leitura is not StatusLeitura.LEGIVEL:
            return None
        return entrada.valor

    def data(self, nome: str) -> date | None:
        valor = self.norm(nome)
        if valor is None or valor == INDETERMINADA:
            return None
        return date.fromisoformat(valor)

    def tem_valor(self, nome: str) -> bool:
        entrada = self.campos.get(nome)
        return entrada is not None and entrada.valor is not None

    def uf(self, campo: str) -> UF | None:
        valor = self.norm(campo)
        if valor is not None:
            return UF(valor)
        if self.uf_leitura is not None and self.uf_leitura is not UF.NAO_IDENTIFICADO:
            return self.uf_leitura
        return None


@dataclass(frozen=True)
class Avaliacao:
    """Resultado de uma regra para um campo (ou só uma sinalização, com campo None)."""

    campo: str | None
    status: StatusValidacao
    motivo: MotivoCampo | None = None
    detalhe: str = ""
    sinalizacao: Sinalizacao | None = None


def valido(campo: str, detalhe: str = "") -> Avaliacao:
    return Avaliacao(campo, StatusValidacao.VALIDO, detalhe=detalhe)


def invalido(campo: str, motivo: MotivoCampo, detalhe: str = "") -> Avaliacao:
    return Avaliacao(campo, StatusValidacao.INVALIDO, motivo, detalhe)


def suspeito(campo: str, motivo: MotivoCampo, detalhe: str = "") -> Avaliacao:
    return Avaliacao(campo, StatusValidacao.SUSPEITO, motivo, detalhe)


def nao_verificavel(
    campo: str, detalhe: str = "", motivo: MotivoCampo = MotivoCampo.VALIDACAO_INDISPONIVEL
) -> Avaliacao:
    return Avaliacao(campo, StatusValidacao.NAO_VERIFICAVEL, motivo, detalhe)


def sinalizar(sinalizacao: Sinalizacao, detalhe: str = "") -> Avaliacao:
    return Avaliacao(None, StatusValidacao.VALIDO, detalhe=detalhe, sinalizacao=sinalizacao)


FuncaoRegra = Callable[[Contexto], Iterable[Avaliacao]]


@dataclass(frozen=True)
class Regra:
    id: str
    versao: str
    descricao: str
    fonte: str
    subtipos: frozenset[Subtipo]
    campos: tuple[str, ...]
    funcao: FuncaoRegra
    referencia: Referencia = Referencia.PROCESSAMENTO
    # Com referência DOCUMENTO: o campo de data do documento que define a vigência.
    campo_data_referencia: str | None = None
    vigencia_inicio: date | None = None  # inclusive
    vigencia_fim: date | None = None  # inclusive
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def com_vigencia(self) -> bool:
        return self.vigencia_inicio is not None or self.vigencia_fim is not None

    def data_referencia(self, ctx: Contexto) -> date | None:
        if self.referencia is Referencia.PROCESSAMENTO:
            return ctx.data_processamento
        assert self.campo_data_referencia is not None
        return ctx.data(self.campo_data_referencia)

    def vigente_em(self, referencia: date) -> bool:
        if self.vigencia_inicio is not None and referencia < self.vigencia_inicio:
            return False
        return self.vigencia_fim is None or referencia <= self.vigencia_fim


# --- Utilitários de data e texto ----------------------------------------------------------------


def somar_anos(dia: date, anos: int) -> date:
    """29/02 + N anos cai em 28/02 quando o ano de destino não é bissexto."""
    try:
        return dia.replace(year=dia.year + anos)
    except ValueError:
        return dia.replace(year=dia.year + anos, day=28)


def idade(nascimento: date, em: date) -> int:
    """Anos completos em `em` (no dia do aniversário, o ano já conta)."""
    anos = em.year - nascimento.year
    if (em.month, em.day) < (nascimento.month, nascimento.day):
        anos -= 1
    return anos


def sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto.upper()) if not unicodedata.combining(c)
    )
