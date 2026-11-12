"""Contrato: OpenAPI versionado, exemplos do plano e respostas reais validam contra o schema."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from app.api import openapi
from app.api.contrato import EXEMPLOS
from tests.conftest import b64, cpf_sintetico, gravacao_cpf, imagem_png

URI = "urn:openapi"


@pytest.fixture(scope="module")
def documento() -> dict[str, Any]:
    return openapi.gerar()


def validador(documento: dict[str, Any], componente: str) -> Draft202012Validator:
    registro = Registry().with_resource(URI, Resource.from_contents(documento, DRAFT202012))
    return Draft202012Validator(
        {"$ref": f"{URI}#/components/schemas/{componente}"}, registry=registro
    )


def test_openapi_versionado_igual_ao_gerado(documento: dict[str, Any]) -> None:
    versionado = openapi.ARQUIVO.read_text(encoding="utf-8")
    assert versionado == openapi.serializar(documento), (
        "O contrato mudou. Rode `make openapi`, revise o diff de docs/openapi.json e "
        "registre a mudança em docs/decisoes.md."
    )


@pytest.mark.parametrize("componente", sorted(EXEMPLOS))
def test_exemplos_do_plano_validam(documento: dict[str, Any], componente: str) -> None:
    validador(documento, componente).validate(EXEMPLOS[componente])


def test_exemplos_embutidos_no_openapi_validam(documento: dict[str, Any]) -> None:
    for nome, schema in documento["components"]["schemas"].items():
        for exemplo in schema.get("examples", []):
            validador(documento, nome).validate(exemplo)


def test_rota_documenta_todos_os_codigos(documento: dict[str, Any]) -> None:
    respostas = documento["paths"]["/v1/extracoes"]["post"]["responses"]
    assert set(respostas) == {"200", "400", "404", "413", "422", "500", "502", "504"}
    for codigo in ("400", "404", "413", "422", "500", "502", "504"):
        ref = respostas[codigo]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/RespostaErro")


def test_rotas_assincronas_documentadas(documento: dict[str, Any]) -> None:
    def ref(respostas: dict[str, Any], codigo: str) -> str:
        return str(respostas[codigo]["content"]["application/json"]["schema"]["$ref"])

    post = documento["paths"]["/v1/extracoes/assincronas"]["post"]["responses"]
    assert set(post) == {"202", "400", "413", "422", "500"} - {"422"}
    assert ref(post, "202").endswith("/RespostaAceita")
    get = documento["paths"]["/v1/extracoes/{id_processamento}"]["get"]["responses"]
    assert set(get) == {"200", "202", "404", "422", "500", "502", "504"}
    assert ref(get, "200").endswith("/RespostaExtracao")
    assert ref(get, "202").endswith("/RespostaAceita")
    for codigo in ("404", "422", "500", "502", "504"):
        assert ref(get, codigo).endswith("/RespostaErro")


def test_enums_do_plano(documento: dict[str, Any]) -> None:
    schemas = documento["components"]["schemas"]
    assert schemas["StatusDocumento"]["enum"] == ["APROVADO", "REVISAO"]
    assert schemas["StatusLeitura"]["enum"] == ["LEGIVEL", "PARCIAL", "ILEGIVEL", "AUSENTE"]
    assert schemas["StatusValidacao"]["enum"] == [
        "VALIDO",
        "INVALIDO",
        "SUSPEITO",
        "NAO_VERIFICAVEL",
    ]
    assert set(schemas["Sinalizacao"]["enum"]) == {
        "COPIA_REPROGRAFICA",
        "PRINT_DE_TELA",
        "RG_ANTIGO",
        "DOCUMENTO_VENCIDO",
        "QR_CONFERIDO",
        "MRZ_CONFERIDA",
    }
    motivos = set(schemas["MotivoErro"]["enum"])
    for codigo in (
        "BASE64_INVALIDO",
        "TIPO_DESCONHECIDO",
        "FORMATO_NAO_SUPORTADO",
        "ARQUIVO_GRANDE_DEMAIS",
        "NENHUM_DOCUMENTO",
        "TIPO_DIVERGENTE",
        "LADO_SEM_DADOS",
        "IMAGEM_DESFOCADA",
        "DOCUMENTO_CORTADO",
        "REFLEXO_EXCESSIVO",
        "CAMPO_CRITICO_ILEGIVEL",
        "PROVIDER_INDISPONIVEL",
        "TEMPO_ESGOTADO",
    ):
        assert codigo in motivos


def test_respostas_reais_validam(documento, cliente, gravar) -> None:
    conteudo = imagem_png(1)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    ok = cliente.post(
        "/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": b64(conteudo)}
    )
    assert ok.status_code == 200
    validador(documento, "RespostaExtracao").validate(ok.json())

    erro = cliente.post("/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": "@@"})
    assert erro.status_code == 400
    validador(documento, "RespostaErro").validate(erro.json())

    saude = cliente.get("/health")
    validador(documento, "RespostaSaude").validate(saude.json())


def test_resposta_200_com_valores_nulos_explicitos(cliente, gravar) -> None:
    conteudo = imagem_png(2)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    corpo = cliente.post(
        "/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": b64(conteudo)}
    ).json()
    ausente = corpo["campos"]["data_nascimento"]
    assert ausente["valor"] is None and ausente["valor_normalizado"] is None
    assert json.dumps(corpo)  # serializável
