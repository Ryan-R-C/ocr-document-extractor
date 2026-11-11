"""Interfaces entre o núcleo e os providers.

O núcleo só conhece estes tipos. Cada adaptador (`app/providers/<provider>/`) converte
para o formato do provider, aplica as regras de imagem, faz novas tentativas, contabiliza
tokens e custo e devolve `MetricasChamada` em toda chamada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.api.contrato import UF, Lado, StatusLeitura, Subtipo, TipoDocumento


class ModoChamada(StrEnum):
    """Padrão em produção; Batch/Flex nas rodadas de avaliação (implementação na Fase 1)."""

    PADRAO = "padrao"
    BATCH = "batch"
    FLEX = "flex"


class QualidadeCaptura(StrEnum):
    FOTO = "FOTO"
    COPIA_REPROGRAFICA = "COPIA_REPROGRAFICA"
    PRINT_DE_TELA = "PRINT_DE_TELA"
    NAO_IDENTIFICADO = "NAO_IDENTIFICADO"


class ErroProvider(Exception):
    """Provider falhou depois das novas tentativas do adaptador."""


class TempoEsgotadoProvider(ErroProvider):
    """Provider não respondeu dentro do tempo."""


class ImagemRecusadaLocalmente(ValueError):
    """O adaptador recusou a imagem antes de enviar (acima do limite do provider)."""


@dataclass(frozen=True)
class MetricasChamada:
    provider: str
    modelo: str
    etapa: str
    tokens_entrada: int = 0
    tokens_saida: int = 0
    custo_usd: float = 0.0
    custo_brl: float = 0.0
    latencia_ms: float = 0.0
    modo: ModoChamada = ModoChamada.PADRAO
    sucesso: bool = True
    erro: str | None = None
    # Detalhes para comparar com o plano: tokens de raciocínio (já incluídos na saída e
    # cobrados como saída), tokens de entrada vindos de cache e a estimativa local dos
    # tokens de imagem (patches × multiplicador).
    tokens_raciocinio: int = 0
    tokens_cache: int = 0
    tokens_imagem_estimados: int = 0
    tentativas: int = 1


@dataclass(frozen=True)
class Caixa:
    x: int
    y: int
    largura: int
    altura: int


@dataclass(frozen=True)
class Imagem:
    """Imagem já normalizada (PNG RGB), com o hash do arquivo recebido para rastreio."""

    conteudo: bytes
    mime: str
    largura: int
    altura: int
    sha256: str
    sha256_origem: str


# --- Leitor visual (VLM) -----------------------------------------------------------------------


@dataclass(frozen=True)
class CampoLido:
    status: StatusLeitura
    rotulo_visto: str
    texto_literal: str


class Rotacao(StrEnum):
    """Quanto girar no sentido horário para deixar o documento de pé."""

    GRAUS_0 = "0"
    GRAUS_90 = "90"
    GRAUS_180 = "180"
    GRAUS_270 = "270"
    NAO_IDENTIFICADO = "NAO_IDENTIFICADO"


class EtapaVisual(StrEnum):
    LEITURA = "leitura_visual"  # desenho único: classificação + campos
    CLASSIFICACAO = "classificacao"  # desenho em duas etapas, 1ª chamada
    EXTRACAO = "extracao"  # desenho em duas etapas, 2ª chamada (schema do subtipo)
    RELEITURA = "releitura"  # recorte de um campo, modelo de escalada


@dataclass(frozen=True)
class PedidoVisual:
    """Uma chamada ao leitor visual: imagem + instruções + schema estrito.

    O provider não interpreta o conteúdo: devolve o JSON no schema pedido. A interpretação
    é código do núcleo (`app/schemas/respostas.py`), igual para todo provider.
    """

    etapa: EtapaVisual
    imagem: Imagem
    tipo_documento: TipoDocumento
    schema: dict[str, Any]
    nome_schema: str
    prompt: str
    modelo: str
    modo: ModoChamada = ModoChamada.PADRAO
    subtipo: Subtipo | None = None  # extração e releitura
    campo: str | None = None  # releitura


@dataclass(frozen=True)
class RespostaVisual:
    dados: dict[str, Any]
    resposta_bruta: dict[str, Any]
    metricas: MetricasChamada


@dataclass(frozen=True)
class LeituraVisual:
    """Leitura visual já interpretada pelo núcleo (de uma ou de duas chamadas)."""

    subtipo: Subtipo
    uf: UF
    lado: Lado
    tipo_documento_detectado: TipoDocumento | None
    qualidade_captura: QualidadeCaptura
    campos: dict[str, CampoLido]
    resposta_bruta: dict[str, Any]
    metricas: MetricasChamada
    rotacao: Rotacao = Rotacao.GRAUS_0
    modelo_documento: str = ""


@dataclass(frozen=True)
class Releitura:
    campo: CampoLido
    resposta_bruta: dict[str, Any]
    metricas: MetricasChamada


@runtime_checkable
class LeitorVisual(Protocol):
    nome: str

    async def ler(self, pedido: PedidoVisual) -> RespostaVisual: ...


# --- Leitor de texto (OCR clássico) ------------------------------------------------------------


@dataclass(frozen=True)
class Palavra:
    id: int
    texto: str
    caixa: Caixa
    confianca: float


@dataclass(frozen=True)
class LeituraTexto:
    palavras: list[Palavra]
    resposta_bruta: dict[str, Any]
    metricas: MetricasChamada


@runtime_checkable
class LeitorTexto(Protocol):
    nome: str

    async def ler(self, imagem: Imagem) -> LeituraTexto: ...


# --- Localizador -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Localizacao:
    caixas: dict[str, Caixa] = field(default_factory=dict)
    metricas: MetricasChamada | None = None


@runtime_checkable
class Localizador(Protocol):
    nome: str

    async def localizar(
        self,
        imagem: Imagem,
        leitura_visual: LeituraVisual,
        leitura_texto: LeituraTexto,
        campos: list[str],
    ) -> Localizacao: ...


@dataclass(frozen=True)
class Dependencias:
    """Providers que a esteira usa, já compostos com tempo limite, fallback e limites."""

    leitor_visual: LeitorVisual
    leitor_texto: LeitorTexto
    localizador: Localizador
