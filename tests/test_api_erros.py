"""Código HTTP de cada erro, sempre com `id_processamento`."""

from __future__ import annotations

import base64
import uuid

import pytest

from app.api.contrato import Lado, StatusLeitura, Subtipo, TipoDocumento
from app.config import Settings
from app.montagem import criar_esteira
from app.servico import ServicoExtracao
from tests.conftest import b64, cpf_sintetico, gravacao_cpf, imagem_png, montar_cliente


def _post(cliente, tipo: str = "CPF", imagem: str | None = None, **extra):
    corpo = {
        "tipo_documento": tipo,
        "imagem_base64": imagem if imagem is not None else b64(imagem_png()),
    }
    corpo.update(extra)
    return cliente.post("/v1/extracoes", json=corpo)


def _assert_erro(resposta, status: int, erro: str, motivo: str, acao: str) -> dict:
    assert resposta.status_code == status, resposta.text
    corpo = resposta.json()
    uuid.UUID(corpo["id_processamento"])
    assert corpo["erro"] == erro
    assert motivo in [m["codigo"] for m in corpo["motivos"]]
    assert corpo["acao_sugerida"] == acao
    return corpo


def test_base64_invalido(cliente) -> None:
    _assert_erro(
        _post(cliente, imagem="não é base64!"),
        400,
        "REQUISICAO_INVALIDA",
        "BASE64_INVALIDO",
        "CORRIGIR_REQUISICAO",
    )


def test_base64_vazio(cliente) -> None:
    _assert_erro(
        _post(cliente, imagem=""),
        400,
        "REQUISICAO_INVALIDA",
        "BASE64_INVALIDO",
        "CORRIGIR_REQUISICAO",
    )


def test_tipo_desconhecido(cliente) -> None:
    _assert_erro(
        _post(cliente, tipo="PASSAPORTE"),
        400,
        "REQUISICAO_INVALIDA",
        "TIPO_DESCONHECIDO",
        "CORRIGIR_REQUISICAO",
    )


def test_formato_nao_suportado(cliente) -> None:
    gif = base64.b64encode(b"GIF89a" + b"\x00" * 64).decode()
    _assert_erro(
        _post(cliente, imagem=gif),
        400,
        "REQUISICAO_INVALIDA",
        "FORMATO_NAO_SUPORTADO",
        "CORRIGIR_REQUISICAO",
    )


def test_imagem_corrompida_com_assinatura_valida(cliente) -> None:
    falso_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"lixo" * 20).decode()
    _assert_erro(
        _post(cliente, imagem=falso_png),
        400,
        "REQUISICAO_INVALIDA",
        "FORMATO_NAO_SUPORTADO",
        "CORRIGIR_REQUISICAO",
    )


def test_campo_obrigatorio_ausente(cliente) -> None:
    resposta = cliente.post("/v1/extracoes", json={"tipo_documento": "CPF"})
    corpo = _assert_erro(
        resposta, 400, "REQUISICAO_INVALIDA", "REQUISICAO_MALFORMADA", "CORRIGIR_REQUISICAO"
    )
    assert corpo["motivos"][0]["campo"] == "imagem_base64"


def test_json_malformado(cliente) -> None:
    resposta = cliente.post(
        "/v1/extracoes", content=b"{nao json", headers={"content-type": "application/json"}
    )
    _assert_erro(
        resposta, 400, "REQUISICAO_INVALIDA", "REQUISICAO_MALFORMADA", "CORRIGIR_REQUISICAO"
    )


def test_campo_extra_recusado(cliente) -> None:
    _assert_erro(
        _post(cliente, outro="x"),
        400,
        "REQUISICAO_INVALIDA",
        "REQUISICAO_MALFORMADA",
        "CORRIGIR_REQUISICAO",
    )


def test_erro_de_validacao_nao_ecoa_conteudo(cliente) -> None:
    resposta = _post(cliente, tipo="12345678909")
    assert "12345678909" not in resposta.text


