"""`versao_pipeline`: identifica provider, modelo, prompt, schemas e regras usados."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.config import Settings
from app.core import ancoragem
from app.prompts import hash_prompts
from app.regras.motor import VERSAO_REGRAS
from app.schemas.campos import VERSAO_SCHEMAS


def _versao_servico() -> str:
    # Na imagem Docker o projeto não é instalado como pacote: lê o pyproject.toml.
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
    try:
        return version("extracao-documentos")
    except PackageNotFoundError:  # pragma: no cover
        return "0.0.0"


VERSAO_SERVICO = _versao_servico()


def versao_pipeline(
    settings: Settings,
    provider_visual: str | None = None,
    modelo_visual: str | None = None,
    provider_texto: str | None = None,
) -> str:
    """Sem argumentos, descreve a configuração; com argumentos, o que de fato rodou
    (por exemplo, quando o fallback respondeu no lugar do provider principal)."""
    partes = [
        VERSAO_SERVICO,
        f"{provider_visual or settings.leitor_visual_provider}:"
        f"{modelo_visual or settings.leitor_visual_modelo}",
        f"escalada:{settings.leitor_visual_modelo_escalada}",
        f"ocr:{provider_texto or settings.leitor_texto_provider}",
        f"prompt:{hash_prompts()}",
        f"schemas:{VERSAO_SCHEMAS}",
        f"ancoragem:{ancoragem.VERSAO_ANCORAGEM}",
        f"regras:{VERSAO_REGRAS}",
    ]
    return "+".join(partes)
