"""Ancoragem no OCR, localizador por OCR e votação da releitura (dados sintéticos)."""

from __future__ import annotations

import asyncio

import pytest

from app.api.contrato import UF, Lado, StatusLeitura, Subtipo, TipoDocumento
from app.config import Settings
from app.core import ancoragem as anc
from app.core.ancoragem import ParametrosAncoragem, StatusAncoragem
from app.core.digitos import ResultadoDigitos
from app.core.localizacao import LocalizadorPorOCR
from app.core.releitura import CampoParaReler, Disparo, executar, planejar, votar
from app.providers.interfaces import (
    Caixa,
    CampoLido,
    ErroProvider,
    Imagem,
    LeituraTexto,
    LeituraVisual,
    MetricasChamada,
    Palavra,
    QualidadeCaptura,
    Releitura,
)
from app.schemas.campos import DefinicaoCampo, definicao

CPF = "111.444.777-35"
PARAM = ParametrosAncoragem.de(Settings(_env_file=None), "CPF_CARTAO")


def _defn(subtipo: Subtipo, nome: str) -> DefinicaoCampo:
    d = definicao(subtipo, nome)
    assert d is not None
    return d


D_CPF = _defn(Subtipo.CPF_CARTAO, "cpf")
D_NOME = _defn(Subtipo.RG_2018_2022, "nome")
D_EXPEDICAO = _defn(Subtipo.RG_2018_2022, "data_expedicao")
D_UF = _defn(Subtipo.RG_2018_2022, "uf_emissao")
D_MANUSCRITO = _defn(Subtipo.CTPS_FISICA, "contrato_cargo")


def ocr(*palavras: tuple[str, int, int, int, int]) -> LeituraTexto:
    """Palavras (texto, x, y, largura, altura); ids na ordem dada."""
    return LeituraTexto(
        palavras=[
            Palavra(i, t, Caixa(x, y, w, h), 0.95) for i, (t, x, y, w, h) in enumerate(palavras)
        ],
        resposta_bruta={},
        metricas=MetricasChamada(provider="t", modelo="t", etapa="leitura_texto"),
    )


def lido(
    texto: str, rotulo: str = "CPF", status: StatusLeitura = StatusLeitura.LEGIVEL
) -> CampoLido:
    return CampoLido(status, rotulo, texto)


def ancorar(defn: DefinicaoCampo, campo: CampoLido | None, leitura: LeituraTexto):
    return anc.ancorar_campo(defn, campo, leitura, PARAM)


# --- Ancoragem ----------------------------------------------------------------------------------


def test_valor_a_direita_do_rotulo_ancora() -> None:
    r = ancorar(D_CPF, lido(CPF), ocr(("CPF", 20, 100, 40, 20), (CPF, 70, 100, 160, 20)))
    assert r.status is StatusAncoragem.ANCORADO and r.detalhe == "exato"
    assert r.palavras_ids == [1] and r.rotulo_ids == [0]
    assert r.caixa_valor == Caixa(70, 100, 160, 20)


def test_valor_na_linha_abaixo_ancora() -> None:
    r = ancorar(D_CPF, lido(CPF), ocr(("CPF", 20, 100, 40, 16), (CPF, 22, 124, 160, 22)))
    assert r.status is StatusAncoragem.ANCORADO


def test_valor_em_varias_palavras_e_pontuacao_diferente() -> None:
    leitura = ocr(
        ("CPF", 20, 100, 40, 20), ("111444", 70, 100, 70, 20), ("777/35", 145, 100, 70, 20)
    )
    r = ancorar(D_CPF, lido(CPF), leitura)
    assert r.status is StatusAncoragem.ANCORADO and r.palavras_ids == [1, 2]


def test_rotulo_colado_no_valor() -> None:
    r = ancorar(D_CPF, lido(CPF), ocr(("CPF:111.444.777-35", 20, 100, 220, 20)))
    # Rótulo e valor na mesma palavra: a posição está garantida.
    assert r.status is StatusAncoragem.ANCORADO and r.palavras_ids == [0]


