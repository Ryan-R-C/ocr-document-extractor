"""Cache por hash: mesma imagem + mesmo tipo + mesma versao_pipeline, sem nova chamada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.armazenamento.limpeza import executar_limpeza
from app.armazenamento.modelos import ChamadaProvider
from app.montagem import criar_esteira
from app.providers.fake import FakeLeitorTexto, FakeLeitorVisual
from app.servico import ServicoExtracao
from tests.conftest import b64, cpf_sintetico, gravacao_cpf, imagem_png, montar_cliente


@pytest.fixture
def contador(monkeypatch) -> dict[str, int]:
    chamadas = {"visual": 0, "texto": 0}
    ler_visual, ler_texto = FakeLeitorVisual.ler, FakeLeitorTexto.ler

    async def visual(self, pedido):
        chamadas["visual"] += 1
        return await ler_visual(self, pedido)

    async def texto(self, imagem):
        chamadas["texto"] += 1
        return await ler_texto(self, imagem)

    monkeypatch.setattr(FakeLeitorVisual, "ler", visual)
    monkeypatch.setattr(FakeLeitorTexto, "ler", texto)
    return chamadas


def _post(cliente, conteudo: bytes, tipo: str = "CPF"):
    return cliente.post(
        "/v1/extracoes", json={"tipo_documento": tipo, "imagem_base64": b64(conteudo)}
    )


def test_mesma_imagem_usa_cache(cliente, gravar, contador, repositorio) -> None:
    conteudo = imagem_png(1)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    primeira = _post(cliente, conteudo).json()
    segunda = _post(cliente, conteudo).json()

    assert contador == {"visual": 1, "texto": 1}
    assert segunda["id_processamento"] != primeira["id_processamento"]
    sem_id = lambda c: {k: v for k, v in c.items() if k != "id_processamento"}  # noqa: E731
    assert sem_id(segunda) == sem_id(primeira)

    registro = repositorio.obter(segunda["id_processamento"])
    assert registro.origem_cache_id == primeira["id_processamento"]
    assert float(registro.custo_brl) == 0
    with repositorio.sessoes() as sessao:
        assert sessao.query(ChamadaProvider).count() == 2  # só as da primeira


def test_cache_vale_tambem_para_422(cliente, contador) -> None:
    conteudo = imagem_png(96)  # sem gravação: NENHUM_DOCUMENTO
    assert _post(cliente, conteudo).status_code == 422
    resposta = _post(cliente, conteudo)
    assert resposta.status_code == 422
    assert contador["visual"] == 1


def test_tipo_diferente_nao_usa_cache(cliente, gravar, contador) -> None:
    conteudo = imagem_png(2)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    _post(cliente, conteudo, "CPF")
    _post(cliente, conteudo, "IDENTITY_DOCUMENT")
    assert contador["visual"] == 2


def test_versao_diferente_nao_usa_cache(settings, repositorio, objetos, gravar, contador) -> None:
    conteudo = imagem_png(3)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    _post(
        montar_cliente(
            settings, ServicoExtracao(settings, criar_esteira(settings), repositorio, objetos)
        ),
        conteudo,
    )
    outro_modelo = settings.model_copy(update={"leitor_visual_modelo": "gpt-6.1-sol"})
    servico = ServicoExtracao(outro_modelo, criar_esteira(outro_modelo), repositorio, objetos)
    _post(montar_cliente(outro_modelo, servico), conteudo)
    assert contador["visual"] == 2


def test_pacote_expirado_nao_e_reaproveitado(
    cliente, gravar, contador, repositorio, objetos, monkeypatch
) -> None:
    conteudo = imagem_png(4)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    _post(cliente, conteudo)

    # Avança o relógio do serviço para depois do TTL de 8 h.
    import app.servico as mod_servico

    futuro = datetime.now(UTC) + timedelta(hours=9)

    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return futuro

    monkeypatch.setattr(mod_servico, "datetime", Relogio)
    _post(cliente, conteudo)
    assert contador["visual"] == 2


def test_pacote_apagado_pela_limpeza_nao_e_reaproveitado(
    cliente, gravar, contador, repositorio, objetos
) -> None:
    conteudo = imagem_png(5)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    primeira = _post(cliente, conteudo).json()
    objetos.apagar_prefixo(repositorio.obter(primeira["id_processamento"]).prefixo_pacote)
    _post(cliente, conteudo)
    assert contador["visual"] == 2


def test_resposta_do_cache_expira_junto_com_o_original(
    cliente, gravar, repositorio, objetos
) -> None:
    conteudo = imagem_png(6)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    primeira = _post(cliente, conteudo).json()
    segunda = _post(cliente, conteudo).json()
    original = repositorio.obter(primeira["id_processamento"])
    copia = repositorio.obter(segunda["id_processamento"])
    assert copia.expira_em == original.expira_em
    executar_limpeza(repositorio, objetos, datetime.now(UTC) + timedelta(hours=9))
    assert repositorio.obter(primeira["id_processamento"]) is None
    assert repositorio.obter(segunda["id_processamento"]) is None


def test_erro_de_provider_nao_entra_no_cache(
    settings, repositorio, objetos, gravar, contador
) -> None:
    rapido = settings.model_copy(update={"provider_timeout_s": 0.2})
    cliente = montar_cliente(
        rapido, ServicoExtracao(rapido, criar_esteira(rapido), repositorio, objetos)
    )
    conteudo = imagem_png(7)
    gravar(conteudo, gravacao_cpf(cpf_sintetico(), falhas={"leitura_visual": "indisponivel"}))
    assert _post(cliente, conteudo).status_code == 502
    assert _post(cliente, conteudo).status_code == 502
    assert contador["visual"] == 2
