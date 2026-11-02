"""Invariante 6: schemas enviados ao VLM. Normalizadores. Configuração."""

from __future__ import annotations

from typing import Any

import pytest

from app.api.contrato import Subtipo, TipoDocumento
from app.config import Settings, interpretar_duracao
from app.prompts import carregar, hash_prompts
from app.schemas.campos import schema_campos, schema_leitura, schema_releitura
from app.schemas.normalizacao import TipoValor, normalizar

PROIBIDOS_NO_VALOR = {"pattern", "format", "enum", "const", "minLength", "maxLength"}
RACIOCINIO = ("raciocinio", "reasoning", "justificativa", "explicacao", "pensamento")


def _percorrer(no: Any, caminho: str = ""):
    if isinstance(no, dict):
        yield caminho, no
        for chave, valor in no.items():
            yield from _percorrer(valor, f"{caminho}/{chave}")
    elif isinstance(no, list):
        for i, valor in enumerate(no):
            yield from _percorrer(valor, f"{caminho}/{i}")


def _schemas() -> list[dict[str, Any]]:
    return (
        [schema_leitura(t) for t in TipoDocumento]
        + [schema_releitura()]
        + [schema_campos(s) for s in Subtipo]
    )


@pytest.mark.parametrize("schema", _schemas())
def test_objetos_estritos(schema) -> None:
    for caminho, no in _percorrer(schema):
        if no.get("type") == "object":
            assert no.get("additionalProperties") is False, caminho
            assert set(no.get("required", [])) == set(no.get("properties", {})), caminho


@pytest.mark.parametrize("schema", _schemas())
def test_sem_restricao_nos_valores(schema) -> None:
    for caminho, no in _percorrer(schema):
        props = no.get("properties", {})
        for nome in ("texto_literal", "rotulo_visto"):
            if nome in props:
                assert not PROIBIDOS_NO_VALOR & set(props[nome]), f"{caminho}/{nome}"


@pytest.mark.parametrize("schema", _schemas())
def test_listas_fechadas_tem_saida(schema) -> None:
    for caminho, no in _percorrer(schema):
        if "enum" not in no:
            continue
        if caminho.endswith("/status"):
            assert {"ILEGIVEL", "AUSENTE"} <= set(no["enum"]), caminho
        elif caminho.endswith("/subtipo"):
            continue  # discriminador de cada variante; NAO_IDENTIFICADO é uma variante própria
        else:
            assert "NAO_IDENTIFICADO" in no["enum"], caminho


@pytest.mark.parametrize("tipo", list(TipoDocumento))
def test_leitura_tem_variante_nao_identificado(tipo) -> None:
    variantes = schema_leitura(tipo)["properties"]["documento"]["anyOf"]
    assert ["NAO_IDENTIFICADO"] in [v["properties"]["subtipo"]["enum"] for v in variantes]


@pytest.mark.parametrize("schema", _schemas())
def test_sem_campo_de_raciocinio(schema) -> None:
    for caminho, no in _percorrer(schema):
        for nome in no.get("properties", {}):
            assert not any(r in nome.lower() for r in RACIOCINIO), f"{caminho}/{nome}"


def test_prompts_sem_dados_realistas() -> None:
    import re

    for nome in ("leitura_visual", "releitura"):
        texto = carregar(nome)
        assert not re.search(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}", texto)
        assert not re.search(r"\d{2}/\d{2}/\d{4}", texto)
    assert len(hash_prompts()) == 8


@pytest.mark.parametrize(
    ("tipo", "entrada", "esperado"),
    [
        (TipoValor.CPF, "123.456.789-09", "12345678909"),
        (TipoValor.CPF, "123.456.789-0", None),
        (TipoValor.CPF, "123.456.789-O9", None),  # letra O não vira zero
        (TipoValor.DATA, "12/03/2015", "2015-03-12"),
        (TipoValor.DATA, "12/03/2O15", None),
        (TipoValor.DATA, "31/02/2015", None),
        (TipoValor.DATA, "12.03.2015", "2015-03-12"),
        (TipoValor.TEXTO, "  maria   da  silva ", "MARIA DA SILVA"),
        (TipoValor.UF, "sp", "SP"),
        (TipoValor.UF, "XX", None),
        (TipoValor.ALFANUMERICO, "12.345.678-X", "12345678X"),
        (TipoValor.CNPJ, "12.ABC.345/01DE-35", "12ABC34501DE35"),
        (TipoValor.CATEGORIA_CNH, "a b", "AB"),
        (TipoValor.CATEGORIA_CNH, "Z", None),
        (TipoValor.DIGITOS, "12 34", "1234"),
        (TipoValor.TEXTO, "MAR?A", None),
        (TipoValor.TEXTO, "   ", None),
    ],
)
def test_normalizacao(tipo, entrada, esperado) -> None:
    assert normalizar(tipo, entrada) == esperado


def test_ttl() -> None:
    oito, trinta = interpretar_duracao("8h"), interpretar_duracao("30d")
    assert oito is not None and oito.total_seconds() == 8 * 3600
    assert trinta is not None and trinta.days == 30
    assert interpretar_duracao("null") is None
    assert interpretar_duracao(None) is None
    with pytest.raises(ValueError):
        interpretar_duracao("8 horas")
    with pytest.raises(ValueError):
        interpretar_duracao("0h")


def test_artefatos_ttl_aceita_so_valores_do_plano(monkeypatch) -> None:
    for valor, esperado in (("8h", 8), ("16h", 16)):
        monkeypatch.setenv("ARTEFATOS_TTL", valor)
        s = Settings(_env_file=None)
        assert s.ttl_artefatos is not None
        assert s.ttl_artefatos.total_seconds() == esperado * 3600
    monkeypatch.setenv("ARTEFATOS_TTL", "null")
    assert Settings(_env_file=None).ttl_artefatos is None
    monkeypatch.setenv("ARTEFATOS_TTL", "24h")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_configuracao_por_ambiente(monkeypatch) -> None:
    monkeypatch.setenv("PRECOS_MODELOS", '{"m": {"entrada": 1, "saida": 2}}')
    monkeypatch.setenv("PROVIDER_LIMITES", '{"openai": {"concorrencia": 3}}')
    monkeypatch.setenv("PROVIDER_FALLBACK", "")
    monkeypatch.setenv("HTTP_STATUS_FALHA_EXTRACAO", "400")
    s = Settings(_env_file=None)
    assert s.precos_modelos["m"].saida == 2
    assert s.limite_provider("openai").concorrencia == 3
    assert s.limite_provider("outro").concorrencia == 8
    assert s.provider_fallback is None
    assert s.http_status_falha_extracao == 400
    monkeypatch.setenv("HTTP_STATUS_FALHA_EXTRACAO", "500")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_env_example_cobre_a_configuracao() -> None:
    from pathlib import Path

    exemplo = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    declaradas = {
        linha.split("=", 1)[0].lstrip("# ").strip()
        for linha in exemplo.splitlines()
        if "=" in linha and linha.lstrip("# ").split("=", 1)[0].strip().isupper()
    }
    esperadas = {nome.upper() for nome in Settings.model_fields}
    assert esperadas <= declaradas, esperadas - declaradas