def test_confusao_do_ocr_vale_conforme_o_tipo() -> None:
    # Campo numérico: O e I lidos pelo OCR contam como 0 e 1.
    r = ancorar(
        D_CPF, lido(CPF), ocr(("CPF", 20, 100, 40, 20), ("III.444.777-35", 70, 100, 160, 20))
    )
    assert r.status is StatusAncoragem.ANCORADO
    # Campo de texto: o 1 e o 0 do OCR contam como I e O.
    r = ancorar(
        D_NOME,
        lido("LIMO TOVA", "NOME"),
        ocr(("NOME", 20, 50, 60, 20), ("L1M0", 90, 50, 60, 20), ("TOVA", 160, 50, 60, 20)),
    )
    assert r.status is StatusAncoragem.ANCORADO
    # Mas nunca a letra no lugar do dígito num campo de texto.
    r = ancorar(D_UF, lido("SP", "UF"), ocr(("UF", 20, 50, 30, 20), ("5P", 60, 50, 30, 20)))
    assert r.status is StatusAncoragem.ANCORADO  # 5→S vale em UF (texto)


def test_confusao_nunca_altera_o_valor_do_vlm() -> None:
    # O VLM escreveu letra num CPF: PARCIAL (LEITURAS_DIVERGENTES), mesmo com o OCR igual.
    r = ancorar(
        D_CPF,
        lido("111.444.777-3S"),
        ocr(("CPF", 20, 100, 40, 20), ("111.444.777-3S", 70, 100, 160, 20)),
    )
    assert r.status is StatusAncoragem.PARCIAL and r.vlm_incompativel
    assert r.palavras_ids == [1]  # localizado mesmo assim, para a releitura


def test_coringa_do_vlm_casa_com_qualquer_caractere() -> None:
    r = ancorar(
        D_CPF,
        lido("111.444.7?7-35", status=StatusLeitura.PARCIAL),
        ocr(("CPF", 20, 100, 40, 20), (CPF, 70, 100, 160, 20)),
    )
    assert r.status is StatusAncoragem.ANCORADO


def test_acento_e_cedilha_tolerados_no_rotulo_e_no_valor() -> None:
    leitura = ocr(("EXPEDIO", 20, 100, 90, 20), ("15/01/2019", 120, 100, 120, 20))
    r = ancorar(D_EXPEDICAO, lido("15/01/2019", "EXPEDIÇÃO"), leitura)
    assert r.status is StatusAncoragem.ANCORADO
    r = ancorar(
        D_NOME,
        lido("JOÃO LIMA", "NOME"),
        ocr(("NOME", 20, 50, 60, 20), ("JOAO", 90, 50, 60, 20), ("LIMA", 160, 50, 60, 20)),
    )
    assert r.status is StatusAncoragem.ANCORADO


def test_fora_da_regiao_ou_sem_rotulo_fica_parcial() -> None:
    longe = ocr(("CPF", 20, 100, 40, 20), (CPF, 300, 600, 160, 20))
    r = ancorar(D_CPF, lido(CPF), longe)
    assert r.status is StatusAncoragem.PARCIAL and r.detalhe == "fora_da_regiao"
    r = ancorar(D_CPF, lido(CPF), ocr((CPF, 70, 100, 160, 20)))
    assert r.status is StatusAncoragem.PARCIAL and r.detalhe == "sem_rotulo"


def test_um_caractere_de_diferenca() -> None:
    perto = ocr(("CPF", 20, 100, 40, 20), ("111.444.777-36", 70, 100, 160, 20))
    r = ancorar(D_CPF, lido(CPF), perto)
    assert r.status is StatusAncoragem.PARCIAL and r.detalhe == "um_caractere"
    # Valor curto (UF): um caractere de diferença não é evidência.
    r = ancorar(D_UF, lido("SP", "UF"), ocr(("UF", 20, 50, 30, 20), ("SC", 60, 50, 30, 20)))
    assert r.status is StatusAncoragem.NAO_ANCORADO
    # Dois caracteres: não ancora.
    r = ancorar(
        D_CPF, lido(CPF), ocr(("CPF", 20, 100, 40, 20), ("111.444.777-99", 70, 100, 160, 20))
    )
    assert r.status is StatusAncoragem.NAO_ANCORADO


