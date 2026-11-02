"""Fixtures comuns. Nada aqui chama provider real nem usa documento real."""

from __future__ import annotations

import base64
import hashlib
import io
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from PIL import Image, ImageDraw

from app.armazenamento.db import criar_engine, criar_sessoes
from app.armazenamento.modelos import Base
from app.armazenamento.objetos import Objetos
from app.armazenamento.repositorio import Repositorio
from app.config import Settings
from app.main import criar_app
from app.montagem import criar_esteira
from app.providers.fake import gravacoes
from app.providers.fake.gravacoes import Gravacao
from app.servico import ServicoExtracao
from avaliacao.sintetico import gerar_cpf

NOME_SINTETICO = "ZILAVO PERENU QUIDABE"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def imagem_png(semente: int = 0, tamanho: tuple[int, int] = (400, 260)) -> bytes:
    """PNG sintético e determinístico; a semente muda o conteúdo (e o hash)."""
    imagem = Image.new("RGB", tamanho, (250, 250, 245))
    desenho = ImageDraw.Draw(imagem)
    desenho.text((20, 20), f"AMOSTRA SINTETICA {semente}", fill=(0, 0, 0))
    desenho.rectangle((20, 60, 20 + semente % 200, 80), fill=(30, 30, 30))
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()


def sha256(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def b64(conteudo: bytes) -> str:
    return base64.b64encode(conteudo).decode()


def cpf_sintetico(semente: int = 1) -> str:
    return gerar_cpf(random.Random(semente))


def gravacao_cpf(cpf: str, nome: str = NOME_SINTETICO, **extra: Any) -> Gravacao:
    """Cartão CPF lido com perfeição pelo fake (VLM e OCR)."""
    dados: dict[str, Any] = {
        "leitura_visual": {
            "tipo_documento_detectado": "CPF",
            "subtipo": "CPF_CARTAO",
            "uf": "SP",
            "lado": "FRENTE",
            "qualidade_captura": "FOTO",
            "campos": {
                "cpf": {"status": "LEGIVEL", "rotulo_visto": "CPF", "texto_literal": cpf},
                "nome": {"status": "LEGIVEL", "rotulo_visto": "NOME", "texto_literal": nome},
                "data_nascimento": {"status": "AUSENTE"},
            },
            "tokens_entrada": 3420,
            "tokens_saida": 600,
            "latencia_ms": 1000,
        },
        "leitura_texto": {
            "palavras": [
                {"id": 0, "texto": "CPF", "caixa": [20, 100, 40, 20]},
                {"id": 1, "texto": cpf, "caixa": [70, 100, 160, 20]},
            ]
        },
        "caixas": {"cpf": [70, 100, 160, 20], "nome": [20, 140, 300, 20]},
    }
    dados.update(extra)
    return Gravacao.model_validate(dados)


@pytest.fixture
def dir_gravacoes(tmp_path: Path) -> Path:
    destino = tmp_path / "gravacoes"
    destino.mkdir()
    return destino


@pytest.fixture
def settings(dir_gravacoes: Path) -> Settings:
    return Settings(
        _env_file=None,
        leitor_visual_provider="fake",
        leitor_texto_provider="fake",
        localizador_provider="fake",
        fake_gravacoes_dir=str(dir_gravacoes),
        database_url="sqlite+pysqlite:///:memory:",
        s3_endpoint_url=None,
        s3_bucket="teste-auditoria",
        artefatos_ttl="8h",
        casos_revisao_ttl="30d",
        provider_timeout_s=2.0,
        log_json=True,
    )


@pytest.fixture
def aws() -> Iterator[None]:
    with mock_aws():
        yield


@pytest.fixture
def objetos(settings: Settings, aws: None) -> Objetos:
    cliente = boto3.client("s3", region_name=settings.s3_regiao)
    objetos = Objetos(settings, cliente=cliente)
    objetos.preparar_bucket()
    return objetos


@pytest.fixture
def repositorio(settings: Settings) -> Repositorio:
    engine = criar_engine(settings)
    Base.metadata.create_all(engine)
    return Repositorio(criar_sessoes(engine))


@pytest.fixture
def servico(settings: Settings, repositorio: Repositorio, objetos: Objetos) -> ServicoExtracao:
    return ServicoExtracao(settings, criar_esteira(settings), repositorio, objetos)


def montar_cliente(settings: Settings, servico: ServicoExtracao) -> TestClient:
    async def saude() -> dict[str, str]:
        return {"banco": "ok", "objetos": "ok"}

    app = criar_app(settings=settings, servico=servico, verificar_saude=saude)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def cliente(settings: Settings, servico: ServicoExtracao) -> TestClient:
    return montar_cliente(settings, servico)


@pytest.fixture
def gravar(dir_gravacoes: Path):
    def _gravar(conteudo: bytes, gravacao: Gravacao) -> str:
        chave = sha256(conteudo)
        gravacoes.salvar(dir_gravacoes, chave, gravacao)
        return chave

    return _gravar
