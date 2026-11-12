"""Motor de regras: validações determinísticas e versionadas, só com o próprio documento.

- Cada regra roda se estiver vigente na sua data de referência. Regra com vigência pela
  data do documento e sem essa data lida devolve NAO_VERIFICAVEL nos seus campos.
- Agregação por campo: INVALIDO > SUSPEITO > NAO_VERIFICAVEL > VALIDO.
- Campo com valor normalizado e nenhuma regra aplicável: VALIDO, registrado na auditoria
  como `sem_regra` (decisão 99). Campo sem valor normalizado: NAO_VERIFICAVEL.
- Sinalizações de política informam e nunca mudam o status do campo.
- `VERSAO_REGRAS` identifica o conjunto (ids e versões das regras e versões das tabelas).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from app.api.contrato import UF, MotivoCampo, Sinalizacao, StatusValidacao, Subtipo
from app.regras import tabelas as mod_tabelas
from app.regras.base import Avaliacao, Contexto, EntradaRegra, Parametros, Regra
from app.regras.conjuntos import REGRAS
from app.schemas.campos import campos_do_subtipo

__all__ = [
    "IMPLEMENTADO",
    "VERSAO_REGRAS",
    "EntradaRegra",
    "Parametros",
    "RegraAplicada",
    "ResultadoValidacao",
    "validar",
]

IMPLEMENTADO = True
VERSAO_CONJUNTO = "1.0"

GRAVIDADE = {
    StatusValidacao.VALIDO: 0,
    StatusValidacao.NAO_VERIFICAVEL: 1,
    StatusValidacao.SUSPEITO: 2,
    StatusValidacao.INVALIDO: 3,
}


def _versao(regras: Sequence[Regra], versao_tabelas: str) -> str:
    assinatura = "|".join(sorted(f"{r.id}@{r.versao}" for r in regras)) + "|" + versao_tabelas
    return f"{VERSAO_CONJUNTO}-{hashlib.sha256(assinatura.encode()).hexdigest()[:8]}"


VERSAO_REGRAS = _versao(REGRAS, mod_tabelas.padrao().versao)


@dataclass(frozen=True)
class RegraAplicada:
    id: str
    versao: str
    campo: str | None
    resultado: StatusValidacao
    motivo: MotivoCampo | None = None
    detalhe: str = ""
    sinalizacao: Sinalizacao | None = None


@dataclass(frozen=True)
class ResultadoValidacao:
    por_campo: dict[str, StatusValidacao]
    motivos: dict[str, list[MotivoCampo]]
    regras_aplicadas: list[RegraAplicada] = field(default_factory=list)
    sinalizacoes: list[Sinalizacao] = field(default_factory=list)
    versao: str = VERSAO_REGRAS


def _avaliar(regra: Regra, ctx: Contexto) -> list[Avaliacao]:
    if regra.com_vigencia:
        referencia = regra.data_referencia(ctx)
        if referencia is None:
            # Sem a data que define a vigência, a regra não sabe qual prazo vale.
            return [
                Avaliacao(
                    campo,
                    StatusValidacao.NAO_VERIFICAVEL,
                    MotivoCampo.VALIDACAO_INDISPONIVEL,
                    f"sem_{regra.campo_data_referencia}",
                )
                for campo in regra.campos
                if ctx.norm(campo) is not None
            ]
        if not regra.vigente_em(referencia):
            return []
    return list(regra.funcao(ctx))


def validar(
    subtipo: Subtipo,
    campos: Mapping[str, EntradaRegra],
    *,
    data_processamento: date,
    parametros: Parametros | None = None,
    tabelas: mod_tabelas.Tabelas | None = None,
    uf_leitura: UF | None = None,
    regras: Sequence[Regra] = REGRAS,
) -> ResultadoValidacao:
    tabelas = tabelas or mod_tabelas.padrao()
    ctx = Contexto(
        subtipo=subtipo,
        definicoes=campos_do_subtipo(subtipo),
        campos=campos,
        data_processamento=data_processamento,
        parametros=parametros or Parametros(),
        tabelas=tabelas,
        uf_leitura=uf_leitura,
    )
    aplicadas: list[RegraAplicada] = []
    sinalizacoes: list[Sinalizacao] = []
    por_campo_avaliacoes: dict[str, list[Avaliacao]] = {}
    for regra in regras:
        if subtipo not in regra.subtipos:
            continue
        for av in _avaliar(regra, ctx):
            if av.campo is not None and av.campo not in campos:
                continue  # campo que o subtipo não tem
            aplicadas.append(
                RegraAplicada(
                    regra.id,
                    regra.versao,
                    av.campo,
                    av.status,
                    av.motivo,
                    av.detalhe,
                    av.sinalizacao,
                )
            )
            if av.sinalizacao is not None:
                sinalizacoes.append(av.sinalizacao)
            if av.campo is not None:
                por_campo_avaliacoes.setdefault(av.campo, []).append(av)

    por_campo: dict[str, StatusValidacao] = {}
    motivos: dict[str, list[MotivoCampo]] = {}
    for nome in campos:
        avaliacoes = por_campo_avaliacoes.get(nome, [])
        if ctx.norm(nome) is None:
            por_campo[nome] = StatusValidacao.NAO_VERIFICAVEL
            motivos[nome] = []
            continue
        if not avaliacoes:
            por_campo[nome] = StatusValidacao.VALIDO
            motivos[nome] = []
            aplicadas.append(
                RegraAplicada("sem_regra", VERSAO_CONJUNTO, nome, StatusValidacao.VALIDO)
            )
            continue
        pior = max(avaliacoes, key=lambda a: GRAVIDADE[a.status]).status
        por_campo[nome] = pior
        motivos[nome] = list(
            dict.fromkeys(
                a.motivo
                for a in avaliacoes
                if a.motivo is not None and a.status is not StatusValidacao.VALIDO
            )
        )

    return ResultadoValidacao(
        por_campo=por_campo,
        motivos=motivos,
        regras_aplicadas=aplicadas,
        sinalizacoes=list(dict.fromkeys(sinalizacoes)),
        versao=_versao(regras, tabelas.versao),
    )