def test_nada_a_ancorar() -> None:
    leitura = ocr(("CPF", 20, 100, 40, 20))
    assert ancorar(D_CPF, None, leitura).status is StatusAncoragem.NAO_APLICAVEL
    assert (
        ancorar(D_CPF, lido("", status=StatusLeitura.AUSENTE), leitura).status
        is StatusAncoragem.NAO_APLICAVEL
    )
    r = ancorar(
        D_MANUSCRITO,
        lido("AUXILIAR", "CARGO"),
        ocr(("CARGO", 1, 1, 50, 20), ("AUXILIAR", 60, 1, 90, 20)),
    )
    assert r.status is StatusAncoragem.NAO_APLICAVEL and r.detalhe == "manuscrito"
    assert ancorar(D_CPF, lido(CPF), leitura).status is StatusAncoragem.NAO_ANCORADO


def test_regiao_configuravel_por_subtipo() -> None:
    estreita = Settings(
        _env_file=None,
        ancoragem_regioes={"CPF_CARTAO": {"direita_max_alturas": 1, "abaixo_max_alturas": 1}},
    )
    parametros = ParametrosAncoragem.de(estreita, "CPF_CARTAO")
    leitura = ocr(("CPF", 20, 100, 40, 20), (CPF, 200, 100, 160, 20))
    assert (
        anc.ancorar_campo(D_CPF, lido(CPF), leitura, parametros).status is StatusAncoragem.PARCIAL
    )
    assert anc.ancorar_campo(D_CPF, lido(CPF), leitura, PARAM).status is StatusAncoragem.ANCORADO


# --- Localizador ---------------------------------------------------------------------------------


def _visual(campos: dict[str, CampoLido]) -> LeituraVisual:
    return LeituraVisual(
        subtipo=Subtipo.CPF_CARTAO,
        uf=UF.SP,
        lado=Lado.FRENTE,
        tipo_documento_detectado=TipoDocumento.CPF,
        qualidade_captura=QualidadeCaptura.FOTO,
        campos=campos,
        resposta_bruta={},
        metricas=MetricasChamada(provider="t", modelo="principal", etapa="leitura_visual"),
    )


def test_localizador_usa_a_evidencia_ou_a_regiao_do_rotulo() -> None:
    imagem = Imagem(b"", "image/png", 800, 400, "x", "x")
    leitura = ocr(("CPF", 20, 100, 40, 20), (CPF, 70, 100, 160, 20), ("NOME", 20, 200, 60, 20))
    visual = _visual({"cpf": lido(CPF), "nome": lido("LIMO TOVA", "NOME")})
    loc = asyncio.run(
        LocalizadorPorOCR(Settings(_env_file=None)).localizar(
            imagem, visual, leitura, ["cpf", "nome", "data_nascimento"]
        )
    )
    assert loc.caixas["cpf"] == Caixa(70, 100, 160, 20)
    nome = loc.caixas["nome"]  # sem valor no OCR: região do rótulo (direita e abaixo)
    assert nome.x <= 20 and nome.y == 200 and nome.largura > 60 and nome.altura > 20
    assert nome.x + nome.largura <= 800
    assert "data_nascimento" not in loc.caixas  # sem rótulo nem valor: sem caixa


# --- Releitura -----------------------------------------------------------------------------------


def _releitura(texto: str, modelo: str = "escalada", status=StatusLeitura.LEGIVEL) -> Releitura:
    return Releitura(
        campo=CampoLido(status, "CPF", texto),
        resposta_bruta={},
        metricas=MetricasChamada(provider="t", modelo=modelo, etapa="releitura"),
    )


def _campo(
    texto: str, defn: DefinicaoCampo = D_CPF, status=StatusLeitura.LEGIVEL
) -> CampoParaReler:
    return CampoParaReler(defn, status, texto, "CPF")


OCR_CPF = ocr(("CPF", 20, 100, 40, 20), (CPF, 70, 100, 160, 20))
ERRADO = "111.444.777-36"


def _votar(campo, releitura, leitura=OCR_CPF, disparo=Disparo.ANCORAGEM_PARCIAL):
    ancoragem = anc.ancorar_campo(
        campo.definicao, CampoLido(campo.status_leitura, "CPF", campo.texto_vlm), leitura, PARAM
    )
    return votar(campo, disparo, ancoragem, releitura, leitura, PARAM, "principal")


def test_ocr_e_releitura_concordam_contra_o_vlm() -> None:
    r = _votar(_campo(ERRADO), _releitura(CPF))
    assert r.concordancia == "2_DE_3" and r.leituras_concordantes == ("ocr", "releitura")
    assert r.valor_final == CPF and r.ocr_concorda


