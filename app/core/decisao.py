"""Decisão determinística: status de cada campo e do documento.

Invariante 1 do CLAUDE.md: nenhum campo crítico é aprovado sem leitura LEGIVEL do VLM,
ancoragem no OCR e validação sem falha. Campo manuscrito crítico exige duas leituras de
modelos diferentes concordando (no lugar da ancoragem, que o OCR não faz em manuscrito).
Este módulo é código real desde a Fase 0. O motor de regras ainda é stub (Fase 3) e chega
aqui como "não verificável": sem ele, nada resulta em APROVADO.
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


def aplicar_releituras(
    preparados: dict[str, CampoPreparado], releituras: dict[str, ResultadoReleitura]
) -> dict[str, CampoPreparado]:
    """Campo com concordância que inclui o OCR (ou dupla leitura, no manuscrito) passa a
    valer o valor da concordância, normalizado de novo. O resto fica como estava."""
    finais = dict(preparados)
    for nome, r in releituras.items():
        prep = preparados.get(nome)
        if prep is None or r.valor_final is None:
            continue
        if not (r.ocr_concorda or r.dupla_leitura):
            continue
        normalizado = prep.definicao.normalizar(r.valor_final)
        if normalizado is None:
            continue
        finais[nome] = CampoPreparado(
            prep.definicao, StatusLeitura.LEGIVEL, r.valor_final, normalizado
        )
    return finais


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
    sinalizacoes_captura: list[Sinalizacao] | None = None,
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
        releitura = releituras.get(nome)
        manuscrito = prep.definicao.escrita is Escrita.MANUSCRITO
        status_anc = ancoragem.status if ancoragem else StatusAncoragem.NAO_ANCORADO
        if releitura is not None and releitura.ocr_concorda:
            # O OCR confirma o valor que saiu da votação da releitura.
            status_anc = StatusAncoragem.ANCORADO
        if prep.status_leitura in _LIDOS and not manuscrito:
            if status_anc is StatusAncoragem.NAO_ANCORADO:
                motivos.append(MotivoCampo.NAO_ANCORADO)
            elif status_anc is StatusAncoragem.PARCIAL:
                motivos.append(MotivoCampo.ANCORAGEM_PARCIAL)
        vlm_incompativel = bool(ancoragem and ancoragem.vlm_incompativel)
        divergentes = bool(releitura and releitura.executada and releitura.concordancia is None)
        if (vlm_incompativel and not (releitura and releitura.ocr_concorda)) or divergentes:
            motivos.append(MotivoCampo.LEITURAS_DIVERGENTES)
        validacao_campo = validacao.por_campo.get(nome, StatusValidacao.NAO_VERIFICAVEL)
        motivos.extend(validacao.motivos.get(nome, []))

        dupla_leitura = bool(releitura and releitura.dupla_leitura)
        if prep.definicao.critico and manuscrito and not dupla_leitura:
            motivos.append(MotivoCampo.MANUSCRITO_SEM_DUPLA_LEITURA)

        if prep.definicao.critico:
            razoes: list[str] = []
            if prep.status_leitura is not StatusLeitura.LEGIVEL:
                razoes.append(f"leitura_{prep.status_leitura.value.lower()}")
            if prep.valor_normalizado is None:
                razoes.append("sem_valor_normalizado")
            if not manuscrito and status_anc is not StatusAncoragem.ANCORADO:
                razoes.append(f"ancoragem_{status_anc.value.lower()}")
            if validacao_campo not in validos:
                razoes.append(f"validacao_{validacao_campo.value.lower()}")
            if MotivoCampo.REGRA_NAO_IMPLEMENTADA in validacao.motivos.get(nome, []):
                # Regra ausente nunca conta como validação, nem com NAO_VERIFICAVEL liberado.
                razoes.append("regra_nao_implementada")
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
        sinalizacoes=_sinalizacoes(leitura, validacao, sinalizacoes_captura),
        bloqueios=bloqueios,
    )


def sinalizacoes_de_captura(
    leitura: LeituraVisual, copia_heuristica: bool, print_heuristica: bool, fonte: str
) -> list[Sinalizacao]:
    """Combina a heurística em CPU com a classificação do VLM conforme a configuração:
    `vlm`, `heuristica`, `qualquer` (uma das duas) ou `ambas` (as duas concordam)."""
    vlm_copia = leitura.qualidade_captura is QualidadeCaptura.COPIA_REPROGRAFICA
    vlm_print = leitura.qualidade_captura is QualidadeCaptura.PRINT_DE_TELA

    def combinar(vlm: bool, heuristica: bool) -> bool:
        return {
            "vlm": vlm,
            "heuristica": heuristica,
            "qualquer": vlm or heuristica,
            "ambas": vlm and heuristica,
        }[fonte]

    sinais = []
    if combinar(vlm_copia, copia_heuristica):
        sinais.append(Sinalizacao.COPIA_REPROGRAFICA)
    if combinar(vlm_print, print_heuristica):
        sinais.append(Sinalizacao.PRINT_DE_TELA)
    return sinais


def _sinalizacoes(
    leitura: LeituraVisual,
    validacao: ResultadoValidacao,
    captura: list[Sinalizacao] | None,
) -> list[Sinalizacao]:
    """Sinalizações de política: informam e nunca alteram o status."""
    if captura is None:
        captura = sinalizacoes_de_captura(leitura, False, False, "vlm")
    sinais: list[Sinalizacao] = list(captura)
    if leitura.subtipo is Subtipo.RG_ANTIGO:
        sinais.append(Sinalizacao.RG_ANTIGO)
    sinais.extend(validacao.sinalizacoes)
    return list(dict.fromkeys(sinais))