def test_arquivo_grande_demais_decodificado(settings: Settings, repositorio, objetos) -> None:
    pequeno = settings.model_copy(update={"arquivo_max_bytes": 1000})
    servico = ServicoExtracao(pequeno, criar_esteira(pequeno), repositorio, objetos)
    cliente = montar_cliente(pequeno, servico)
    grande = b64(b"\x89PNG\r\n\x1a\n" + bytes(1200))
    _assert_erro(
        _post(cliente, imagem=grande),
        413,
        "ARQUIVO_GRANDE_DEMAIS",
        "ARQUIVO_GRANDE_DEMAIS",
        "REENVIAR_IMAGEM",
    )


def test_arquivo_grande_demais_pelo_tamanho_do_corpo(
    settings: Settings, repositorio, objetos
) -> None:
    pequeno = settings.model_copy(update={"arquivo_max_bytes": 1000})
    servico = ServicoExtracao(pequeno, criar_esteira(pequeno), repositorio, objetos)
    cliente = montar_cliente(pequeno, servico)
    enorme = "A" * 200_000
    _assert_erro(
        _post(cliente, imagem=enorme),
        413,
        "ARQUIVO_GRANDE_DEMAIS",
        "ARQUIVO_GRANDE_DEMAIS",
        "REENVIAR_IMAGEM",
    )


def test_resolucao_acima_do_limite(settings: Settings, repositorio, objetos) -> None:
    limitado = settings.model_copy(update={"imagem_pixels_max": 10_000})
    servico = ServicoExtracao(limitado, criar_esteira(limitado), repositorio, objetos)
    cliente = montar_cliente(limitado, servico)
    corpo = _assert_erro(
        _post(cliente), 413, "ARQUIVO_GRANDE_DEMAIS", "ARQUIVO_GRANDE_DEMAIS", "REENVIAR_IMAGEM"
    )
    assert corpo["motivos"][0]["detalhe"] == "resolução"


def test_nenhum_documento_quando_nao_ha_gravacao(cliente) -> None:
    _assert_erro(
        _post(cliente, imagem=b64(imagem_png(99))),
        422,
        "DOCUMENTO_NAO_PROCESSAVEL",
        "NENHUM_DOCUMENTO",
        "REENVIAR_IMAGEM",
    )


def test_tipo_divergente(cliente, gravar) -> None:
    conteudo = imagem_png(3)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    _assert_erro(
        _post(cliente, tipo="IDENTITY_DOCUMENT", imagem=b64(conteudo)),
        422,
        "DOCUMENTO_NAO_PROCESSAVEL",
        "TIPO_DIVERGENTE",
        "CORRIGIR_REQUISICAO",
    )


def test_lado_sem_dados(cliente, gravar) -> None:
    conteudo = imagem_png(4)
    gravacao = gravacao_cpf(cpf_sintetico())
    assert gravacao.leitura_visual is not None
    lv = gravacao.leitura_visual.model_copy(
        update={
            "tipo_documento_detectado": TipoDocumento.IDENTITY_DOCUMENT,
            "subtipo": Subtipo.RG_ANTIGO,
            "lado": Lado.FRENTE,
            "campos": {},
        }
    )
    gravacao = gravacao.model_copy(update={"leitura_visual": lv})
    # Sem campos devolvidos, o código os trata como ILEGIVEL; com todos AUSENTE é LADO_SEM_DADOS.
    from app.providers.fake.gravacoes import CampoGravado
    from app.schemas.campos import nomes_criticos

    lv = lv.model_copy(
        update={
            "campos": {
                n: CampoGravado(status=StatusLeitura.AUSENTE)
                for n in nomes_criticos(Subtipo.RG_ANTIGO)
            }
        }
    )
    gravar(conteudo, gravacao.model_copy(update={"leitura_visual": lv}))
    _assert_erro(
        _post(cliente, tipo="IDENTITY_DOCUMENT", imagem=b64(conteudo)),
        422,
        "DOCUMENTO_NAO_PROCESSAVEL",
        "LADO_SEM_DADOS",
        "REENVIAR_IMAGEM",
    )


