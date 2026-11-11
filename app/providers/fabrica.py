"""Monta os providers a partir da configuração. Único lugar que conhece os nomes."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.providers.composicao import LeitorTextoComposto, LeitorVisualComposto
from app.providers.interfaces import (
    Dependencias,
    Imagem,
    LeitorTexto,
    LeitorVisual,
    LeituraTexto,
    Localizador,
    MetricasChamada,
)
from app.providers.limites import LimitadorProvider


class ProviderNaoImplementado(RuntimeError):
    pass


_FASE = {"tesseract": "Fase 4"}


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
    if nome == "paddleocr":
        from app.providers.paddleocr import LeitorTextoPaddleOCR

        return LeitorTextoPaddleOCR(settings)
    raise ProviderNaoImplementado(f"leitor de texto {nome!r}: {_FASE.get(nome, 'desconhecido')}")


def _localizador(nome: str, settings: Settings) -> Localizador:
    if nome == "fake":
        from app.providers.fake import FakeLocalizador

        return FakeLocalizador(settings)
    if nome == "ocr":
        from app.core.localizacao import LocalizadorPorOCR

        return LocalizadorPorOCR(settings)
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


def criar_dependencias_batch(settings: Settings, diretorio_estado: Path) -> Dependencias:
    """Dependências para a rodada Batch do avaliador: o leitor visual vira um coletor de
    lote (sem o tempo limite da composição, porque um lote leva até 24 h)."""
    if settings.leitor_visual_provider != "openai":
        raise ProviderNaoImplementado(
            f"Batch não disponível para o leitor visual {settings.leitor_visual_provider!r}"
        )
    from app.providers.openai import LeitorVisualOpenAI
    from app.providers.openai.batch import ColetorBatch

    base = criar_dependencias(settings.model_copy(update={"leitor_visual_provider": "fake"}))
    coletor = ColetorBatch(LeitorVisualOpenAI(settings), diretorio_estado)
    return Dependencias(
        leitor_visual=coletor, leitor_texto=base.leitor_texto, localizador=base.localizador
    )
