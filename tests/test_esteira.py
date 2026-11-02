"""Esteira e decisão: nenhum stub resulta em APROVADO; a decisão real exige as três condições."""

from __future__ import annotations

import asyncio
import io
import uuid

import pytest
from PIL import Image

from app.api.contrato import (
    UF,
    Lado,
    MotivoCampo,
    RequisicaoExtracao,
    StatusDocumento,
    StatusLeitura,
    StatusValidacao,
    Subtipo,
    TipoDocumento,
)
from app.core import decisao
from app.core.ancoragem import ResultadoAncoragem, StatusAncoragem
from app.core.erros import FalhaExtracao
from app.core.releitura import ResultadoReleitura
from app.montagem import criar_esteira
from app.providers.interfaces import (
    CampoLido,
    LeituraVisual,
    MetricasChamada,
    QualidadeCaptura,
)
from app.regras.motor import ResultadoValidacao
from app.schemas.campos import campos_do_subtipo, nomes_criticos
from tests.conftest import b64, cpf_sintetico, gravacao_cpf, imagem_png


def _leitura(
    subtipo: Subtipo, campos: dict[str, CampoLido], tipo=TipoDocumento.CPF
) -> LeituraVisual:
    return LeituraVisual(
        subtipo=subtipo,
        uf=UF.SP,
        lado=Lado.FRENTE,
        tipo_documento_detectado=tipo,
        qualidade_captura=QualidadeCaptura.FOTO,
        campos=campos,
        resposta_bruta={},
        metricas=MetricasChamada(provider="t", modelo="t", etapa="leitura_visual"),
    )


def _decidir(
    campos: dict[str, CampoLido],
    ancoragem: StatusAncoragem = StatusAncoragem.ANCORADO,
    validacao: StatusValidacao = StatusValidacao.VALIDO,
    aprovar_nao_verificavel: bool = False,
    subtipo: Subtipo = Subtipo.CPF_CARTAO,
    releituras: dict[str, ResultadoReleitura] | None = None,
):
    leitura = _leitura(subtipo, campos)
    preparados = decisao.preparar_campos(subtipo, campos)
    ancoragens = {n: ResultadoAncoragem(n, ancoragem) for n in preparados}
    resultado_validacao = ResultadoValidacao(
        por_campo={n: validacao for n in preparados}, motivos={}
    )
    return decisao.decidir(
        leitura,
        preparados,
        ancoragens,
        releituras or {},
        resultado_validacao,
        aprovar_nao_verificavel,
    )


CPF = "111.444.777-35"
LIMPO = {"cpf": CampoLido(StatusLeitura.LEGIVEL, "CPF", CPF)}


def test_aprova_com_as_tres_condicoes() -> None:
    resultado = _decidir(LIMPO)
    assert resultado.status is StatusDocumento.APROVADO
    assert resultado.campos["cpf"].valor_normalizado == "11144477735"


def test_sem_ancoragem_nao_aprova() -> None:
    resultado = _decidir(LIMPO, ancoragem=StatusAncoragem.NAO_ANCORADO)
    assert resultado.status is StatusDocumento.REVISAO
    assert MotivoCampo.NAO_ANCORADO in resultado.campos["cpf"].motivos


def test_ancoragem_parcial_nao_aprova() -> None:
    assert _decidir(LIMPO, ancoragem=StatusAncoragem.PARCIAL).status is StatusDocumento.REVISAO


@pytest.mark.parametrize(
    "validacao",
    [StatusValidacao.INVALIDO, StatusValidacao.SUSPEITO, StatusValidacao.NAO_VERIFICAVEL],
)
def test_validacao_com_falha_nao_aprova(validacao) -> None:
    assert _decidir(LIMPO, validacao=validacao).status is StatusDocumento.REVISAO


def test_nao_verificavel_aprova_so_com_configuracao() -> None:
    resultado = _decidir(
        LIMPO, validacao=StatusValidacao.NAO_VERIFICAVEL, aprovar_nao_verificavel=True
    )
    assert resultado.status is StatusDocumento.APROVADO


def test_caractere_duvidoso_vira_parcial_sem_normalizado() -> None:
    campos = {"cpf": CampoLido(StatusLeitura.LEGIVEL, "CPF", "111.444.7?7-35")}
    resultado = _decidir(campos)
    assert resultado.status is StatusDocumento.REVISAO
    campo = resultado.campos["cpf"]
    assert campo.status_leitura is StatusLeitura.PARCIAL
    assert campo.valor_normalizado is None
    assert MotivoCampo.CARACTERE_DUVIDOSO in campo.motivos


