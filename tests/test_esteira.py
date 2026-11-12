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
    Rotacao,
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


# --- Esteira ponta a ponta com os stubs da Fase 3 (regras e dígitos) ---------------------


@pytest.mark.anyio
@pytest.mark.parametrize("aprovar_nao_verificavel", [False, True])
async def test_stubs_nunca_aprovam(settings, gravar, aprovar_nao_verificavel) -> None:
    """Leitura perfeita do VLM e do OCR (ancorada), até com NAO_VERIFICAVEL liberado: ainda
    REVISAO, porque as regras são stub e devolvem REGRA_NAO_IMPLEMENTADA."""
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
        assert set(resultado.rastro.stubs) == {"regras", "digitos"}


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
    # cpf (crítico) ancorou; nome é informativo no cartão CPF: nada a reler.
    assert rastro.ancoragens["cpf"].status.value == "ANCORADO"
    assert rastro.ancoragens["nome"].status.value == "NAO_ANCORADO"
    assert rastro.releituras == {}
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


# --- Desenhos da leitura visual, rotação e sinalizações ---------------------------------------


def _req(conteudo: bytes, tipo: TipoDocumento = TipoDocumento.CPF) -> RequisicaoExtracao:
    return RequisicaoExtracao(tipo_documento=tipo, imagem_base64=b64(conteudo))


@pytest.mark.anyio
async def test_desenho_em_duas_etapas(settings, gravar) -> None:
    conteudo = imagem_png(120)
    gravacao = gravacao_cpf(cpf_sintetico())
    assert gravacao.leitura_visual is not None
    lv = gravacao.leitura_visual.model_copy(
        update={"classificacao_tokens_entrada": 1500, "classificacao_tokens_saida": 60}
    )
    gravar(conteudo, gravacao.model_copy(update={"leitura_visual": lv}))
    config = settings.model_copy(update={"leitor_visual_desenho": "duas_etapas"})
    resultado = await criar_esteira(config).processar("id", _req(conteudo))

    assert resultado.resposta is not None
    assert resultado.resposta.campos["cpf"].valor_normalizado is not None
    etapas = [c.etapa for c in resultado.rastro.chamadas]
    assert etapas[:2] == ["classificacao", "extracao"]
    classificacao = resultado.rastro.chamadas[0]
    assert classificacao.modelo == config.leitor_visual_modelo_classificacao
    assert resultado.rastro.chamadas[1].modelo == config.leitor_visual_modelo
    assert "classificacao" in resultado.rastro.tempos_ms
    assert "desenho:duas_etapas/gpt-6-luna" in resultado.resposta.versao_pipeline
    # Desenho único devolve os mesmos campos.
    unico = await criar_esteira(settings).processar("id", _req(conteudo))
    assert unico.resposta is not None
    assert unico.resposta.campos == resultado.resposta.campos


@pytest.mark.anyio
async def test_duas_etapas_recusa_tipo_divergente_sem_extrair(settings, gravar) -> None:
    conteudo = imagem_png(121)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    config = settings.model_copy(update={"leitor_visual_desenho": "duas_etapas"})
    resultado = await criar_esteira(config).processar(
        "id", _req(conteudo, TipoDocumento.IDENTITY_DOCUMENT)
    )
    assert resultado.falha is not None
    assert [m.codigo.value for m in resultado.falha.motivos] == ["TIPO_DIVERGENTE"]
    assert [c.etapa for c in resultado.rastro.chamadas] == ["classificacao"]


@pytest.mark.anyio
@pytest.mark.parametrize("desenho", ["unico", "duas_etapas"])
async def test_rotacao_informada_pelo_vlm_gira_a_imagem(settings, gravar, desenho) -> None:
    from app.providers.interfaces import Rotacao

    conteudo = imagem_png(122, tamanho=(400, 260))
    gravacao = gravacao_cpf(cpf_sintetico())
    assert gravacao.leitura_visual is not None
    lv = gravacao.leitura_visual.model_copy(update={"rotacao": Rotacao.GRAUS_90})
    gravar(conteudo, gravacao.model_copy(update={"leitura_visual": lv}))
    config = settings.model_copy(update={"leitor_visual_desenho": desenho})
    resultado = await criar_esteira(config).processar("id", _req(conteudo))
    preparada = resultado.rastro.imagem_preparada
    assert preparada is not None
    assert (preparada.largura, preparada.altura) == (260, 400)


