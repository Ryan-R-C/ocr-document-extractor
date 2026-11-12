"""Motor de regras: cada regra nas fronteiras, só com dados sintéticos."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

import pytest

from app.api.contrato import UF, MotivoCampo, Sinalizacao, StatusLeitura, StatusValidacao, Subtipo
from app.regras import digitos, motor
from app.regras.base import (
    EntradaRegra,
    Parametros,
    Referencia,
    Regra,
    idade,
    somar_anos,
    valido,
)
from app.regras.conjuntos import REGRAS, cin, cnh, ctps
from app.regras.tabelas import Tabelas, carregar
from app.schemas.campos import Verificador, definicao
from avaliacao.sintetico import gerar_cpf

HOJE = date(2026, 11, 12)
V, INV, S, NV = (
    StatusValidacao.VALIDO,
    StatusValidacao.INVALIDO,
    StatusValidacao.SUSPEITO,
    StatusValidacao.NAO_VERIFICAVEL,
)


def br(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def entradas(subtipo: Subtipo, **valores: str | None) -> dict[str, EntradaRegra]:
    saida = {}
    for nome, valor in valores.items():
        defn = definicao(subtipo, nome)
        assert defn is not None, nome
        if valor is None:
            saida[nome] = EntradaRegra(StatusLeitura.AUSENTE, None, None)
        else:
            saida[nome] = EntradaRegra(StatusLeitura.LEGIVEL, valor, defn.normalizar(valor))
    return saida


def validar(
    subtipo: Subtipo,
    hoje: date = HOJE,
    tabelas: Tabelas | None = None,
    uf: UF | None = None,
    **valores: str | None,
) -> motor.ResultadoValidacao:
    return motor.validar(
        subtipo,
        entradas(subtipo, **valores),
        data_processamento=hoje,
        parametros=Parametros(),
        tabelas=tabelas,
        uf_leitura=uf,
    )


def cpf(semente: int = 1) -> str:
    return gerar_cpf(random.Random(semente))


def com_dv(verificador: Verificador, tamanho: int, semente: int = 0) -> str:
    rng = random.Random(semente)
    while True:
        candidato = "".join(str(rng.randint(0, 9)) for _ in range(tamanho))
        if digitos.verificar(verificador, candidato):
            return candidato


def troca_ultimo(valor: str) -> str:
    return valor[:-1] + str((int(valor[-1]) + 1) % 10)


# --- Utilitários de data -------------------------------------------------------------------------


def test_idade_conta_o_dia_do_aniversario() -> None:
    nasc = date(2000, 5, 10)
    assert idade(nasc, date(2018, 5, 9)) == 17
    assert idade(nasc, date(2018, 5, 10)) == 18


def test_somar_anos_em_29_de_fevereiro() -> None:
    assert somar_anos(date(2024, 2, 29), 1) == date(2025, 2, 28)
    assert somar_anos(date(2024, 2, 29), 4) == date(2028, 2, 29)


# --- Dígitos verificadores ----------------------------------------------------------------------


def test_cpf_valido_invalido_e_onze_iguais() -> None:
    bom = digitos.verificar(Verificador.CPF, cpf().replace(".", "").replace("-", ""))
    assert bom
    assert not digitos.verificar(Verificador.CPF, "11111111111")
    assert not digitos.verificar(Verificador.CPF, "00000000000")


def test_cpf_curto_nunca_e_completado_com_zeros() -> None:
    """O validate-docbr completaria '0123456789' com um zero à esquerda; aqui não."""
    numero = cpf(2).replace(".", "").replace("-", "")
    assert digitos.verificar(Verificador.CPF, numero)
    if numero.startswith("0"):
        assert not digitos.verificar(Verificador.CPF, numero[1:])
    assert not digitos.verificar(Verificador.CPF, numero[:-1])


def test_cnpj_numerico_e_alfanumerico() -> None:
    numerico = com_dv(Verificador.CNPJ, 14)
    assert digitos.cnpj_valido(numerico)
    assert not digitos.cnpj_valido(troca_ultimo(numerico))
    # Exemplo de CNPJ alfanumérico divulgado pela Receita Federal.
    assert digitos.cnpj_valido("12ABC34501DE35")
    assert not digitos.cnpj_valido("12ABC34501DE36")
    assert not digitos.cnpj_valido("AAAAAAAAAAAA00")


def test_rg_sp_com_x() -> None:
    rng = random.Random(5)
    for _ in range(200):
        base = "".join(str(rng.randint(0, 9)) for _ in range(8))
        if len(set(base)) == 1:
            continue
        dv = digitos.dv_rg_sp(base)
        assert digitos.rg_sp_valido(base + dv)
        outro = "0" if dv != "0" else "1"
        assert not digitos.rg_sp_valido(base + outro)
    # Algum número tem DV X (resto 10): o formato aceita o X só no fim.
    assert any(digitos.dv_rg_sp(f"{n:08d}") == "X" for n in range(10_000_000, 10_000_200))


@pytest.mark.parametrize(
    ("verificador", "tamanho"),
    [
        (Verificador.CNH, 11),
        (Verificador.PIS, 11),
        (Verificador.TITULO_ELEITOR, 12),
        (Verificador.CNS, 15),
    ],
)
def test_outros_verificadores(verificador: Verificador, tamanho: int) -> None:
    numero = com_dv(verificador, tamanho)
    assert digitos.verificar(verificador, numero)
    assert not digitos.verificar(verificador, numero[:-1])  # tamanho exato
    assert not digitos.verificar(verificador, troca_ultimo(numero)) or verificador is (
        Verificador.CNS
    )


def test_checar_digitos_da_releitura_rg_fora_de_sp() -> None:
    defn = definicao(Subtipo.RG_ANTIGO, "numero_rg")
    assert defn is not None
    base = "12345678"
    valor = base + digitos.dv_rg_sp(base)
    assert digitos.checar_digitos(defn, valor, UF.SP).value == "VALIDO"
    assert (
        digitos.checar_digitos(
            defn, troca_ultimo(valor) if valor[-1] != "X" else base + "0", UF.SP
        ).value
        == "INVALIDO"
    )
    assert digitos.checar_digitos(defn, valor, UF.RJ).value == "NAO_APLICAVEL"
    assert digitos.checar_digitos(defn, "1234567", UF.SP).value == "NAO_APLICAVEL"


# --- Motor: agregação, sem regra, vigência -------------------------------------------------------


def test_sem_regra_e_valido_e_sem_valor_e_nao_verificavel() -> None:
    r = validar(Subtipo.CPF_CARTAO, cpf=cpf(), nome="FULANO SINTETICO", data_nascimento=None)
    assert r.por_campo == {"cpf": V, "nome": V, "data_nascimento": NV}
    assert any(a.id == "sem_regra" and a.campo == "nome" for a in r.regras_aplicadas)
    assert any(a.id == "digitos.cpf" and a.campo == "cpf" for a in r.regras_aplicadas)
    assert r.versao == motor.VERSAO_REGRAS


def test_agregacao_pela_gravidade() -> None:
    nasc = date(2015, 1, 1)  # 11 anos: idade abaixo do mínimo (SUSPEITO)
    r = validar(
        Subtipo.CPF_CARTAO,
        cpf=troca_ultimo(cpf()),
        nome="X",
        data_nascimento=br(nasc),
    )
    assert r.por_campo["cpf"] is INV
    assert r.motivos["cpf"] == [MotivoCampo.DIGITO_VERIFICADOR_INVALIDO]
    assert r.por_campo["data_nascimento"] is S


def _regra_vigente(inicio: date, fim: date, referencia: Referencia) -> Regra:
    return Regra(
        id="teste.vigencia",
        versao="1",
        descricao="",
        fonte="",
        subtipos=frozenset({Subtipo.CNH}),
        campos=("categoria",),
        funcao=lambda ctx: [valido("categoria", "rodou")],
        referencia=referencia,
        campo_data_referencia="data_emissao" if referencia is Referencia.DOCUMENTO else None,
        vigencia_inicio=inicio,
        vigencia_fim=fim,
    )


@pytest.mark.parametrize(
    ("dia", "roda"),
    [
        (date(2026, 1, 31), False),  # antes
        (date(2026, 2, 1), True),  # no início
        (date(2026, 2, 28), True),  # no fim
        (date(2026, 3, 1), False),  # depois
    ],
)
@pytest.mark.parametrize("referencia", [Referencia.PROCESSAMENTO, Referencia.DOCUMENTO])
def test_quatro_estados_de_vigencia(dia: date, roda: bool, referencia: Referencia) -> None:
    regra = _regra_vigente(date(2026, 2, 1), date(2026, 2, 28), referencia)
    hoje = dia if referencia is Referencia.PROCESSAMENTO else HOJE
    r = motor.validar(
        Subtipo.CNH,
        entradas(Subtipo.CNH, categoria="B", data_emissao=br(dia)),
        data_processamento=hoje,
        regras=[regra],
    )
    rodou = any(a.detalhe == "rodou" for a in r.regras_aplicadas)
    assert rodou is roda


def test_vigencia_pelo_documento_sem_a_data_nao_verifica() -> None:
    regra = _regra_vigente(date(2026, 2, 1), date(2026, 2, 28), Referencia.DOCUMENTO)
    r = motor.validar(
        Subtipo.CNH,
        entradas(Subtipo.CNH, categoria="B", data_emissao=None),
        data_processamento=HOJE,
        regras=[regra],
    )
    assert r.por_campo["categoria"] is NV
    assert r.motivos["categoria"] == [MotivoCampo.VALIDACAO_INDISPONIVEL]


def test_versao_muda_com_regras_e_tabelas() -> None:
    tabelas = carregar()
    assert motor._versao(REGRAS, tabelas.versao) == motor.VERSAO_REGRAS
    assert motor._versao(REGRAS[:-1], tabelas.versao) != motor.VERSAO_REGRAS
    assert motor._versao(REGRAS, tabelas.versao + "x") != motor.VERSAO_REGRAS
    assert "+" not in motor.VERSAO_REGRAS  # versao_pipeline usa + como separador


# --- Datas comuns -------------------------------------------------------------------------------


def test_datas_futuras_e_anteriores_ao_nascimento() -> None:
    r = validar(
        Subtipo.RG_ANTIGO,
        data_nascimento=br(HOJE + timedelta(days=1)),
        data_expedicao=br(HOJE + timedelta(days=1)),
    )
    assert r.por_campo["data_nascimento"] is INV and r.por_campo["data_expedicao"] is INV
    r = validar(Subtipo.RG_ANTIGO, data_nascimento="10/05/1990", data_expedicao="09/05/1990")
    assert r.por_campo["data_expedicao"] is INV
    assert MotivoCampo.DATA_INCOERENTE in r.motivos["data_expedicao"]
    r = validar(Subtipo.RG_ANTIGO, data_nascimento="10/05/1990", data_expedicao="10/05/1990")
    assert r.por_campo["data_expedicao"] is V
    r = validar(Subtipo.RG_ANTIGO, data_nascimento="31/12/1899")
    assert r.por_campo["data_nascimento"] is INV


@pytest.mark.parametrize(("anos_atras", "esperado"), [(13, S), (14, V), (120, V), (121, S)])
def test_idade_plausivel(anos_atras: int, esperado: StatusValidacao) -> None:
    nasc = somar_anos(HOJE, -anos_atras)
    r = validar(Subtipo.CPF_CARTAO, cpf=cpf(), data_nascimento=br(nasc))
    assert r.por_campo["data_nascimento"] is esperado


# --- RG ------------------------------------------------------------------------------------------


def _rg_sp(base: str = "23456789") -> str:
    return base + digitos.dv_rg_sp(base)


def test_rg_numero_por_uf() -> None:
    valido_sp = _rg_sp()
    assert (
        validar(Subtipo.RG_ANTIGO, numero_rg=valido_sp, uf_emissao="SP").por_campo["numero_rg"] is V
    )
    errado = valido_sp[:-1] + ("0" if valido_sp[-1] != "0" else "1")
    r = validar(Subtipo.RG_ANTIGO, numero_rg=errado, uf_emissao="SP")
    assert r.por_campo["numero_rg"] is INV
    r = validar(Subtipo.RG_ANTIGO, numero_rg="1234567", uf_emissao="SP")
    assert r.por_campo["numero_rg"] is NV
    r = validar(Subtipo.RG_ANTIGO, numero_rg="12.345.678-9", uf_emissao="RJ")
    assert r.por_campo["numero_rg"] is NV
    assert r.motivos["numero_rg"] == [MotivoCampo.VALIDACAO_INDISPONIVEL]
    r = validar(Subtipo.RG_ANTIGO, numero_rg="12A45678", uf_emissao="MG")
    assert r.por_campo["numero_rg"] is S
    # Sem o campo de UF, vale a UF classificada pelo VLM.
    r = validar(Subtipo.RG_ANTIGO, uf=UF.SP, numero_rg=valido_sp)
    assert r.por_campo["numero_rg"] is V


@pytest.mark.parametrize(
    ("orgao", "uf", "esperado"),
    [
        ("SSP/SP", "SP", V),
        ("SSP-SP", "SP", V),
        ("SSP SP", "SP", V),
        ("SSPSP", "SP", V),
        ("S.S.P./RJ", "RJ", V),
        ("SSP", "MG", V),
        ("SSP/RJ", "SP", S),
        ("DETRAN/RJ", "RJ", NV),
        ("SSP/XX", "SP", NV),
    ],
)
def test_orgao_emissor(orgao: str, uf: str, esperado: StatusValidacao) -> None:
    r = validar(Subtipo.RG_2018_2022, orgao_emissor=orgao, uf_emissao=uf)
    assert r.por_campo["orgao_emissor"] is esperado


def _matricula(ano: int = 1990, codigo: str = "55") -> str:
    base = "123456" + "01" + codigo + f"{ano:04d}" + "1" + "00012" + "034" + "0005678"
    assert len(base) == 30
    for dv1 in range(10):
        for dv2 in range(10):
            if digitos.certidao_valida(base + f"{dv1}{dv2}"):
                return base + f"{dv1}{dv2}"
    raise AssertionError


def test_matricula_de_certidao() -> None:
    m = _matricula()
    ok = validar(Subtipo.RG_ANTIGO, documento_origem=f"C.NASC {m}", data_nascimento="01/02/1990")
    assert ok.por_campo["documento_origem"] is V
    dv = troca_ultimo(m)
    r = validar(Subtipo.RG_ANTIGO, documento_origem=dv, data_nascimento="01/02/1990")
    assert r.por_campo["documento_origem"] is S
    r = validar(Subtipo.RG_ANTIGO, documento_origem=_matricula(codigo="44"))
    assert r.por_campo["documento_origem"] is S
    # Registro no ano do nascimento vale; antes, não.
    r = validar(
        Subtipo.RG_ANTIGO, documento_origem=_matricula(ano=1989), data_nascimento="01/02/1990"
    )
    assert r.por_campo["documento_origem"] is S
    # Texto sem matrícula de 32 dígitos: não há regra aplicável.
    r = validar(Subtipo.RG_ANTIGO, documento_origem="CERT NASC LV 12 FL 34 TERMO 5678")
    assert r.por_campo["documento_origem"] is V


def test_naturalidade_com_e_sem_tabela() -> None:
    sem = validar(Subtipo.RG_ANTIGO, naturalidade="SAO PAULO - SP")
    assert sem.por_campo["naturalidade"] is NV  # tabela do IBGE ainda ausente
    tabelas = Tabelas(
        orgaos={},
        municipios={"SP": frozenset({"SAOPAULO", "MOGIDASCRUZES"})},
        cbo=None,
        versoes={},
    )
    for texto in ("São Paulo - SP", "SAO PAULO/SP", "Mogi das Cruzes, SP"):
        r = validar(Subtipo.RG_ANTIGO, tabelas=tabelas, naturalidade=texto)
        assert r.por_campo["naturalidade"] is V, texto
    r = validar(Subtipo.RG_ANTIGO, tabelas=tabelas, naturalidade="SAO PAULO - RJ")
    assert r.por_campo["naturalidade"] is NV
    assert r.motivos["naturalidade"] == [MotivoCampo.FORA_DA_TABELA]


@pytest.mark.parametrize(
    ("hoje", "nascimento", "vencido"),
    [
        (date(2032, 2, 28), date(1980, 1, 1), False),  # último dia de validade
        (date(2032, 3, 1), date(1980, 1, 1), True),  # vencido
        (date(2032, 3, 1), date(1962, 3, 1), False),  # 60 anos em 01/03/2022
        (date(2032, 3, 1), date(1962, 3, 2), True),  # 59 anos em 01/03/2022
    ],
)
def test_rg_antigo_validade(hoje: date, nascimento: date, vencido: bool) -> None:
    r = validar(Subtipo.RG_ANTIGO, hoje=hoje, data_nascimento=br(nascimento))
    assert (Sinalizacao.DOCUMENTO_VENCIDO in r.sinalizacoes) is vencido
    assert r.por_campo["data_nascimento"] is V  # sinalização não muda o campo


# --- CIN -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("anos", "prazo"), [(11, 5), (12, 10), (59, 10), (60, None)])
def test_cin_validade_pela_idade_na_expedicao(anos: int, prazo: int | None) -> None:
    expedicao = date(2024, 6, 1)
    nascimento = somar_anos(expedicao, -anos)
    assert cin.prazo_cin(idade(nascimento, expedicao)) == prazo
    certa = "INDETERMINADA" if prazo is None else br(somar_anos(expedicao, prazo))
    r = validar(
        Subtipo.CIN,
        data_nascimento=br(nascimento),
        data_expedicao=br(expedicao),
        validade=certa,
    )
    assert r.por_campo["validade"] is V
    # Prazo da faixa vizinha: incoerente.
    errada = (
        br(somar_anos(expedicao, 10))
        if prazo is None
        else ("INDETERMINADA" if prazo == 10 and anos == 59 else br(somar_anos(expedicao, 15)))
    )
    r = validar(
        Subtipo.CIN,
        data_nascimento=br(nascimento),
        data_expedicao=br(expedicao),
        validade=errada,
    )
    assert r.por_campo["validade"] in (S, INV)


def test_cin_validade_um_dia_antes_dos_12() -> None:
    expedicao = date(2024, 6, 1)
    nascimento = somar_anos(expedicao, -12) + timedelta(days=1)  # 11 anos
    r = validar(
        Subtipo.CIN,
        data_nascimento=br(nascimento),
        data_expedicao=br(expedicao),
        validade=br(somar_anos(expedicao, 10)),
    )
    assert r.por_campo["validade"] is S


def test_cin_validade_sem_expedicao_nao_verifica_e_vencida_sinaliza() -> None:
    r = validar(Subtipo.CIN, validade="01/01/2030", data_nascimento="01/01/1990")
    assert r.por_campo["validade"] is NV
    r = validar(Subtipo.CIN, validade=br(HOJE - timedelta(days=1)))
    assert Sinalizacao.DOCUMENTO_VENCIDO in r.sinalizacoes
    r = validar(Subtipo.CIN, validade=br(HOJE))
    assert Sinalizacao.DOCUMENTO_VENCIDO not in r.sinalizacoes


def test_mrz_especime_icao() -> None:
    """Espécime TD1 do ICAO 9303: os quatro dígitos de controle conferem."""
    mrz = cin.ler_mrz(
        "I<UTOD231458907<<<<<<<<<<<<<<<\n"
        "7408122F1204159UTO<<<<<<<<<<<6\n"
        "ERIKSSON<<ANNA<MARIA<<<<<<<<<<"
    )
    assert mrz is not None and mrz.checagens_ok
    assert mrz.numero == "D23145890" and mrz.nascimento == "740812"
    assert mrz.nomes == ["ERIKSSON", "ANNA", "MARIA"]
    errada = cin.ler_mrz(
        "I<UTOD231458907<<<<<<<<<<<<<<<7408123F1204159UTO<<<<<<<<<<<6ERIKSSON<<ANNA<MARIA<<<<<<<<<<"
    )
    assert errada is not None and not errada.checagens_ok
    assert cin.ler_mrz("I<UTOD2314") is None


def _mrz_cin(numero_cpf: str, nascimento: str, validade: str, nome: str) -> str:
    l1 = "IDBRA" + numero_cpf[:9] + "<" + numero_cpf[9:] + cin.digito_mrz(numero_cpf)
    l1 = l1.ljust(30, "<")
    l2 = nascimento + cin.digito_mrz(nascimento) + "M" + validade + cin.digito_mrz(validade)
    l2 = (l2 + "BRA").ljust(29, "<")
    l2 += cin.digito_mrz(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29])
    l3 = nome.ljust(30, "<")
    return f"{l1}\n{l2}\n{l3}"


def test_mrz_da_cin_conferida_e_divergente() -> None:
    numero = cpf(9).replace(".", "").replace("-", "")
    mrz = _mrz_cin(numero, "900102", "340515", "SILVA<<JOAO<PEDR")
    lida = cin.ler_mrz(mrz)
    assert lida is not None and lida.checagens_ok and lida.numero == numero
    base: dict[str, Any] = {
        "cpf": numero,
        "data_nascimento": "02/01/1990",
        "validade": "15/05/2034",
        "nome": "JOAO PEDRO SILVA",
    }
    r = validar(Subtipo.CIN, mrz=mrz, **base)
    assert r.por_campo["mrz"] is V and Sinalizacao.MRZ_CONFERIDA in r.sinalizacoes
    r = validar(Subtipo.CIN, mrz=mrz, **{**base, "data_nascimento": "03/01/1990"})
    assert r.por_campo["mrz"] is S and Sinalizacao.MRZ_CONFERIDA not in r.sinalizacoes
    r = validar(Subtipo.CIN, mrz="TEXTO QUALQUER", **base)
    assert r.por_campo["mrz"] is NV  # ilegível ou fora do formato não pesa contra
    assert r.por_campo["cpf"] is V


# --- CNH -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("categoria", ["A", "B", "AB", "AE", "ACC"])
def test_categoria_valida(categoria: str) -> None:
    assert validar(Subtipo.CNH, categoria=categoria).por_campo["categoria"] is V


@pytest.mark.parametrize("categoria", ["BA", "CC", "F"])
def test_categoria_fora_da_lista(categoria: str) -> None:
    r = validar(Subtipo.CNH, categoria=categoria)
    assert r.por_campo["categoria"] in (INV, NV)


def test_registro_da_cnh() -> None:
    registro = com_dv(Verificador.CNH, 11, 3)
    assert validar(Subtipo.CNH, numero_registro=registro).por_campo["numero_registro"] is V
    errado = registro[:-1] + str((int(registro[-1]) + 5) % 10)
    assert validar(Subtipo.CNH, numero_registro=errado).por_campo["numero_registro"] is INV


def _cnh(emissao: date, anos: int, validade_anos: int, dias: int = 0) -> StatusValidacao:
    nascimento = somar_anos(emissao, -anos)
    r = validar(
        Subtipo.CNH,
        data_nascimento=br(nascimento),
        data_emissao=br(emissao),
        validade=br(somar_anos(emissao, validade_anos) + timedelta(days=dias)),
    )
    return r.por_campo["validade"]


@pytest.mark.parametrize(("anos", "teto"), [(49, 10), (50, 10), (69, 10), (70, 3)])
def test_teto_da_cnh_desde_12_04_2021(anos: int, teto: int) -> None:
    emissao = date(2021, 4, 12)
    assert _cnh(emissao, anos, teto) is V
    assert _cnh(emissao, anos, teto - 1) is V  # prazo menor é permitido
    assert _cnh(emissao, anos, teto, dias=32) is S


@pytest.mark.parametrize(("anos", "teto"), [(65, 5), (66, 3)])
def test_teto_da_cnh_antes_de_12_04_2021(anos: int, teto: int) -> None:
    emissao = date(2021, 4, 11)
    assert _cnh(emissao, anos, teto) is V
    assert _cnh(emissao, anos, teto, dias=32) is S


def test_cnh_49_anos_com_10_anos_antes_de_2021_e_suspeita() -> None:
    assert _cnh(date(2021, 4, 11), 49, 10) is S
    assert _cnh(date(2021, 4, 12), 49, 10) is V


def test_cnh_sem_emissao_nao_verifica_a_validade() -> None:
    r = validar(Subtipo.CNH, validade="01/01/2030", data_nascimento="01/01/1990")
    assert r.por_campo["validade"] is NV
    assert MotivoCampo.VALIDACAO_INDISPONIVEL in r.motivos["validade"]


@pytest.mark.parametrize(
    ("validade", "hoje", "vencida"),
    [
        (date(2026, 6, 4), date(2026, 11, 12), True),  # antes da janela
        (date(2026, 6, 5), date(2026, 11, 12), False),  # início da janela
        (date(2026, 12, 8), date(2026, 12, 9), False),  # fim da janela, último dia
        (date(2026, 12, 8), date(2026, 12, 10), True),  # prorrogação acabou
        (date(2026, 6, 5), date(2026, 12, 10), True),
        (date(2026, 12, 9), date(2026, 12, 10), True),  # fora da janela
        (date(2027, 1, 1), date(2026, 12, 10), False),  # não vencida
    ],
)
def test_prorrogacao_do_contran(validade: date, hoje: date, vencida: bool) -> None:
    r = validar(Subtipo.CNH, hoje=hoje, validade=br(validade))
    assert (Sinalizacao.DOCUMENTO_VENCIDO in r.sinalizacoes) is vencida


@pytest.mark.parametrize(("anos", "esperado"), [(17, S), (18, V)])
def test_primeira_habilitacao_aos_18(anos: int, esperado: StatusValidacao) -> None:
    nascimento = date(1990, 5, 10)
    primeira = somar_anos(nascimento, anos)
    r = validar(Subtipo.CNH, data_nascimento=br(nascimento), primeira_habilitacao=br(primeira))
    assert r.por_campo["primeira_habilitacao"] is esperado
    r = validar(
        Subtipo.CNH,
        data_nascimento=br(nascimento),
        primeira_habilitacao=br(somar_anos(nascimento, 20)),
        data_emissao=br(somar_anos(nascimento, 19)),
    )
    assert r.por_campo["primeira_habilitacao"] is S


# --- CTPS ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numero", "serie", "esperado_numero", "esperado_serie"),
    [
        ("1234567", "0012", V, V),
        ("12345", "001", V, V),
        ("1234", "001234", S, S),
    ],
)
def test_ctps_fisica_numero_e_serie(
    numero: str, serie: str, esperado_numero: StatusValidacao, esperado_serie: StatusValidacao
) -> None:
    r = validar(Subtipo.CTPS_FISICA, numero_ctps=numero, serie_ctps=serie, uf_ctps="SP")
    assert r.por_campo["numero_ctps"] is esperado_numero
    assert r.por_campo["serie_ctps"] is esperado_serie


@pytest.mark.parametrize(("admissao", "esperado"), [(date(2026, 7, 30), S), (date(2026, 7, 31), V)])
def test_cnpj_alfanumerico_a_partir_de_31_07_2026(
    admissao: date, esperado: StatusValidacao
) -> None:
    r = validar(
        Subtipo.CTPS_DIGITAL,
        hoje=date(2026, 12, 1),
        contrato_cnpj="12.ABC.345/01DE-35",
        contrato_admissao=br(admissao),
    )
    assert r.por_campo["contrato_cnpj"] is esperado


def test_cnpj_do_contrato_com_dv_falho_e_invalido() -> None:
    r = validar(Subtipo.CTPS_DIGITAL, contrato_cnpj="12.ABC.345/01DE-36")
    assert r.por_campo["contrato_cnpj"] is INV


def test_datas_do_contrato() -> None:
    r = validar(
        Subtipo.CTPS_FISICA,
        data_nascimento="01/01/1990",
        contrato_admissao="01/03/2010",
        contrato_saida="28/02/2010",
    )
    assert r.por_campo["contrato_saida"] is S
    r = validar(
        Subtipo.CTPS_FISICA,
        data_nascimento="01/01/1990",
        contrato_admissao="31/12/2003",  # 13 anos
    )
    assert r.por_campo["contrato_admissao"] is S
    r = validar(
        Subtipo.CTPS_FISICA,
        data_nascimento="01/01/1990",
        contrato_admissao="01/01/2004",  # 14 anos
        contrato_saida="01/01/2004",
    )
    assert r.por_campo["contrato_admissao"] is V and r.por_campo["contrato_saida"] is V


@pytest.mark.parametrize(
    ("salario", "admissao", "esperado"),
    [
        ("R$ 1.500,00", "01/07/1994", V),
        ("R$ 1.500,00", "30/06/1994", S),
        ("CR$ 300.000,00", "15/03/1994", V),
        ("Cr$ 5.000,00", "15/03/1994", S),  # cruzeiro (não o real) em 1994: fora da folga
        ("CR$ 5.000,00", "15/06/1985", V),  # caixa alta: pode ser o cruzeiro
        ("Cz$ 900,00", "01/03/1986", V),
        ("Cz$ 900,00", "01/01/1990", S),
        ("NCz$ 900,00", "01/03/1989", V),
        ("NCr$ 400,00", "01/01/1968", V),
        ("1.500,00", "01/01/2010", V),  # sem símbolo: sem regra aplicável
    ],
)
def test_moeda_do_salario(salario: str, admissao: str, esperado: StatusValidacao) -> None:
    r = validar(Subtipo.CTPS_FISICA, contrato_salario=salario, contrato_admissao=admissao)
    assert r.por_campo["contrato_salario"] is esperado


def test_moeda_na_transicao() -> None:
    fim_cz = date(1989, 1, 15)
    assert ctps.moeda_coerente({"Cz$"}, fim_cz + timedelta(days=180), 180)
    assert not ctps.moeda_coerente({"Cz$"}, fim_cz + timedelta(days=181), 180)


def test_cbo_com_e_sem_tabela() -> None:
    r = validar(Subtipo.CTPS_DIGITAL, contrato_cbo="411010")
    assert r.por_campo["contrato_cbo"] is NV  # tabela ainda ausente
    tabelas = Tabelas(orgaos={}, municipios=None, cbo=frozenset({"411010"}), versoes={})
    assert (
        validar(Subtipo.CTPS_DIGITAL, tabelas=tabelas, contrato_cbo="4110-10").por_campo[
            "contrato_cbo"
        ]
        is V
    )
    r = validar(Subtipo.CTPS_DIGITAL, tabelas=tabelas, contrato_cbo="999999")
    assert r.por_campo["contrato_cbo"] is INV
    assert r.motivos["contrato_cbo"] == [MotivoCampo.FORA_DA_TABELA]
    r = validar(Subtipo.CTPS_DIGITAL, tabelas=tabelas, contrato_cbo="41101")
    assert r.por_campo["contrato_cbo"] is INV


def test_tabelas_versionadas() -> None:
    tabelas = carregar()
    assert "SP" in tabelas.orgaos["SSP"] and len(tabelas.orgaos["SSP"]) == 27
    assert tabelas.versoes["orgaos_emissores"] == "2026-11-12"
    assert tabelas.municipios is None and tabelas.cbo is None
    assert {"ACC", "AE"} <= cnh.CATEGORIAS


def test_script_de_atualizacao_das_tabelas(tmp_path) -> None:
    from app.regras.tabelas import atualizar

    dados = [
        {
            "id": 3550308,
            "nome": "São Paulo",
            "microrregiao": {"mesorregiao": {"UF": {"sigla": "SP"}}},
        },
        {
            "id": 5300108,
            "nome": "Brasília",
            "microrregiao": None,
            "regiao-imediata": {"regiao-intermediaria": {"UF": {"sigla": "DF"}}},
        },
    ]
    assert atualizar.municipios_de_json(dados) == [
        ("DF", "Brasília", "5300108"),
        ("SP", "São Paulo", "3550308"),
    ]
    arquivo = tmp_path / "CBO2002 - Ocupacao.csv"
    arquivo.write_bytes("CODIGO;TITULO\n411010;Assistente administrativo\n".encode("latin-1"))
    assert atualizar.cbo_de_csv(arquivo) == [("411010", "Assistente administrativo")]