def test_campo_critico_ilegivel(cliente, gravar) -> None:
    conteudo = imagem_png(5)
    gravacao = gravacao_cpf(cpf_sintetico())
    assert gravacao.leitura_visual is not None
    campos = dict(gravacao.leitura_visual.campos)
    campos["cpf"] = campos["cpf"].model_copy(
        update={"status": StatusLeitura.ILEGIVEL, "texto_literal": ""}
    )
    lv = gravacao.leitura_visual.model_copy(update={"campos": campos})
    gravar(conteudo, gravacao.model_copy(update={"leitura_visual": lv}))
    corpo = _assert_erro(
        _post(cliente, imagem=b64(conteudo)),
        422,
        "DOCUMENTO_NAO_PROCESSAVEL",
        "CAMPO_CRITICO_ILEGIVEL",
        "REENVIAR_IMAGEM",
    )
    assert corpo["motivos"] == [{"codigo": "CAMPO_CRITICO_ILEGIVEL", "campo": "cpf"}]


def test_status_de_falha_de_extracao_configuravel(settings: Settings, repositorio, objetos) -> None:
    como_400 = settings.model_copy(update={"http_status_falha_extracao": 400})
    servico = ServicoExtracao(como_400, criar_esteira(como_400), repositorio, objetos)
    cliente = montar_cliente(como_400, servico)
    _assert_erro(
        _post(cliente, imagem=b64(imagem_png(98))),
        400,
        "DOCUMENTO_NAO_PROCESSAVEL",
        "NENHUM_DOCUMENTO",
        "REENVIAR_IMAGEM",
    )


@pytest.mark.parametrize(
    ("falha", "status", "codigo"),
    [("indisponivel", 502, "PROVIDER_INDISPONIVEL"), ("tempo", 504, "TEMPO_ESGOTADO")],
)
def test_falha_de_provider(
    settings: Settings, repositorio, objetos, gravar, falha, status, codigo
) -> None:
    rapido = settings.model_copy(update={"provider_timeout_s": 0.2})
    servico = ServicoExtracao(rapido, criar_esteira(rapido), repositorio, objetos)
    cliente = montar_cliente(rapido, servico)
    conteudo = imagem_png(6)
    gravar(conteudo, gravacao_cpf(cpf_sintetico(), falhas={"leitura_visual": falha}))
    _assert_erro(_post(cliente, imagem=b64(conteudo)), status, codigo, codigo, "TENTAR_NOVAMENTE")


def test_falha_do_ocr_tambem_mapeia(settings: Settings, repositorio, objetos, gravar) -> None:
    rapido = settings.model_copy(update={"provider_timeout_s": 0.2})
    servico = ServicoExtracao(rapido, criar_esteira(rapido), repositorio, objetos)
    cliente = montar_cliente(rapido, servico)
    conteudo = imagem_png(7)
    gravar(conteudo, gravacao_cpf(cpf_sintetico(), falhas={"leitura_texto": "indisponivel"}))
    _assert_erro(
        _post(cliente, imagem=b64(conteudo)),
        502,
        "PROVIDER_INDISPONIVEL",
        "PROVIDER_INDISPONIVEL",
        "TENTAR_NOVAMENTE",
    )


def test_erro_interno_tem_id(settings: Settings, cliente, monkeypatch) -> None:
    async def explode(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(cliente.app.state.servico, "processar", explode)
    _assert_erro(_post(cliente), 500, "ERRO_INTERNO", "ERRO_INTERNO", "TENTAR_NOVAMENTE")


def test_sucesso_e_cabecalho_de_contrato(cliente, gravar) -> None:
    conteudo = imagem_png(8)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    resposta = _post(cliente, imagem=b64(conteudo), id_externo="abc-1")
    assert resposta.status_code == 200
    corpo = resposta.json()
    uuid.UUID(corpo["id_processamento"])
    assert corpo["status"] == "APROVADO"
    assert corpo["documento"] == {"subtipo": "CPF_CARTAO", "uf": "SP", "lado": "FRENTE"}


def test_data_url_aceita(cliente, gravar) -> None:
    conteudo = imagem_png(9)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    resposta = _post(cliente, imagem="data:image/png;base64," + b64(conteudo))
    assert resposta.status_code == 200


def test_health(cliente) -> None:
    resposta = cliente.get("/health")
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "ok"


def test_health_degradado(settings, servico) -> None:
    from fastapi.testclient import TestClient

    from app.main import criar_app

    async def falha() -> dict[str, str]:
        return {"banco": "falha", "objetos": "ok"}

    cliente = TestClient(criar_app(settings=settings, servico=servico, verificar_saude=falha))
    resposta = cliente.get("/health")
    assert resposta.status_code == 503
    assert resposta.json()["status"] == "degradado"
