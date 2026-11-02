"""Formato das respostas gravadas usadas pelos providers fake.

Um arquivo `<sha256 do arquivo recebido>.json` por imagem. Nenhuma gravação versionada no
repositório pode conter dados pessoais reais: só dados sintéticos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.contrato import UF, Lado, StatusLeitura, Subtipo, TipoDocumento
from app.providers.interfaces import QualidadeCaptura

Falha = Literal["indisponivel", "tempo"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CampoGravado(_Base):
    status: StatusLeitura
    rotulo_visto: str = ""
    texto_literal: str = ""


class LeituraVisualGravada(_Base):
    tipo_documento_detectado: TipoDocumento | None = None
    subtipo: Subtipo
    uf: UF = UF.NAO_IDENTIFICADO
    lado: Lado = Lado.NAO_IDENTIFICADO
    qualidade_captura: QualidadeCaptura = QualidadeCaptura.FOTO
    campos: dict[str, CampoGravado] = Field(default_factory=dict)
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
    latencia_ms: float = 0.0


class PalavraGravada(_Base):
    id: int
    texto: str
    caixa: tuple[int, int, int, int]
    confianca: float = 1.0


class LeituraTextoGravada(_Base):
    palavras: list[PalavraGravada] = Field(default_factory=list)
    latencia_ms: float = 0.0


class ReleituraGravada(CampoGravado):
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
    latencia_ms: float = 0.0


class Gravacao(_Base):
    leitura_visual: LeituraVisualGravada | None = None
    leitura_texto: LeituraTextoGravada | None = None
    caixas: dict[str, tuple[int, int, int, int]] = Field(default_factory=dict)
    releituras: dict[str, ReleituraGravada] = Field(default_factory=dict)
    falhas: dict[Literal["leitura_visual", "leitura_texto", "releitura"], Falha] = Field(
        default_factory=dict
    )


def caminho(diretorio: Path, sha256: str) -> Path:
    return diretorio / f"{sha256}.json"


def carregar(diretorio: Path, sha256: str) -> Gravacao | None:
    arquivo = caminho(diretorio, sha256)
    if not arquivo.is_file():
        return None
    return Gravacao.model_validate_json(arquivo.read_text(encoding="utf-8"))


def salvar(diretorio: Path, sha256: str, gravacao: Gravacao) -> Path:
    diretorio.mkdir(parents=True, exist_ok=True)
    arquivo = caminho(diretorio, sha256)
    dados = gravacao.model_dump(mode="json", exclude_defaults=True)
    arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    return arquivo
