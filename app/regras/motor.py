"""Motor de regras: validações determinísticas, só com o próprio documento.

STUB da Fase 0: nenhuma regra implementada. Todo campo com valor sai NAO_VERIFICAVEL com
motivo REGRA_NAO_IMPLEMENTADA. As regras reais (id, versão, vigência, fonte, testes nas
fronteiras) entram na Fase 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.api.contrato import MotivoCampo, Sinalizacao, StatusLeitura, StatusValidacao, Subtipo

IMPLEMENTADO = False
VERSAO_REGRAS = "0.0-stub"


@dataclass(frozen=True)
class EntradaRegra:
    status_leitura: StatusLeitura
    valor: str | None
    valor_normalizado: str | None


@dataclass(frozen=True)
class RegraAplicada:
    id: str
    versao: str
    campo: str | None
    resultado: StatusValidacao
    motivo: MotivoCampo | None = None


@dataclass(frozen=True)
class ResultadoValidacao:
    por_campo: dict[str, StatusValidacao]
    motivos: dict[str, list[MotivoCampo]]
    regras_aplicadas: list[RegraAplicada] = field(default_factory=list)
    sinalizacoes: list[Sinalizacao] = field(default_factory=list)
    versao: str = VERSAO_REGRAS


def validar(subtipo: Subtipo, campos: dict[str, EntradaRegra]) -> ResultadoValidacao:
    por_campo: dict[str, StatusValidacao] = {}
    motivos: dict[str, list[MotivoCampo]] = {}
    aplicadas: list[RegraAplicada] = []
    for nome, entrada in campos.items():
        por_campo[nome] = StatusValidacao.NAO_VERIFICAVEL
        if entrada.valor is not None:
            motivos[nome] = [MotivoCampo.REGRA_NAO_IMPLEMENTADA]
            aplicadas.append(
                RegraAplicada(
                    id="stub.nao_implementada",
                    versao=VERSAO_REGRAS,
                    campo=nome,
                    resultado=StatusValidacao.NAO_VERIFICAVEL,
                    motivo=MotivoCampo.REGRA_NAO_IMPLEMENTADA,
                )
            )
        else:
            motivos[nome] = []
    return ResultadoValidacao(por_campo=por_campo, motivos=motivos, regras_aplicadas=aplicadas)
