"""Pacote de auditoria, criptografia, TTL e job de limpeza."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.armazenamento import retencao
from app.armazenamento.limpeza import executar_limpeza
from app.armazenamento.modelos import CasoRevisao, ChamadaProvider, Processamento
from app.config import Settings
from app.montagem import criar_esteira
from app.servico import ServicoExtracao
from tests.conftest import b64, cpf_sintetico, gravacao_cpf, imagem_png, montar_cliente


def _post(cliente, conteudo: bytes, tipo: str = "CPF") -> dict:
    resposta = cliente.post(
        "/v1/extracoes",
        json={"tipo_documento": tipo, "imagem_base64": b64(conteudo), "id_externo": "ext-1"},
    )
    return resposta.json()


def _todos(repositorio, modelo):
    with repositorio.sessoes() as sessao:
        return list(sessao.query(modelo))


def test_pacote_de_auditoria_completo(cliente, gravar, objetos, repositorio) -> None:
    conteudo = imagem_png(1)
    sha = gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    corpo = _post(cliente, conteudo)
    id_proc = corpo["id_processamento"]

    prefixo = f"ttl-8h/{id_proc}"
    chaves = set(objetos.listar(prefixo + "/"))
    assert chaves == {
        f"{prefixo}/auditoria.json",
        f"{prefixo}/resposta.json",
        f"{prefixo}/imagem_recebida.png",
        f"{prefixo}/imagem_normalizada.png",
        f"{prefixo}/recortes/cpf.png",
        f"{prefixo}/recortes/nome.png",
    }
    assert objetos.ler(f"{prefixo}/imagem_recebida.png") == conteudo

    manifesto = json.loads(objetos.ler(f"{prefixo}/auditoria.json"))
    assert manifesto["imagem_recebida"]["sha256"] == sha
    assert manifesto["id_externo"] == "ext-1"
    assert manifesto["versao_pipeline"] == corpo["versao_pipeline"]
    assert manifesto["leitura_visual"]["metricas"]["tokens_entrada"] == 3420
    assert manifesto["leitura_visual"]["metricas"]["custo_brl"] > 0
    assert manifesto["leitura_texto"]["resposta_bruta"]["palavras"]
    assert manifesto["ancoragens"]["cpf"]["status"] == "NAO_ANCORADO"
    assert manifesto["releituras"]["cpf"]["executada"] is False
    assert manifesto["regras"]["aplicadas"][0]["id"] == "stub.nao_implementada"
    assert manifesto["regras"]["aplicadas"][0]["versao"]
    assert manifesto["decisao"]["status"] == "REVISAO"
    assert manifesto["recebido_em"] and manifesto["concluido_em"]
    assert set(manifesto["stubs_ativos"]) == {"qualidade", "ancoragem", "releitura", "regras"}

    processamento = repositorio.obter(id_proc)
    assert processamento.sha256_imagem == sha
    assert processamento.status == "REVISAO"
    assert processamento.prefixo_pacote == prefixo
    chamadas = _todos(repositorio, ChamadaProvider)
    assert {c.etapa for c in chamadas} == {"leitura_visual", "leitura_texto"}
    assert all(c.processamento_id == id_proc for c in chamadas)


def test_todo_objeto_criptografado_em_repouso(cliente, gravar, objetos) -> None:
    conteudo = imagem_png(2)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    _post(cliente, conteudo)
    chaves = objetos.listar("")
    assert chaves
    for chave in chaves:
        cabecalho = objetos.cliente.head_object(Bucket=objetos.bucket, Key=chave)
        assert cabecalho["ServerSideEncryption"] == "AES256", chave
    padrao = objetos.cliente.get_bucket_encryption(Bucket=objetos.bucket)
    regra = padrao["ServerSideEncryptionConfiguration"]["Rules"][0]
    assert regra["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"


def test_lifecycle_e_so_rede_de_seguranca(objetos) -> None:
    regras = objetos.cliente.get_bucket_lifecycle_configuration(Bucket=objetos.bucket)["Rules"]
    por_prefixo = {r["Filter"]["Prefix"]: r["Expiration"]["Days"] for r in regras}
    assert por_prefixo["ttl-8h/"] == 1
    assert por_prefixo["ttl-16h/"] == 1
    assert por_prefixo["revisao/"] == 31  # 30 dias + 1 de folga
    assert "sem-expiracao/" not in por_prefixo


@pytest.mark.parametrize(
    ("ttl", "prefixo", "horas"),
    [("8h", "ttl-8h", 8), ("16h", "ttl-16h", 16), (None, "sem-expiracao", None)],
)
def test_expira_em_por_ttl(settings, repositorio, objetos, gravar, ttl, prefixo, horas) -> None:
    config = settings.model_copy(update={"artefatos_ttl": ttl})
    cliente = montar_cliente(
        config, ServicoExtracao(config, criar_esteira(config), repositorio, objetos)
    )
    conteudo = imagem_png(3)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    corpo = _post(cliente, conteudo)
    proc = repositorio.obter(corpo["id_processamento"])
    assert proc.prefixo_pacote.startswith(prefixo + "/")
    if horas is None:
        assert proc.expira_em is None
    else:
        delta = proc.expira_em.replace(tzinfo=UTC) - proc.recebido_em.replace(tzinfo=UTC)
        assert delta == timedelta(hours=horas)


def test_caso_em_revisao_tem_registro_e_ttl_proprios(cliente, gravar, repositorio, objetos) -> None:
    conteudo = imagem_png(4)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    corpo = _post(cliente, conteudo)
    [caso] = _todos(repositorio, CasoRevisao)
    assert caso.processamento_id == corpo["id_processamento"]
    assert caso.subtipo == "CPF_CARTAO"
    assert caso.situacao == "PENDENTE"
    assert caso.expira_em.replace(tzinfo=UTC) - caso.criado_em.replace(tzinfo=UTC) == timedelta(
        days=30
    )
    assert objetos.ler(f"{caso.prefixo_pacote}/imagem_recebida.png") == conteudo


def test_recusa_422_nao_vira_caso_de_revisao(cliente, repositorio) -> None:
    _post(cliente, imagem_png(97))  # sem gravação: NENHUM_DOCUMENTO
    assert _todos(repositorio, CasoRevisao) == []
    [proc] = _todos(repositorio, Processamento)
    assert proc.status == "RECUSADO"
    assert proc.http_status == 422


def test_requisicao_invalida_registra_sem_pacote(cliente, repositorio, objetos) -> None:
    cliente.post("/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": "@@@"})
    [proc] = _todos(repositorio, Processamento)
    assert proc.status == "REQUISICAO_INVALIDA"
    assert proc.prefixo_pacote is None
    assert proc.expira_em is not None


def test_limpeza_apaga_so_o_que_venceu(settings, cliente, gravar, repositorio, objetos) -> None:
    conteudo = imagem_png(5)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    corpo = _post(cliente, conteudo)
    id_proc = corpo["id_processamento"]
    agora = datetime.now(UTC)

    # Antes do vencimento: nada é apagado.
    relatorio = executar_limpeza(repositorio, objetos, agora + timedelta(hours=7, minutes=59))
    assert relatorio.processamentos == 0
    assert repositorio.obter(id_proc) is not None

    # Depois de 8 h: objeto e metadado do processamento somem; o caso em revisão (30 d) fica.
    relatorio = executar_limpeza(repositorio, objetos, agora + timedelta(hours=8, minutes=1))
    assert relatorio.processamentos == 1 and relatorio.falhas == 0
    assert repositorio.obter(id_proc) is None
    assert objetos.listar(f"ttl-8h/{id_proc}/") == []
    [caso] = _todos(repositorio, CasoRevisao)
    assert objetos.listar(caso.prefixo_pacote + "/")
    # O custo por chamada não tem dado pessoal e sobrevive à expiração.
    assert _todos(repositorio, ChamadaProvider)

    # Depois de 30 dias, o caso em revisão também vai embora.
    relatorio = executar_limpeza(repositorio, objetos, agora + timedelta(days=30, minutes=1))
    assert relatorio.casos == 1
    assert _todos(repositorio, CasoRevisao) == []
    assert objetos.listar("revisao/") == []

    # Idempotente.
    assert executar_limpeza(repositorio, objetos, agora + timedelta(days=31)).falhas == 0


def test_sem_expiracao_nunca_e_limpo(settings, repositorio, objetos, gravar) -> None:
    config = settings.model_copy(update={"artefatos_ttl": None, "casos_revisao_ttl": None})
    cliente = montar_cliente(
        config, ServicoExtracao(config, criar_esteira(config), repositorio, objetos)
    )
    conteudo = imagem_png(6)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    corpo = _post(cliente, conteudo)
    relatorio = executar_limpeza(repositorio, objetos, datetime.now(UTC) + timedelta(days=3650))
    assert relatorio.processamentos == relatorio.casos == 0
    assert repositorio.obter(corpo["id_processamento"]) is not None


def test_falha_ao_apagar_objeto_mantem_metadado(
    cliente, gravar, repositorio, objetos, monkeypatch
) -> None:
    conteudo = imagem_png(7)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    corpo = _post(cliente, conteudo)

    def falha(prefixo: str) -> int:
        raise RuntimeError("s3 fora")

    monkeypatch.setattr(objetos, "apagar_prefixo", falha)
    relatorio = executar_limpeza(repositorio, objetos, datetime.now(UTC) + timedelta(hours=9))
    assert relatorio.falhas == 1
    assert repositorio.obter(corpo["id_processamento"]) is not None  # tenta de novo depois


def test_falha_de_auditoria_nao_bloqueia_resposta(
    cliente, gravar, repositorio, objetos, monkeypatch
) -> None:
    conteudo = imagem_png(8)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))

    def falha(*args, **kwargs) -> None:
        raise RuntimeError("s3 fora")

    monkeypatch.setattr(objetos, "gravar", falha)
    resposta = cliente.post(
        "/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": b64(conteudo)}
    )
    assert resposta.status_code == 200
    proc = repositorio.obter(resposta.json()["id_processamento"])
    assert proc.prefixo_pacote is None
    assert proc.chave_cache is None  # sem pacote, não entra no cache


def test_prefixos() -> None:
    assert retencao.prefixo_artefatos(timedelta(hours=8)) == "ttl-8h"
    assert retencao.prefixo_artefatos(timedelta(hours=16)) == "ttl-16h"
    assert retencao.prefixo_artefatos(None) == "sem-expiracao"


def test_migracao_alembic_cria_as_tabelas(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, inspect

    from app.armazenamento.preparar import migrar

    url = f"sqlite:///{tmp_path / 'm.db'}"
    migrar(url)
    tabelas = set(inspect(create_engine(url)).get_table_names())
    assert {"processamentos", "casos_revisao", "chamadas_provider", "alembic_version"} <= tabelas


def test_migracao_igual_aos_modelos(tmp_path: Path) -> None:
    """Falha se alguém mudar os modelos sem gerar a migração."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from app.armazenamento.modelos import Base
    from app.armazenamento.preparar import migrar

    url = f"sqlite:///{tmp_path / 'm.db'}"
    migrar(url)
    with create_engine(url).connect() as conexao:
        diferencas = compare_metadata(MigrationContext.configure(conexao), Base.metadata)
    assert diferencas == []


@pytest.mark.integracao
def test_integracao_postgres_e_s3() -> None:  # pragma: no cover - roda com `make test-integracao`
    from app.armazenamento.db import banco_ok, criar_engine
    from app.armazenamento.objetos import Objetos

    settings = Settings()
    assert banco_ok(criar_engine(settings))
    objetos = Objetos(settings)
    objetos.preparar_bucket()
    objetos.gravar("teste/integracao.txt", b"ok", "text/plain")
    assert objetos.ler("teste/integracao.txt") == b"ok"
    assert objetos.apagar_prefixo("teste") == 1
