"""Monta os providers a partir da configuração. Único lugar que conhece os nomes."""

from __future__ import annotations

from app.config import Settings
from app.providers.composicao import LeitorTextoComposto, LeitorVisualComposto
from app.providers.interfaces import (
    Dependencias,
    Imagem,
    LeitorTexto,
    LeitorVisual,
    LeituraTexto,
    LeituraVisual,
    Localizacao,
    Localizador,
    MetricasChamada,
)
from app.providers.limites import LimitadorProvider


class ProviderNaoImplementado(RuntimeError):
    pass


_FASE = {"paddleocr": "Fase 2", "tesseract": "Fase 4"}


class LocalizadorPorOCR:
    """Caixas a partir das palavras do OCR clássico.

    STUB da Fase 0: não localiza nada (sem recortes, sem releitura). Implementação na Fase 2.
    """

    nome = "ocr"

    async def localizar(
        self,
        imagem: Imagem,
        leitura_visual: LeituraVisual,
        leitura_texto: LeituraTexto,
        campos: list[str],
    ) -> Localizacao:
        return Localizacao()


class LeitorTextoNenhum:
    """Sem segundo leitor (Fase 1): nenhuma palavra. Nada ancora, então nada é aprovado."""

    nome = "nenhum"

    async def ler(self, imagem: Imagem) -> LeituraTexto:
        return LeituraTexto(
            palavras=[],
            resposta_bruta={},
            metricas=MetricasChamada(provider="nenhum", modelo="nenhum", etapa="leitura_texto"),
        )


def _visual(nome: str, settings: Settings) -> LeitorVisual:
    if nome == "fake":
        from app.providers.fake import FakeLeitorVisual

        return FakeLeitorVisual(settings)
    if nome == "openai":
        from app.providers.openai import LeitorVisualOpenAI

        return LeitorVisualOpenAI(settings)
    raise ProviderNaoImplementado(f"leitor visual {nome!r}: {_FASE.get(nome, 'desconhecido')}")


def _texto(nome: str, settings: Settings) -> LeitorTexto:
    if nome == "fake":
        from app.providers.fake import FakeLeitorTexto

        return FakeLeitorTexto(settings)
    if nome == "nenhum":
        return LeitorTextoNenhum()
    raise ProviderNaoImplementado(f"leitor de texto {nome!r}: {_FASE.get(nome, 'desconhecido')}")


def _localizador(nome: str, settings: Settings) -> Localizador:
    if nome == "fake":
        from app.providers.fake import FakeLocalizador

        return FakeLocalizador(settings)
    if nome == "ocr":
        return LocalizadorPorOCR()
    raise ProviderNaoImplementado(f"localizador {nome!r}")


def criar_dependencias(settings: Settings) -> Dependencias:
    principal = _visual(settings.leitor_visual_provider, settings)
    fallback = _visual(settings.provider_fallback, settings) if settings.provider_fallback else None
    limitadores = {
        p.nome: LimitadorProvider(settings.limite_provider(p.nome))
        for p in (principal, fallback)
        if p is not None
    }
    return Dependencias(
        leitor_visual=LeitorVisualComposto(
            principal, fallback, settings.provider_timeout_s, limitadores
        ),
        leitor_texto=LeitorTextoComposto(
            _texto(settings.leitor_texto_provider, settings), settings.provider_timeout_s
        ),
        localizador=_localizador(settings.localizador_provider, settings),
    )
