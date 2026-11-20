"""Diagnóstico da etapa que deixou passar erros escapados e alucinações (Portão 2+3).

Registros montados à mão para cada categoria e um cenário ponta a ponta com os fakes.
Só dados sintéticos (CPF gerado, nome inventado)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import pytest

from app.api.contrato import RequisicaoExtracao, StatusDocumento, Subtipo, TipoDocumento
from app.montagem import criar_esteira
from app.schemas.campos import definicao
from avaliacao import diagnostico, metricas, relatorio
from avaliacao.diagnostico import Casamento, Categoria, casamento, diagnosticar
from avaliacao.gabarito import ItemGabarito
from avaliacao.metricas import RegistroCampo
from tests.conftest import (
    NOME_SINTETICO,
    b64,
    cpf_sintetico,
    gravacao_cpf,
    imagem_png,
    sha256,
)

_BASE = RegistroCampo(
    arquivo="sintetico/0001.png",
    campo="cpf",
    critico=True,
    subtipo="CPF_CARTAO",
    uf="SP",
    qualidade="foto",
    variante=False,
    campo_apagado=False,
    esperado_status="LEGIVEL",
    doc_aprovado=True,
    devolveu_valor=True,
    correto=False,
    id="abc123def456",
    status_leitura="LEGIVEL",
    status_vlm="LEGIVEL",
    vlm_devolveu_valor=True,
    correto_vlm=False,
    ancoragem="ANCORADO",
    ancoragem_efetiva="ANCORADO",
    casamento_ancoragem="EXATO",
    status_validacao="VALIDO",
    regras=("cpf.digitos",),
)


def _reg(**mudancas: Any) -> RegistroCampo:
    return replace(_BASE, **mudancas)


# --- Categorias com registros feitos à mão ------------------------------------------------------


@pytest.mark.parametrize(
    ("registro", "categoria"),
    [
        (_reg(), Categoria.VLM_ERROU_OCR_CONFIRMOU),
        (_reg(casamento_ancoragem="CONFUSAO"), Categoria.ANCORAGEM_TOLERANTE_CONFUSAO),
        (
            _reg(campo="nome", casamento_ancoragem="PONTUACAO"),
            Categoria.ANCORAGEM_TOLERANTE_PONTUACAO,
        ),
        (
            _reg(correto_vlm=True, releitura="EXECUTADA", releitura_trocou_valor=True),
            Categoria.RELEITURA_INTRODUZIU,
        ),
        (
            _reg(valor_de_outro_campo=True, campo="data_nascimento"),
            Categoria.REGRAS_NAO_PEGARAM,
        ),
        (
            _reg(
                manuscrito=True,
                ancoragem="NAO_APLICAVEL",
                ancoragem_efetiva="NAO_APLICAVEL",
                casamento_ancoragem=None,
                releitura="EXECUTADA",
                concordancia="2_DE_2",
            ),
            Categoria.DUPLA_LEITURA_CONCORDOU_ERRADO,
        ),
        (
            _reg(
                variante=True,
                campo_apagado=True,
                esperado_status="ILEGIVEL",
                correto=None,
                correto_vlm=None,
            ),
            Categoria.CAMPO_APAGADO,
        ),
        (_reg(casamento_ancoragem=None), Categoria.OUTRA),
        (_reg(casamento_ancoragem="INDETERMINADO"), Categoria.OUTRA),
    ],
)
def test_categoria_por_registro(registro: RegistroCampo, categoria: Categoria) -> None:
    assert registro.erro_escapado
    assert diagnosticar(registro) is categoria


def test_precedencias() -> None:
    # Campo apagado vence a ancoragem tolerante.
    apagado = _reg(
        campo_apagado=True,
        esperado_status="AUSENTE",
        correto=None,
        casamento_ancoragem="CONFUSAO",
    )
    assert diagnosticar(apagado) is Categoria.CAMPO_APAGADO
    # Releitura trocou o valor, mas o VLM principal também errava: a culpa é de quem
    # confirmou o valor da releitura (aqui, a ancoragem exata).
    ambos_errados = _reg(releitura_trocou_valor=True, correto_vlm=False)
    assert diagnosticar(ambos_errados) is Categoria.VLM_ERROU_OCR_CONFIRMOU
    # Releitura com o VLM principal vazio (sem valor) também conta como introduzida.
    vazio = _reg(releitura_trocou_valor=True, vlm_devolveu_valor=False, correto_vlm=False)
    assert diagnosticar(vazio) is Categoria.RELEITURA_INTRODUZIU
    # Manuscrito aprovado sem 2_DE_2 não deveria acontecer: OUTRA.
    assert diagnosticar(_reg(manuscrito=True, concordancia=None)) is Categoria.OUTRA


def test_calcular_lista_so_escapados_e_conta_todas_as_categorias() -> None:
    campos = [
        _reg(),
        _reg(campo="nome", casamento_ancoragem="PONTUACAO", critico=True),
        _reg(correto=True),  # certo: não entra
        _reg(doc_aprovado=False),  # em revisão: não entra
        _reg(critico=False),  # informativo: não entra
    ]
    resultado = diagnostico.calcular(campos)
    assert [c["categoria"] for c in resultado["casos"]] == [
        "VLM_ERROU_OCR_CONFIRMOU",
        "ANCORAGEM_TOLERANTE_PONTUACAO",
    ]
    assert set(resultado["por_categoria"]) == {c.value for c in Categoria}
    assert resultado["por_categoria"]["VLM_ERROU_OCR_CONFIRMOU"] == 1
    assert resultado["por_categoria"]["OUTRA"] == 0
    caso = resultado["casos"][0]
    assert caso["sugestao"] == diagnostico.SUGESTOES[Categoria.VLM_ERROU_OCR_CONFIRMOU]
    assert set(caso) >= {"id", "campo", "subtipo", "uf", "qualidade", "categoria", "sugestao"}
    assert caso["regras"] == ["cpf.digitos"]
    assert all(diagnostico.SUGESTOES[c] for c in Categoria)


def test_relatorio_tem_secao_de_diagnostico() -> None:
    registros = metricas.Registros(campos=[_reg(casamento_ancoragem="CONFUSAO")])
    m = metricas.calcular(registros)
    texto = "\n".join(relatorio._diagnostico(m["diagnostico"]))
    assert "## Diagnóstico dos erros escapados e alucinações" in texto
    assert "| abc123def456 | cpf | CPF_CARTAO | SP | foto | valor_errado |" in texto
    assert "ANCORAGEM_TOLERANTE_CONFUSAO" in texto
    vazio = "\n".join(relatorio._diagnostico(diagnostico.calcular([])))
    assert "Nenhum erro escapado" in vazio
    assert relatorio._diagnostico(None) == []


# --- Tipo de casamento ----------------------------------------------------------------------


def _defn(subtipo: Subtipo, nome: str):
    d = definicao(subtipo, nome)
    assert d is not None
    return d


def test_casamento() -> None:
    cpf = _defn(Subtipo.CPF_CARTAO, "cpf")
    nome = _defn(Subtipo.CPF_CARTAO, "nome")
    data = _defn(Subtipo.CPF_CARTAO, "data_nascimento")
    assert casamento(cpf, "111.444.777-35", ["111.444.777-35"]) is Casamento.EXATO
    # Pontuação que não muda o valor normalizado continua exata.
    assert casamento(cpf, "111.444.777-35", ["11144477735"]) is Casamento.EXATO
    assert casamento(data, "01/02/1990", ["01", "02", "1990"]) is Casamento.EXATO
    assert casamento(cpf, "111.444.777-35", ["111.444.777-3S"]) is Casamento.CONFUSAO
    assert casamento(cpf, "111.444.777-0?", ["111.444.777-05"]) is Casamento.CORINGA
    # Espaço ou acento ignorados pela chave, mas que mudam o nome normalizado.
    assert casamento(nome, "ZILAVO PERENUQUIDABE", ["ZILAVO", "PERENU", "QUIDABE"]) is (
        Casamento.PONTUACAO
    )
    assert casamento(nome, "ZILÁVO PERENU", ["ZILAVO", "PERENU"]) is Casamento.PONTUACAO
    assert casamento(nome, "ZILAVO PERENU", ["Zilavo", "Perenu"]) is Casamento.EXATO
    # Rótulo grudado no valor.
    assert casamento(cpf, "111.444.777-35", ["CPF:111.444.777-35"], ["CPF"]) is Casamento.EXATO
    assert casamento(cpf, "111.444.777-35", []) is Casamento.INDETERMINADO


def test_valor_de_outro_campo() -> None:
    item = ItemGabarito.model_validate(
        {
            "arquivo": "x.png",
            "sha256": "0" * 64,
            "tipo_documento": "IDENTITY_DOCUMENT",
            "subtipo": "RG_ANTIGO",
            "uf": "SP",
            "lado": "VERSO",
            "qualidade": "foto",
            "campos": {
                "data_nascimento": {"valor": "01/02/1990"},
                "data_expedicao": {"valor": "03/04/2015"},
            },
        }
    )
    assert diagnostico._valor_de_outro_campo(item, "data_nascimento", "2015-04-03", "1990-02-01")
    assert not diagnostico._valor_de_outro_campo(
        item, "data_nascimento", "2015-04-04", "1990-02-01"
    )
    assert not diagnostico._valor_de_outro_campo(
        item, "data_nascimento", "1990-02-01", "1990-02-01"
    )
    assert not diagnostico._valor_de_outro_campo(item, "data_nascimento", None, "1990-02-01")


# --- Ponta a ponta com os fakes -----------------------------------------------------------------


def _item(conteudo: bytes, cpf: str) -> ItemGabarito:
    return ItemGabarito.model_validate(
        {
            "arquivo": "sintetico/cpf.png",
            "sha256": sha256(conteudo),
            "tipo_documento": "CPF",
            "subtipo": "CPF_CARTAO",
            "uf": "SP",
            "lado": "FRENTE",
            "qualidade": "foto",
            "campos": {"cpf": {"valor": cpf}, "nome": {"valor": NOME_SINTETICO}},
        }
    )


async def _avaliar_um(settings, gravar, conteudo: bytes, gravacao, cpf_gabarito: str):
    gravar(conteudo, gravacao)
    resultado = await criar_esteira(settings).processar(
        str(uuid.uuid4()),
        RequisicaoExtracao(tipo_documento=TipoDocumento.CPF, imagem_base64=b64(conteudo)),
    )
    assert resultado.resposta is not None
    assert resultado.resposta.status is StatusDocumento.APROVADO
    registros = metricas.Registros()
    metricas.registrar(registros, _item(conteudo, cpf_gabarito), resultado)
    return registros, metricas.calcular(registros)


@pytest.mark.anyio
async def test_ponta_a_ponta_vlm_errou_e_ocr_confirmou(settings, gravar) -> None:
    """O VLM fake lê outro CPF válido e o OCR fake tem o mesmo texto: escapa."""
    certo, errado = cpf_sintetico(1), cpf_sintetico(2)
    assert certo != errado
    conteudo = imagem_png(901)
    registros, m = await _avaliar_um(settings, gravar, conteudo, gravacao_cpf(errado), certo)

    cpf = next(c for c in registros.campos if c.campo == "cpf")
    assert cpf.erro_escapado and cpf.correto_vlm is False
    assert cpf.casamento_ancoragem == "EXATO"
    assert cpf.status_validacao == "VALIDO"
    assert cpf.regras and "sem_regra" not in cpf.regras
    assert cpf.correto_releitura is None and not cpf.releitura_trocou_valor
    assert not cpf.valor_de_outro_campo

    diag = m["diagnostico"]
    assert diag["por_categoria"]["VLM_ERROU_OCR_CONFIRMOU"] == 1
    (caso,) = diag["casos"]
    assert caso["id"] == sha256(conteudo)[:12]
    assert caso["campo"] == "cpf" and caso["categoria"] == "VLM_ERROU_OCR_CONFIRMOU"
    # Nada de valor de campo no diagnóstico (invariante 10).
    assert certo not in str(diag) and errado not in str(diag)
    assert NOME_SINTETICO not in str(diag)


@pytest.mark.anyio
async def test_ponta_a_ponta_ancoragem_por_confusao(settings, gravar) -> None:
    """O OCR fake lê `O` no lugar de `0`; a ancoragem casa pela confusão O/0."""
    certo = cpf_sintetico(1)
    errado = next(
        c for s in range(3, 200) if (c := cpf_sintetico(s)) != certo and "0" in c.split("-")[0]
    )
    ocr = errado.replace("0", "O", 1)
    gravacao = gravacao_cpf(
        errado,
        leitura_texto={
            "palavras": [
                {"id": 0, "texto": "CPF", "caixa": [20, 100, 40, 20]},
                {"id": 1, "texto": ocr, "caixa": [70, 100, 160, 20]},
            ]
        },
    )
    conteudo = imagem_png(902)
    _, m = await _avaliar_um(settings, gravar, conteudo, gravacao, certo)
    (caso,) = m["diagnostico"]["casos"]
    assert caso["categoria"] == "ANCORAGEM_TOLERANTE_CONFUSAO"
    assert caso["casamento_ancoragem"] == "CONFUSAO"