def test_letra_no_lugar_de_digito_nao_e_corrigida() -> None:
    campos = {"cpf": CampoLido(StatusLeitura.LEGIVEL, "CPF", "111.444.777-3S")}
    campo = _decidir(campos).campos["cpf"]
    assert campo.valor == "111.444.777-3S"
    assert campo.valor_normalizado is None
    assert MotivoCampo.FORMATO_INVALIDO in campo.motivos


def test_ilegivel_nunca_devolve_valor_mesmo_que_o_modelo_escreva() -> None:
    campos = {
        "cpf": CampoLido(StatusLeitura.LEGIVEL, "CPF", CPF),
        "nome": CampoLido(StatusLeitura.ILEGIVEL, "NOME", "TEXTO QUE O MODELO ESCREVEU"),
    }
    campo = _decidir(campos).campos["nome"]
    assert campo.valor is None and campo.valor_normalizado is None


def test_campo_nao_devolvido_vira_ilegivel_e_campo_extra_e_descartado() -> None:
    campos = {
        "cpf": CampoLido(StatusLeitura.LEGIVEL, "CPF", CPF),
        "campo_inventado": CampoLido(StatusLeitura.LEGIVEL, "", "X"),
    }
    resultado = _decidir(campos)
    assert "campo_inventado" not in resultado.campos
    assert resultado.campos["nome"].status_leitura is StatusLeitura.ILEGIVEL


def test_critico_ausente_com_outros_lidos_vai_para_revisao() -> None:
    subtipo = Subtipo.RG_ANTIGO
    campos = {
        n: CampoLido(StatusLeitura.LEGIVEL, "", "01/02/2010" if "data" in n else "SP")
        for n in nomes_criticos(subtipo)
    }
    campos["nome"] = CampoLido(StatusLeitura.AUSENTE, "", "")
    resultado = _decidir(campos, subtipo=subtipo)
    assert resultado.status is StatusDocumento.REVISAO
    assert resultado.bloqueios["nome"]


def test_todos_criticos_ausentes_e_lado_sem_dados() -> None:
    subtipo = Subtipo.RG_ANTIGO
    campos = {n: CampoLido(StatusLeitura.AUSENTE, "", "") for n in nomes_criticos(subtipo)}
    with pytest.raises(FalhaExtracao) as erro:
        _decidir(campos, subtipo=subtipo)
    assert [m.codigo.value for m in erro.value.motivos] == ["LADO_SEM_DADOS"]


def test_manuscrito_critico_exige_dupla_leitura(monkeypatch) -> None:
    from app.schemas import campos as mod_campos
    from app.schemas.campos import DefinicaoCampo, Escrita
    from app.schemas.normalizacao import TipoValor

    original = mod_campos.CAMPOS_POR_SUBTIPO[Subtipo.CPF_CARTAO]
    manuscrito = (DefinicaoCampo("cpf", True, TipoValor.CPF, ("CPF",), Escrita.MANUSCRITO),)
    monkeypatch.setitem(mod_campos.CAMPOS_POR_SUBTIPO, Subtipo.CPF_CARTAO, manuscrito)
    resultado = _decidir(LIMPO)
    assert resultado.status is StatusDocumento.REVISAO
    assert MotivoCampo.MANUSCRITO_SEM_DUPLA_LEITURA in resultado.campos["cpf"].motivos
    monkeypatch.setitem(mod_campos.CAMPOS_POR_SUBTIPO, Subtipo.CPF_CARTAO, original)


def test_classificacao() -> None:
    with pytest.raises(FalhaExtracao, match="NENHUM_DOCUMENTO"):
        decisao.verificar_classificacao(
            TipoDocumento.CPF, _leitura(Subtipo.NAO_IDENTIFICADO, {}, tipo=None)
        )
    with pytest.raises(FalhaExtracao, match="TIPO_DIVERGENTE"):
        decisao.verificar_classificacao(TipoDocumento.CTPS, _leitura(Subtipo.CPF_CARTAO, {}))
    with pytest.raises(FalhaExtracao, match="TIPO_DIVERGENTE"):
        decisao.verificar_classificacao(
            TipoDocumento.CPF, _leitura(Subtipo.CPF_CARTAO, {}, tipo=TipoDocumento.CTPS)
        )


