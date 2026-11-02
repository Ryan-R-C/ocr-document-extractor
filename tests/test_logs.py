"""Invariante 10: CPF, nomes e datas nunca aparecem em log."""

from __future__ import annotations

import io
import logging

import pytest

from app.logs import configurar_logs, mascarar_texto, obter_logger
from tests.conftest import NOME_SINTETICO, b64, cpf_sintetico, gravacao_cpf, imagem_png

CPF = cpf_sintetico(42)
CPF_DIGITOS = CPF.replace(".", "").replace("-", "")


@pytest.fixture
def saida_log() -> io.StringIO:
    buffer = io.StringIO()
    configurar_logs("DEBUG", json=True, stream=buffer)
    return buffer


def _sem_dados_pessoais(texto: str) -> None:
    assert CPF not in texto
    assert CPF_DIGITOS not in texto
    assert NOME_SINTETICO not in texto
    for parte in NOME_SINTETICO.split():
        assert parte not in texto


@pytest.mark.parametrize(
    "texto",
    [
        f"cpf {CPF} lido",
        f"cpf {CPF_DIGITOS} lido",
        "nascido em 12/03/1990",
        "expedição 1990-03-12 registrada",
        "rg 12.345.678-X",
        "cnpj 12.ABC.345/01DE-35",
        "matrícula 12345678901234567890123456789012",
    ],
)
def test_mascara_padroes(texto: str) -> None:
    mascarado = mascarar_texto(texto)
    assert not any(c.isdigit() for c in mascarado.split(" ", 1)[1].replace("***", "")), mascarado


def test_preserva_ids_e_hashes() -> None:
    texto = "id 9b1c2d3e-1234-4abc-8def-1234567890ab sha 0123456789abcdef0123456789abcdef"
    assert mascarar_texto(texto) == texto


def test_chaves_sensiveis_e_texto_livre(saida_log: io.StringIO) -> None:
    log = obter_logger("teste")
    log.info(
        "leitura",
        nome=NOME_SINTETICO,
        cpf=CPF,
        campos={"cpf": {"valor": CPF, "texto_literal": CPF}},
        detalhe=f"valor {CPF} recebido",
        lista=[CPF_DIGITOS],
        id_processamento="9b1c2d3e-1234-4abc-8def-1234567890ab",
    )
    texto = saida_log.getvalue()
    _sem_dados_pessoais(texto)
    assert "9b1c2d3e-1234-4abc-8def-1234567890ab" in texto
    assert '"timestamp"' in texto  # o timestamp ISO não é confundido com data


def test_logging_padrao_tambem_mascara(saida_log: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").error("falhou com %s", CPF)
    logging.getLogger("terceiro").warning("dados", extra={"nome": NOME_SINTETICO})
    _sem_dados_pessoais(saida_log.getvalue())


def test_excecao_com_dado_pessoal_mascara(saida_log: io.StringIO) -> None:
    log = obter_logger("teste")
    try:
        raise ValueError(f"cpf inválido {CPF}")
    except ValueError:
        log.exception("erro")
    _sem_dados_pessoais(saida_log.getvalue())


def test_requisicao_completa_nao_vaza(settings, servico, gravar) -> None:
    from fastapi.testclient import TestClient

    from app.main import criar_app

    app = criar_app(settings=settings.model_copy(update={"log_level": "DEBUG"}), servico=servico)
    buffer = io.StringIO()
    configurar_logs("DEBUG", json=True, stream=buffer)
    cliente = TestClient(app, raise_server_exceptions=False)

    conteudo = imagem_png(77)
    gravar(conteudo, gravacao_cpf(CPF, NOME_SINTETICO))
    ok = cliente.post(
        "/v1/extracoes",
        json={"tipo_documento": "CPF", "imagem_base64": b64(conteudo), "id_externo": "x"},
    )
    assert ok.status_code == 200
    assert ok.json()["campos"]["cpf"]["valor"] == CPF  # a resposta tem o dado; o log, não
    cliente.post("/v1/extracoes", json={"tipo_documento": CPF, "imagem_base64": b64(conteudo)})
    cliente.post("/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": NOME_SINTETICO})

    texto = buffer.getvalue()
    assert "extracao_concluida" in texto
    _sem_dados_pessoais(texto)