@pytest.mark.anyio
async def test_resposta_fora_do_schema_e_falha_do_provider(settings, gravar, monkeypatch) -> None:
    from app.providers.fake import FakeLeitorVisual
    from app.providers.interfaces import RespostaVisual

    async def quebrada(self, pedido):
        return RespostaVisual(
            {"documento": {"subtipo": "INVENTADO"}},
            {},
            MetricasChamada("fake", "m", "leitura_visual"),
        )

    monkeypatch.setattr(FakeLeitorVisual, "ler", quebrada)
    resultado = await criar_esteira(settings).processar("id", _req(imagem_png(123)))
    assert resultado.falha is not None
    assert resultado.falha.codigo.value == "PROVIDER_INDISPONIVEL"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("fonte", "esperado"),
    [
        ("vlm", []),
        ("heuristica", ["PRINT_DE_TELA"]),
        ("qualquer", ["PRINT_DE_TELA"]),
        ("ambas", []),
    ],
)
async def test_sinalizacao_de_captura_por_fonte(settings, gravar, fonte, esperado) -> None:
    # imagem_png é um arquivo digital limpo: a heurística vê print; o VLM gravado diz FOTO.
    conteudo = imagem_png(124)
    gravar(conteudo, gravacao_cpf(cpf_sintetico()))
    config = settings.model_copy(update={"sinalizacao_captura_fonte": fonte})
    resultado = await criar_esteira(config).processar("id", _req(conteudo))
    assert resultado.resposta is not None
    assert [s.value for s in resultado.resposta.sinalizacoes] == esperado
    assert resultado.resposta.status is StatusDocumento.REVISAO  # sinalização nunca muda status


@pytest.mark.anyio
async def test_reprovacao_de_qualidade_e_422_sem_chamar_provider(settings, gravar) -> None:
    from PIL import ImageFilter

    imagem = Image.open(io.BytesIO(imagem_png(125))).filter(ImageFilter.GaussianBlur(8))
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    resultado = await criar_esteira(settings).processar("id", _req(buffer.getvalue()))
    assert resultado.falha is not None
    assert "IMAGEM_DESFOCADA" in [m.codigo.value for m in resultado.falha.motivos]
    assert resultado.rastro.chamadas == []


# --- Fase 2: releitura, manuscrito e regras ausentes na decisão ---------------------------


def _resultado_releitura(**campos) -> ResultadoReleitura:
    from app.core.releitura import Disparo

    return ResultadoReleitura("cpf", executada=True, disparo=Disparo.ANCORAGEM_PARCIAL, **campos)


def test_regra_nao_implementada_nunca_aprova() -> None:
    leitura = _leitura(Subtipo.CPF_CARTAO, LIMPO)
    preparados = decisao.preparar_campos(Subtipo.CPF_CARTAO, LIMPO)
    validacao = ResultadoValidacao(
        por_campo={"cpf": StatusValidacao.NAO_VERIFICAVEL},
        motivos={"cpf": [MotivoCampo.REGRA_NAO_IMPLEMENTADA]},
    )
    ancoragens = {n: ResultadoAncoragem(n, StatusAncoragem.ANCORADO) for n in preparados}
    resultado = decisao.decidir(leitura, preparados, ancoragens, {}, validacao, True)
    assert resultado.status is StatusDocumento.REVISAO
    assert "regra_nao_implementada" in resultado.bloqueios["cpf"]


def test_releitura_com_ocr_vale_o_valor_da_concordancia() -> None:
    errado = {"cpf": CampoLido(StatusLeitura.LEGIVEL, "CPF", "111.444.777-36")}
    preparados = decisao.preparar_campos(Subtipo.CPF_CARTAO, errado)
    releituras = {
        "cpf": _resultado_releitura(
            concordancia="2_DE_3",
            leituras_concordantes=("ocr", "releitura"),
            valor_final=CPF,
            ocr_concorda=True,
        )
    }
    finais = decisao.aplicar_releituras(preparados, releituras)
    assert finais["cpf"].valor == CPF and finais["cpf"].valor_normalizado == "11144477735"
    resultado = decisao.decidir(
        _leitura(Subtipo.CPF_CARTAO, errado),
        finais,
        {"cpf": ResultadoAncoragem("cpf", StatusAncoragem.PARCIAL)},
        releituras,
        ResultadoValidacao(por_campo={"cpf": StatusValidacao.VALIDO}, motivos={}),
        False,
    )
    assert resultado.status is StatusDocumento.APROVADO  # OCR confirmou o valor relido
    assert resultado.campos["cpf"].valor == CPF


def test_releitura_sem_ocr_nao_aprova_nem_troca_o_valor() -> None:
    preparados = decisao.preparar_campos(Subtipo.CPF_CARTAO, LIMPO)
    releituras = {
        "cpf": _resultado_releitura(
            concordancia="2_DE_3", leituras_concordantes=("vlm", "releitura"), valor_final=CPF
        )
    }
    assert decisao.aplicar_releituras(preparados, releituras) == preparados
    resultado = _decidir(LIMPO, ancoragem=StatusAncoragem.NAO_ANCORADO, releituras=releituras)
    assert resultado.status is StatusDocumento.REVISAO
    assert MotivoCampo.NAO_ANCORADO in resultado.campos["cpf"].motivos