# --- Esteira ponta a ponta com os stubs da Fase 0 -----------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("aprovar_nao_verificavel", [False, True])
async def test_stubs_nunca_aprovam(settings, gravar, aprovar_nao_verificavel) -> None:
    """Leitura perfeita do VLM e do OCR, até com NAO_VERIFICAVEL liberado: ainda REVISAO."""
    config = settings.model_copy(
        update={"aprovar_critico_nao_verificavel": aprovar_nao_verificavel}
    )
    esteira = criar_esteira(config)
    for semente in range(5):
        conteudo = imagem_png(100 + semente)
        gravar(conteudo, gravacao_cpf(cpf_sintetico(semente)))
        resultado = await esteira.processar(
            str(uuid.uuid4()),
            RequisicaoExtracao(tipo_documento=TipoDocumento.CPF, imagem_base64=b64(conteudo)),
        )
        assert resultado.resposta is not None
        assert resultado.resposta.status is StatusDocumento.REVISAO
        assert set(resultado.rastro.stubs) == {"qualidade", "ancoragem", "releitura", "regras"}


@pytest.mark.anyio
async def test_rastro_completo(settings, gravar) -> None:
    conteudo = imagem_png(110)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    resultado = await criar_esteira(settings).processar(
        "id", RequisicaoExtracao(tipo_documento=TipoDocumento.CPF, imagem_base64=b64(conteudo))
    )
    rastro = resultado.rastro
    for etapa in (
        "decodificacao",
        "preprocessamento",
        "leitura_visual",
        "leitura_texto",
        "leituras",
        "ancoragem",
        "localizacao",
        "releitura",
        "regras",
        "decisao",
        "total",
    ):
        assert etapa in rastro.tempos_ms
    assert set(rastro.recortes) == {"cpf", "nome"}
    assert set(rastro.releituras) == {"cpf", "nome"}  # não ancorados seriam relidos
    assert not any(r.executada for r in rastro.releituras.values())
    assert rastro.custo_brl == pytest.approx((3420 * 1 + 600 * 5) / 1e6 * 5.40)
    assert rastro.versao_pipeline.startswith("0.1.0+fake:gpt-6.1-terra+")


@pytest.mark.anyio
async def test_leituras_rodam_em_paralelo(settings, monkeypatch, gravar) -> None:
    from app.providers.fake import FakeLeitorTexto, FakeLeitorVisual

    ler_visual, ler_texto = FakeLeitorVisual.ler, FakeLeitorTexto.ler

    async def visual_lento(self, pedido):
        await asyncio.sleep(0.3)
        return await ler_visual(self, pedido)

    async def texto_lento(self, imagem):
        await asyncio.sleep(0.3)
        return await ler_texto(self, imagem)

    monkeypatch.setattr(FakeLeitorVisual, "ler", visual_lento)
    monkeypatch.setattr(FakeLeitorTexto, "ler", texto_lento)
    conteudo = imagem_png(111)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    resultado = await criar_esteira(settings).processar(
        "id", RequisicaoExtracao(tipo_documento=TipoDocumento.CPF, imagem_base64=b64(conteudo))
    )
    assert resultado.rastro.tempos_ms["leituras"] < 550


def test_schema_do_subtipo_cobre_a_definicao() -> None:
    for subtipo in Subtipo:
        nomes = [d.nome for d in campos_do_subtipo(subtipo)]
        assert len(nomes) == len(set(nomes)), subtipo


@pytest.mark.anyio
async def test_pdf_e_orientacao_exif(settings, gravar) -> None:
    from PIL import Image as PILImage

    # JPEG com orientação EXIF 6 (girar 90°): a imagem normalizada sai "em pé".
    imagem = PILImage.new("RGB", (300, 100), "white")
    exif = PILImage.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    imagem.save(buffer, format="JPEG", exif=exif)
    conteudo = buffer.getvalue()
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    resultado = await criar_esteira(settings).processar(
        "id", RequisicaoExtracao(tipo_documento=TipoDocumento.CPF, imagem_base64=b64(conteudo))
    )
    normalizada = resultado.rastro.imagem_normalizada
    assert normalizada is not None
    assert (normalizada.largura, normalizada.altura) == (100, 300)
    assert Image.open(io.BytesIO(normalizada.conteudo)).format == "PNG"
