"""Decisão determinística: status de cada campo e do documento.

Invariante 1 do CLAUDE.md: nenhum campo crítico é aprovado sem leitura LEGIVEL do VLM,
ancoragem no OCR e validação sem falha. Campo manuscrito crítico exige duas leituras de
modelos diferentes concordando. Este módulo é código real desde a Fase 0; o que ainda é
stub (ancoragem, regras) chega aqui como "não ancorado"/"não verificável" e, portanto,
nunca resulta em APROVADO.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.api.contrato import (
    Campo,
    MotivoCampo,
    MotivoErro,
    MotivoErroDetalhado,
    Sinalizacao,
    StatusDocumento,
    StatusLeitura,
    StatusValidacao,
    Subtipo,
    TipoDocumento,
)
from app.core.ancoragem import ResultadoAncoragem, StatusAncoragem
from app.core.erros import FalhaExtracao
from app.core.releitura import ResultadoReleitura
from app.providers.interfaces import CampoLido, LeituraVisual, QualidadeCaptura
from app.regras.motor import EntradaRegra, ResultadoValidacao
from app.schemas.campos import SUBTIPOS_POR_TIPO, DefinicaoCampo, Escrita, campos_do_subtipo

_LIDOS = (StatusLeitura.LEGIVEL, StatusLeitura.PARCIAL)


@dataclass(frozen=True)
class CampoPreparado:
    definicao: DefinicaoCampo
    status_leitura: StatusLeitura
    valor: str | None
    valor_normalizado: str | None
    motivos: list[MotivoCampo] = field(default_factory=list)

    def entrada_regra(self) -> EntradaRegra:
        return EntradaRegra(self.status_leitura, self.valor, self.valor_normalizado)


@dataclass(frozen=True)
class Decisao:
    status: StatusDocumento
    campos: dict[str, Campo]
    sinalizacoes: list[Sinalizacao]
    # Por campo crítico: por que não está apto (vazio quando apto).
    bloqueios: dict[str, list[str]]


def verificar_classificacao(declarado: TipoDocumento, leitura: LeituraVisual) -> None:
    if leitura.subtipo is Subtipo.NAO_IDENTIFICADO:
        if leitura.tipo_documento_detectado not in (None, declarado):
            raise FalhaExtracao.de(MotivoErro.TIPO_DIVERGENTE)
        raise FalhaExtracao.de(MotivoErro.NENHUM_DOCUMENTO)
    divergente = (
        leitura.tipo_documento_detectado is not None
        and leitura.tipo_documento_detectado is not declarado
    ) or leitura.subtipo not in SUBTIPOS_POR_TIPO[declarado]
    if divergente:
        raise FalhaExtracao.de(MotivoErro.TIPO_DIVERGENTE)


def preparar_campos(subtipo: Subtipo, lidos: dict[str, CampoLido]) -> dict[str, CampoPreparado]:
    """Aplica as regras de leitura do código sobre a transcrição do VLM.

    - Campo do subtipo que o VLM não devolveu: ILEGIVEL (nunca inventa AUSENTE).
    - Campo que o VLM devolveu fora do subtipo: descartado.
    - LEGIVEL com `?`: rebaixado para PARCIAL.
    - LEGIVEL/PARCIAL sem texto: ILEGIVEL.
    - AUSENTE/ILEGIVEL: valor sempre null, mesmo que o modelo tenha escrito algo.
    - Normalização só para LEGIVEL.
    """
    preparados: dict[str, CampoPreparado] = {}
    for definicao in campos_do_subtipo(subtipo):
        lido = lidos.get(definicao.nome)
        motivos: list[MotivoCampo] = []
        if lido is None:
            status, texto = StatusLeitura.ILEGIVEL, ""
        else:
            status, texto = lido.status, lido.texto_literal.strip()
        if status in _LIDOS and not texto:
            status = StatusLeitura.ILEGIVEL
        if status is StatusLeitura.LEGIVEL and "?" in texto:
            status = StatusLeitura.PARCIAL
        if status in _LIDOS and "?" in texto:
            motivos.append(MotivoCampo.CARACTERE_DUVIDOSO)
        valor = texto if status in _LIDOS else None
        normalizado = definicao.normalizar(valor) if status is StatusLeitura.LEGIVEL else None
        if status is StatusLeitura.LEGIVEL and normalizado is None:
            motivos.append(MotivoCampo.FORMATO_INVALIDO)
        preparados[definicao.nome] = CampoPreparado(definicao, status, valor, normalizado, motivos)
    return preparados


def _falha_sem_criticos(preparados: dict[str, CampoPreparado]) -> FalhaExtracao | None:
    criticos = [p for p in preparados.values() if p.definicao.critico]
    if not criticos or any(p.status_leitura in _LIDOS for p in criticos):
        return None
    if all(p.status_leitura is StatusLeitura.AUSENTE for p in criticos):
        return FalhaExtracao.de(MotivoErro.LADO_SEM_DADOS)
    return FalhaExtracao(
        [
            MotivoErroDetalhado(codigo=MotivoErro.CAMPO_CRITICO_ILEGIVEL, campo=p.definicao.nome)
            for p in criticos
            if p.status_leitura is StatusLeitura.ILEGIVEL
        ]
    )


def decidir(
    leitura: LeituraVisual,
    preparados: dict[str, CampoPreparado],
    ancoragens: dict[str, ResultadoAncoragem],
    releituras: dict[str, ResultadoReleitura],
    validacao: ResultadoValidacao,
    aprovar_nao_verificavel: bool,
) -> Decisao:
    falha = _falha_sem_criticos(preparados)
    if falha is not None:
        raise falha

    campos: dict[str, Campo] = {}
    bloqueios: dict[str, list[str]] = {}
    validos = {StatusValidacao.VALIDO}
    if aprovar_nao_verificavel:
        validos.add(StatusValidacao.NAO_VERIFICAVEL)

    for nome, prep in preparados.items():
        motivos = list(prep.motivos)
        ancoragem = ancoragens.get(nome)
        status_anc = ancoragem.status if ancoragem else StatusAncoragem.NAO_ANCORADO
        if prep.status_leitura in _LIDOS:
            if status_anc is StatusAncoragem.NAO_ANCORADO:
                motivos.append(MotivoCampo.NAO_ANCORADO)
            elif status_anc is StatusAncoragem.PARCIAL:
                motivos.append(MotivoCampo.ANCORAGEM_PARCIAL)
        releitura = releituras.get(nome)
        if releitura and releitura.executada and releitura.concordancia is None:
            motivos.append(MotivoCampo.LEITURAS_DIVERGENTES)
        validacao_campo = validacao.por_campo.get(nome, StatusValidacao.NAO_VERIFICAVEL)
        motivos.extend(validacao.motivos.get(nome, []))

        dupla_leitura = bool(releitura and releitura.executada and releitura.concordancia)
        manuscrito = prep.definicao.escrita is Escrita.MANUSCRITO
        if prep.definicao.critico and manuscrito and not dupla_leitura:
            motivos.append(MotivoCampo.MANUSCRITO_SEM_DUPLA_LEITURA)

        if prep.definicao.critico:
            razoes: list[str] = []
            if prep.status_leitura is not StatusLeitura.LEGIVEL:
                razoes.append(f"leitura_{prep.status_leitura.value.lower()}")
            if prep.valor_normalizado is None:
                razoes.append("sem_valor_normalizado")
            if status_anc is not StatusAncoragem.ANCORADO:
                razoes.append(f"ancoragem_{status_anc.value.lower()}")
            if validacao_campo not in validos:
                razoes.append(f"validacao_{validacao_campo.value.lower()}")
            if manuscrito and not dupla_leitura:
                razoes.append("manuscrito_sem_dupla_leitura")
            bloqueios[nome] = razoes

        campos[nome] = Campo(
            valor=prep.valor,
            valor_normalizado=prep.valor_normalizado,
            status_leitura=prep.status_leitura,
            status_validacao=validacao_campo,
            motivos=list(dict.fromkeys(motivos)),
        )

    aprovado = bool(bloqueios) and all(not razoes for razoes in bloqueios.values())
    return Decisao(
        status=StatusDocumento.APROVADO if aprovado else StatusDocumento.REVISAO,
        campos=campos,
        sinalizacoes=_sinalizacoes(leitura, validacao),
        bloqueios=bloqueios,
    )


def _sinalizacoes(leitura: LeituraVisual, validacao: ResultadoValidacao) -> list[Sinalizacao]:
    sinais: list[Sinalizacao] = []
    if leitura.qualidade_captura is QualidadeCaptura.COPIA_REPROGRAFICA:
        sinais.append(Sinalizacao.COPIA_REPROGRAFICA)
    if leitura.qualidade_captura is QualidadeCaptura.PRINT_DE_TELA:
        sinais.append(Sinalizacao.PRINT_DE_TELA)
    if leitura.subtipo is Subtipo.RG_ANTIGO:
        sinais.append(Sinalizacao.RG_ANTIGO)
    sinais.extend(validacao.sinalizacoes)
    return list(dict.fromkeys(sinais))