def test_leituras_divergentes_e_vlm_incompativel() -> None:
    divergente = {"cpf": _resultado_releitura()}
    resultado = _decidir(LIMPO, ancoragem=StatusAncoragem.PARCIAL, releituras=divergente)
    assert MotivoCampo.LEITURAS_DIVERGENTES in resultado.campos["cpf"].motivos

    letra = {"cpf": CampoLido(StatusLeitura.LEGIVEL, "CPF", "111.444.777-3S")}
    leitura = _leitura(Subtipo.CPF_CARTAO, letra)
    preparados = decisao.preparar_campos(Subtipo.CPF_CARTAO, letra)
    resultado = decisao.decidir(
        leitura,
        preparados,
        {"cpf": ResultadoAncoragem("cpf", StatusAncoragem.PARCIAL, vlm_incompativel=True)},
        {},
        ResultadoValidacao(por_campo={"cpf": StatusValidacao.VALIDO}, motivos={}),
        False,
    )
    assert resultado.campos["cpf"].valor_normalizado is None
    assert MotivoCampo.LEITURAS_DIVERGENTES in resultado.campos["cpf"].motivos


@pytest.mark.anyio
async def test_releitura_ponta_a_ponta_corrige_pelo_ocr(settings, gravar, monkeypatch) -> None:
    """VLM erra um dígito; o OCR tem o valor certo; a releitura (escalada) concorda com o
    OCR. O prompt da releitura não leva nenhum valor."""
    from app.providers.fake import FakeLeitorVisual

    certo = cpf_sintetico(3)
    ultimo = str((int(certo[-1]) + 1) % 10)
    errado = certo[:-1] + ultimo
    gravacao = gravacao_cpf(certo)
    gravacao.leitura_visual.campos["cpf"].texto_literal = errado
    gravacao = gravacao.model_validate(
        {
            **gravacao.model_dump(mode="json"),
            "releituras": {
                "cpf": {
                    "status": "LEGIVEL",
                    "rotulo_visto": "CPF",
                    "texto_literal": certo,
                    "modelo": "gpt-6.1-sol",
                    "tokens_entrada": 500,
                    "tokens_saida": 30,
                }
            },
        }
    )
    conteudo = imagem_png(140)
    gravar(conteudo, gravacao)
    pedidos = []
    original = FakeLeitorVisual.ler

    async def espiar(self, pedido):
        pedidos.append(pedido)
        return await original(self, pedido)

    monkeypatch.setattr(FakeLeitorVisual, "ler", espiar)
    config = settings.model_copy(update={"localizador_provider": "ocr"})
    resultado = await criar_esteira(config).processar(
        "id", RequisicaoExtracao(tipo_documento=TipoDocumento.CPF, imagem_base64=b64(conteudo))
    )
    rastro = resultado.rastro
    assert rastro.ancoragens["cpf"].status is StatusAncoragem.PARCIAL  # um dígito
    rel = rastro.releituras["cpf"]
    assert rel.executada and rel.leituras_concordantes == ("ocr", "releitura")
    assert resultado.resposta is not None
    assert resultado.resposta.campos["cpf"].valor == certo
    assert resultado.resposta.status is StatusDocumento.REVISAO  # regras ainda stub
    releitura = next(p for p in pedidos if p.etapa.value == "releitura")
    assert releitura.modelo == "gpt-6.1-sol" and releitura.campo == "cpf"
    assert certo not in releitura.prompt and errado not in releitura.prompt
    assert releitura.imagem.altura >= config.releitura_altura_min_px
    assert "releitura" in {c.etapa for c in rastro.chamadas}
    assert rastro.custo_brl > (3420 * 1 + 600 * 5) / 1e6 * 5.40  # inclui a escalada


@pytest.mark.anyio
async def test_documento_girado_relido_pelo_ocr_de_pe(settings, gravar) -> None:
    conteudo = imagem_png(150)
    gravacao = gravacao_cpf(cpf_sintetico(4))
    gravacao.leitura_visual.rotacao = Rotacao.GRAUS_90
    gravar(conteudo, gravacao)
    resultado = await criar_esteira(settings).processar(
        "id", RequisicaoExtracao(tipo_documento=TipoDocumento.CPF, imagem_base64=b64(conteudo))
    )
    rastro = resultado.rastro
    assert rastro.leitura_texto_antes_de_girar is not None
    assert [c.etapa for c in rastro.chamadas].count("leitura_texto") == 2