def test_tres_concordam() -> None:
    r = _votar(_campo(CPF), _releitura(CPF), disparo=Disparo.DIGITO_VERIFICADOR)
    assert r.concordancia == "3_DE_3" and r.ocr_concorda and r.valor_final == CPF


def test_vlm_e_releitura_sem_ocr_ficam_em_revisao() -> None:
    sem_valor = ocr(("CPF", 20, 100, 40, 20))
    r = _votar(_campo(CPF), _releitura(CPF), leitura=sem_valor, disparo=Disparo.NAO_ANCORADO)
    assert r.concordancia == "2_DE_3" and r.leituras_concordantes == ("vlm", "releitura")
    assert not r.ocr_concorda


def test_tres_leituras_diferentes() -> None:
    r = _votar(_campo(ERRADO), _releitura("111.444.777-99"))
    assert r.concordancia is None and r.valor_final is None and r.executada


def test_releitura_ilegivel_nao_concorda() -> None:
    r = _votar(_campo(ERRADO), _releitura("", status=StatusLeitura.ILEGIVEL))
    assert r.concordancia is None


def test_manuscrito_exige_modelos_diferentes_concordando() -> None:
    campo = _campo("AUXILIAR", D_MANUSCRITO)
    vazio = ocr()
    ok = votar(campo, Disparo.MANUSCRITO, None, _releitura("AUXILIAR"), vazio, PARAM, "principal")
    assert ok.dupla_leitura and ok.concordancia == "2_DE_2"
    mesmo_modelo = votar(
        campo,
        Disparo.MANUSCRITO,
        None,
        _releitura("AUXILIAR", "principal"),
        vazio,
        PARAM,
        "principal",
    )
    assert not mesmo_modelo.dupla_leitura


def test_planejar_so_criticos_lidos() -> None:
    campos = {
        "cpf": _campo(ERRADO),
        "nome": CampoParaReler(
            _defn(Subtipo.CPF_CARTAO, "nome"), StatusLeitura.LEGIVEL, "X", "NOME"
        ),
    }
    ancoragens = {
        "cpf": anc.ResultadoAncoragem("cpf", StatusAncoragem.PARCIAL),
        "nome": anc.ResultadoAncoragem("nome", StatusAncoragem.NAO_ANCORADO),
    }
    assert planejar(campos, ancoragens, {}) == {"cpf": Disparo.ANCORAGEM_PARCIAL}
    ancorado = {"cpf": anc.ResultadoAncoragem("cpf", StatusAncoragem.ANCORADO)}
    assert planejar(campos, ancorado, {}) == {}
    assert planejar(campos, ancorado, {"cpf": ResultadoDigitos.INVALIDO}) == {
        "cpf": Disparo.DIGITO_VERIFICADOR
    }
    duvidoso = {"cpf": _campo("111.444.7?7-35", status=StatusLeitura.PARCIAL)}
    assert planejar(duvidoso, ancorado, {}) == {"cpf": Disparo.LEITURA_PARCIAL}
    ilegivel = {"cpf": _campo("", status=StatusLeitura.ILEGIVEL)}
    assert planejar(ilegivel, {}, {}) == {}


def test_executar_sem_caixa_e_com_erro_do_provider() -> None:
    campos = {"cpf": _campo(ERRADO)}

    async def falha(nome: str) -> Releitura:
        raise ErroProvider("x")

    sem_caixa = asyncio.run(
        executar(
            {"cpf": Disparo.NAO_ANCORADO},
            campos,
            {},
            lambda n: False,
            falha,
            OCR_CPF,
            PARAM,
            "principal",
        )
    )
    assert sem_caixa["cpf"].motivo_nao_executada == "sem_caixa" and not sem_caixa["cpf"].executada
    erro = asyncio.run(
        executar(
            {"cpf": Disparo.NAO_ANCORADO},
            campos,
            {},
            lambda n: True,
            falha,
            OCR_CPF,
            PARAM,
            "principal",
        )
    )
    motivo = erro["cpf"].motivo_nao_executada
    assert motivo is not None and motivo.startswith("erro_provider")


@pytest.mark.parametrize("texto", ["111.444.777-35", "111 444 777 35"])
def test_chave_ignora_espaco_e_pontuacao(texto: str) -> None:
    assert anc.chave(texto) == "11144477735"
