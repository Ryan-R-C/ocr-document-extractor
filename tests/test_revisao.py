"""Casos em revisão: exportar para conferência e importar a correção no gabarito."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.armazenamento.modelos import CasoRevisao
from avaliacao.gabarito import ler_manifesto
from avaliacao.revisao import ErroRevisao, exportar, importar
from tests.conftest import b64, cpf_sintetico, gravacao_cpf, imagem_png


def _caso_em_revisao(cliente, gravar, semente: int = 1) -> tuple[str, bytes]:
    conteudo = imagem_png(300 + semente)
    cpf = cpf_sintetico(semente)
    gravar(conteudo, gravacao_cpf(cpf))
    corpo = cliente.post(
        "/v1/extracoes", json={"tipo_documento": "CPF", "imagem_base64": b64(conteudo)}
    ).json()
    assert corpo["status"] == "REVISAO"
    return cpf, conteudo


def _preencher(pasta: Path, cpf: str) -> None:
    correcao = json.loads((pasta / "correcao.json").read_text())
    correcao["qualidade"] = "foto"
    correcao["campos"]["cpf"] = {"status": "LEGIVEL", "valor": cpf}
    correcao["campos"]["nome"] = {"status": "LEGIVEL", "valor": "LIMO TOVA"}
    correcao["campos"]["data_nascimento"] = {"status": "AUSENTE", "valor": None}
    (pasta / "correcao.json").write_text(json.dumps(correcao))


def test_exportar_e_importar(cliente, gravar, repositorio, objetos, tmp_path) -> None:
    cpf, conteudo = _caso_em_revisao(cliente, gravar)
    destino = tmp_path / "revisao"
    ids = exportar(repositorio, objetos, destino)
    assert len(ids) == 1
    pasta = destino / ids[0]
    assert (pasta / "imagem_recebida.png").read_bytes() == conteudo
    assert (pasta / "recortes" / "cpf.png").exists()
    leituras = json.loads((pasta / "leituras.json").read_text())
    assert leituras["ancoragens"]["cpf"]["status"] == "ANCORADO"
    modelo = json.loads((pasta / "correcao.json").read_text())
    assert modelo["subtipo"] == "CPF_CARTAO"
    # O modelo vem em branco: a conferência não é induzida pelo valor lido.
    assert all(c == {"status": None, "valor": None} for c in modelo["campos"].values())
    assert exportar(repositorio, objetos, destino) == []  # não reexporta

    _preencher(pasta, cpf)
    gabarito = tmp_path / "gabarito"
    item = importar(pasta, gabarito, repositorio)
    assert item.particao == "ajuste" and item.origem == f"revisao:{ids[0]}"
    assert (gabarito / item.arquivo).read_bytes() == conteudo
    assert [i.sha256 for i in ler_manifesto(gabarito / "manifesto.jsonl")] == [item.sha256]
    caso = repositorio.obter_caso(ids[0])
    assert isinstance(caso, CasoRevisao) and caso.situacao == "CONFERIDO" and caso.conferido_em
    assert exportar(repositorio, objetos, tmp_path / "outro") == []  # já conferido
    with pytest.raises(ErroRevisao, match="já está no gabarito"):
        importar(pasta, gabarito, None)


def test_importar_recusa_correcao_incompleta_sem_vazar_valor(
    cliente, gravar, repositorio, objetos, tmp_path
) -> None:
    cpf, _ = _caso_em_revisao(cliente, gravar, 2)
    pasta = tmp_path / "revisao" / exportar(repositorio, objetos, tmp_path / "revisao")[0]
    with pytest.raises(ErroRevisao, match="qualidade"):
        importar(pasta, tmp_path / "gabarito", None)
    _preencher(pasta, cpf)
    correcao = json.loads((pasta / "correcao.json").read_text())
    correcao["campos"]["data_nascimento"] = {"status": "AUSENTE", "valor": cpf}
    (pasta / "correcao.json").write_text(json.dumps(correcao))
    with pytest.raises(ErroRevisao) as erro:
        importar(pasta, tmp_path / "gabarito", None)
    assert cpf not in str(erro.value) and "data_nascimento" in str(erro.value)
    correcao["campos"].pop("data_nascimento")
    (pasta / "correcao.json").write_text(json.dumps(correcao))
    with pytest.raises(ErroRevisao, match="sem status"):
        importar(pasta, tmp_path / "gabarito", None)
    assert not (tmp_path / "gabarito" / "manifesto.jsonl").exists()


def test_importar_confere_a_imagem_do_caso(cliente, gravar, repositorio, objetos, tmp_path) -> None:
    cpf, _ = _caso_em_revisao(cliente, gravar, 3)
    pasta = tmp_path / "r" / exportar(repositorio, objetos, tmp_path / "r")[0]
    _preencher(pasta, cpf)
    (pasta / "imagem_recebida.png").write_bytes(imagem_png(999))
    with pytest.raises(ErroRevisao, match="sha256"):
        importar(pasta, tmp_path / "gabarito", None)
