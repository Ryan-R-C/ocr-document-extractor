"""Modo assíncrono: 202 + fila no banco + trabalhador + consulta por id."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.armazenamento.fila import CONCLUIDA, PENDENTE, PROCESSANDO
from app.armazenamento.limpeza import executar_limpeza
from app.trabalhador import Trabalhador
from tests.conftest import b64, cpf_sintetico, gravacao_cpf, imagem_png, montar_cliente


def _enfileirar(cliente, conteudo: bytes, tipo: str = "CPF"):
    return cliente.post(
        "/v1/extracoes/assincronas",
        json={"tipo_documento": tipo, "imagem_base64": b64(conteudo), "id_externo": "ext-9"},
    )


def _rodar_uma(servico) -> bool:
    return asyncio.run(Trabalhador(servico, "teste").uma())


def test_fluxo_completo_igual_ao_sincrono(cliente, servico, gravar, objetos) -> None:
    conteudo = imagem_png(401)
    gravar(conteudo, gravacao_cpf(cpf_sintetico(41)))
    resposta = _enfileirar(cliente, conteudo)
    assert resposta.status_code == 202
    aceita = resposta.json()
    id_proc = aceita["id_processamento"]
    assert aceita["situacao"] == "PENDENTE" and aceita["posicao_fila"] == 1
    assert resposta.headers["location"] == f"/v1/extracoes/{id_proc}"
    assert resposta.headers["retry-after"] == "2"
    # A imagem aguarda o trabalhador no armazenamento de objetos, criptografada.
    assert objetos.listar(f"fila/{id_proc}/")

    pendente = cliente.get(f"/v1/extracoes/{id_proc}")
    assert pendente.status_code == 202 and pendente.json()["situacao"] == "PENDENTE"

    assert _rodar_uma(servico) is True
    assert _rodar_uma(servico) is False  # fila vazia

    final = cliente.get(f"/v1/extracoes/{id_proc}")
    assert final.status_code == 200
    corpo = final.json()
    assert corpo["id_processamento"] == id_proc and corpo["status"] == "REVISAO"
    assert corpo["campos"]["cpf"]["valor_normalizado"]
    # O mesmo documento pelo síncrono dá o mesmo corpo (a menos do id).
    sincrono = cliente.post(
        "/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": b64(conteudo)}
    ).json()
    assert {k: v for k, v in sincrono.items() if k != "id_processamento"} == {
        k: v for k, v in corpo.items() if k != "id_processamento"
    }
    tarefa = servico.fila.obter(id_proc)
    assert tarefa.situacao == CONCLUIDA and tarefa.http_status == 200
    assert objetos.listar(f"fila/{id_proc}/") == []  # imagem da fila apagada
    processamento = servico.repositorio.obter(id_proc)
    assert processamento.id_externo == "ext-9"
    assert processamento.recebido_em.replace(tzinfo=UTC) == tarefa.criado_em.replace(tzinfo=UTC)


def test_erros_de_requisicao_voltam_na_hora(cliente) -> None:
    resposta = cliente.post(
        "/v1/extracoes/assincronas",
        json={"tipo_documento": "CPF", "imagem_base64": "não é base64"},
    )
    assert resposta.status_code == 400
    id_proc = resposta.json()["id_processamento"]
    assert cliente.get(f"/v1/extracoes/{id_proc}").status_code == 404
    malformada = cliente.post("/v1/extracoes/assincronas", json={"tipo_documento": "CPF"})
    assert malformada.status_code == 400


def test_id_desconhecido(cliente) -> None:
    resposta = cliente.get("/v1/extracoes/nao-existe")
    assert resposta.status_code == 404
    corpo = resposta.json()
    assert corpo["erro"] == "NAO_ENCONTRADO"
    assert corpo["motivos"][0]["codigo"] == "EXTRACAO_NAO_ENCONTRADA"
    assert corpo["id_processamento"] == "nao-existe"


def test_sincrono_desligado(settings, servico) -> None:
    desligado = settings.model_copy(update={"extracao_sincrona": False})
    cliente = montar_cliente(desligado, servico)
    resposta = cliente.post(
        "/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": b64(imagem_png(1))}
    )
    assert resposta.status_code == 404
    assert resposta.json()["motivos"][0]["codigo"] == "MODO_SINCRONO_DESLIGADO"
    assert (
        cliente.post(
            "/v1/extracoes/assincronas",
            json={"tipo_documento": "CPF", "imagem_base64": b64(imagem_png(1))},
        ).status_code
        == 202
    )


def test_falha_de_extracao_sai_na_consulta(cliente, servico) -> None:
    # Imagem sem gravação: o fake não identifica documento → 422 NENHUM_DOCUMENTO.
    id_proc = _enfileirar(cliente, imagem_png(402)).json()["id_processamento"]
    _rodar_uma(servico)
    resposta = cliente.get(f"/v1/extracoes/{id_proc}")
    assert resposta.status_code == 422
    assert resposta.json()["motivos"][0]["codigo"] == "NENHUM_DOCUMENTO"


def test_cache_conclui_sem_passar_pela_fila(cliente, servico, gravar) -> None:
    conteudo = imagem_png(403)
    gravar(conteudo, gravacao_cpf(cpf_sintetico(43)))
    primeiro = _enfileirar(cliente, conteudo).json()["id_processamento"]
    _rodar_uma(servico)
    segundo = _enfileirar(cliente, conteudo)
    assert segundo.status_code == 202 and segundo.json()["situacao"] == "CONCLUIDA"
    assert "retry-after" not in segundo.headers
    id_proc = segundo.json()["id_processamento"]
    assert servico.fila.obter(id_proc) is None  # não entrou na fila
    corpo = cliente.get(f"/v1/extracoes/{id_proc}").json()
    assert corpo["id_processamento"] == id_proc != primeiro
    assert corpo["status"] == "REVISAO"


def test_erro_transitorio_volta_para_a_fila_e_esgota(cliente, servico, gravar) -> None:
    servico.settings = servico.settings.model_copy(
        update={"fila_espera_inicial_s": 0.0, "fila_tentativas_max": 2}
    )
    conteudo = imagem_png(404)
    gravar(conteudo, gravacao_cpf(cpf_sintetico(44), falhas={"leitura_visual": "indisponivel"}))
    id_proc = _enfileirar(cliente, conteudo).json()["id_processamento"]

    _rodar_uma(servico)  # 1ª tentativa: devolvida
    tarefa = servico.fila.obter(id_proc)
    assert tarefa.situacao == PENDENTE and tarefa.tentativas == 1
    assert cliente.get(f"/v1/extracoes/{id_proc}").status_code == 202
    assert servico.repositorio.obter(id_proc) is None  # nada persistido ainda

    _rodar_uma(servico)  # 2ª (última): o 502 é o resultado final
    resposta = cliente.get(f"/v1/extracoes/{id_proc}")
    assert resposta.status_code == 502
    assert resposta.json()["erro"] == "PROVIDER_INDISPONIVEL"
    assert servico.fila.obter(id_proc).situacao == CONCLUIDA


def test_reserva_vencida_volta_e_esgota_com_erro_interno(cliente, servico, objetos) -> None:
    servico.settings = servico.settings.model_copy(update={"fila_tentativas_max": 1})
    id_proc = _enfileirar(cliente, imagem_png(405)).json()["id_processamento"]
    agora = datetime.now(UTC)
    # Trabalhador reserva e "morre" sem concluir.
    assert servico.fila.reservar(agora, "morto", timedelta(seconds=1)) is not None
    assert servico.fila.reservar(agora, "outro", timedelta(seconds=1)) is None  # reservada
    assert cliente.get(f"/v1/extracoes/{id_proc}").json()["situacao"] == PROCESSANDO
    # Depois do prazo, outro trabalhador pega de novo; tentativas esgotadas → 500.
    tarefa = servico.fila.obter(id_proc)
    servico.fila.devolver(id_proc, agora)  # simula o prazo vencido sem esperar
    with servico.fila.sessoes.begin() as sessao:
        registro = sessao.get(type(tarefa), id_proc)
        registro.situacao, registro.prazo_em = PROCESSANDO, agora - timedelta(seconds=1)
        registro.tentativas = 1
    _rodar_uma(servico)
    resposta = cliente.get(f"/v1/extracoes/{id_proc}")
    assert resposta.status_code == 500 and resposta.json()["erro"] == "ERRO_INTERNO"
    assert objetos.listar(f"fila/{id_proc}/") == []


def test_concorrencia_limitada(cliente, servico, monkeypatch) -> None:
    servico.settings = servico.settings.model_copy(
        update={"trabalhador_concorrencia": 2, "fila_intervalo_s": 0.01}
    )
    ids = [_enfileirar(cliente, imagem_png(410 + i)).json()["id_processamento"] for i in range(5)]
    original = servico.processar_tarefa
    em_andamento, maximo = 0, 0

    async def espiar(tarefa):
        nonlocal em_andamento, maximo
        em_andamento += 1
        maximo = max(maximo, em_andamento)
        await asyncio.sleep(0.02)
        try:
            return await original(tarefa)
        finally:
            em_andamento -= 1

    monkeypatch.setattr(servico, "processar_tarefa", espiar)
    # O SQLite dos testes tem uma conexão só: serializa o acesso das threads ao banco.
    trava = threading.Lock()
    for nome in ("reservar", "concluir", "devolver", "obter", "posicao"):
        metodo = getattr(servico.fila, nome)

        def serializado(*a, _m=metodo, **k):
            with trava:
                return _m(*a, **k)

        monkeypatch.setattr(servico.fila, nome, serializado)
    for nome in ("registrar", "obter", "buscar_cache"):
        metodo = getattr(servico.repositorio, nome)

        def serializado_repo(*a, _m=metodo, **k):
            with trava:
                return _m(*a, **k)

        monkeypatch.setattr(servico.repositorio, nome, serializado_repo)

    async def rodar():
        parar = asyncio.Event()
        tarefa = asyncio.create_task(Trabalhador(servico, "t").rodar(parar))
        for _ in range(500):
            with trava:
                restantes = servico.fila.pendentes()
            if not restantes:
                break
            await asyncio.sleep(0.02)
        parar.set()
        await tarefa

    asyncio.run(rodar())
    assert maximo == 2
    assert all(servico.fila.obter(i).situacao == CONCLUIDA for i in ids)


def test_limpeza_apaga_tarefas_vencidas(cliente, servico, objetos) -> None:
    id_proc = _enfileirar(cliente, imagem_png(420)).json()["id_processamento"]
    depois = datetime.now(UTC) + timedelta(hours=9)
    relatorio = executar_limpeza(servico.repositorio, objetos, depois)
    assert relatorio.tarefas == 1
    assert servico.fila.obter(id_proc) is None
    assert objetos.listar(f"fila/{id_proc}/") == []
    assert cliente.get(f"/v1/extracoes/{id_proc}").status_code == 404


def test_corpo_de_erro_da_fila_sem_dado_pessoal(cliente, servico, objetos) -> None:
    id_proc = _enfileirar(cliente, imagem_png(421)).json()["id_processamento"]
    objetos.apagar_prefixo(f"fila/{id_proc}")  # imagem sumiu
    _rodar_uma(servico)
    tarefa = servico.fila.obter(id_proc)
    assert tarefa.http_status == 500
    assert set(json.loads(tarefa.corpo_erro)) == {
        "id_processamento",
        "erro",
        "motivos",
        "acao_sugerida",
    }


@pytest.mark.parametrize("caminho", ["/v1/extracoes/x", "/v1/extracoes/" + "a" * 80])
def test_consulta_nunca_quebra(cliente, caminho) -> None:
    assert cliente.get(caminho).status_code == 404
