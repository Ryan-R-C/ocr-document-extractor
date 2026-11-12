"""Releitura dirigida: o campo crítico com ancoragem falha (ou dígito verificador falho, ou
manuscrito) é recortado, ampliado e relido pelo modelo de escalada, com o schema de um único
campo. O pedido nunca leva o valor esperado nem as leituras anteriores (invariante 5).

Votação entre três leituras (VLM principal, OCR e releitura), depois da normalização:
- duas concordando dão o valor da concordância;
- para o campo poder ser aprovado, o OCR precisa estar entre as que concordam;
- VLM principal + releitura sem o OCR: o campo fica em revisão (decisão em aberto);
- manuscrito: VLM principal e releitura, de modelos diferentes, precisam concordar
  (o OCR não lê manuscrito).

No máximo uma releitura por campo: as regras rodam de novo depois, sem novo ciclo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from app.api.contrato import StatusLeitura
from app.core.ancoragem import (
    ParametrosAncoragem,
    ResultadoAncoragem,
    StatusAncoragem,
    ocr_concorda,
)
from app.core.digitos import ResultadoDigitos
from app.providers.interfaces import ErroProvider, LeituraTexto, Releitura
from app.schemas.campos import DefinicaoCampo, Escrita

IMPLEMENTADO = True

VLM, OCR, RELEITURA = "vlm", "ocr", "releitura"


class Disparo(StrEnum):
    NAO_ANCORADO = "NAO_ANCORADO"
    ANCORAGEM_PARCIAL = "ANCORAGEM_PARCIAL"
    DIGITO_VERIFICADOR = "DIGITO_VERIFICADOR"
    LEITURA_PARCIAL = "LEITURA_PARCIAL"  # o VLM marcou caractere duvidoso (`?`)
    MANUSCRITO = "MANUSCRITO"


@dataclass(frozen=True)
class ResultadoReleitura:
    campo: str
    executada: bool
    releitura: Releitura | None = None
    # "3_DE_3", "2_DE_3" ou "2_DE_2" (manuscrito); None quando não houve concordância.
    concordancia: str | None = None
    motivo_nao_executada: str | None = None
    disparo: Disparo | None = None
    leituras_concordantes: tuple[str, ...] = ()
    # Texto literal da leitura vencedora (o que vai na resposta) e se o OCR o confirma.
    valor_final: str | None = None
    ocr_concorda: bool = False
    modelos_diferentes: bool = False
    ancoragem_final: ResultadoAncoragem | None = None

    @property
    def dupla_leitura(self) -> bool:
        """VLM principal e releitura de modelos diferentes concordando (manuscrito)."""
        return (
            self.executada
            and self.modelos_diferentes
            and VLM in self.leituras_concordantes
            and RELEITURA in self.leituras_concordantes
        )


@dataclass(frozen=True)
class CampoParaReler:
    definicao: DefinicaoCampo
    status_leitura: StatusLeitura
    texto_vlm: str
    rotulo_visto: str


def planejar(
    campos: Mapping[str, CampoParaReler],
    ancoragens: Mapping[str, ResultadoAncoragem],
    digitos: Mapping[str, ResultadoDigitos],
) -> dict[str, Disparo]:
    """Campos críticos lidos (LEGIVEL ou PARCIAL) que precisam de releitura, e por quê."""
    plano: dict[str, Disparo] = {}
    for nome, campo in campos.items():
        if not campo.definicao.critico or not campo.texto_vlm:
            continue
        if campo.status_leitura not in (StatusLeitura.LEGIVEL, StatusLeitura.PARCIAL):
            continue
        anc = ancoragens.get(nome)
        status = anc.status if anc else StatusAncoragem.NAO_ANCORADO
        if campo.definicao.escrita is Escrita.MANUSCRITO:
            plano[nome] = Disparo.MANUSCRITO
        elif status is StatusAncoragem.NAO_ANCORADO:
            plano[nome] = Disparo.NAO_ANCORADO
        elif status is StatusAncoragem.PARCIAL:
            plano[nome] = Disparo.ANCORAGEM_PARCIAL
        elif campo.status_leitura is StatusLeitura.PARCIAL:
            plano[nome] = Disparo.LEITURA_PARCIAL
        elif digitos.get(nome) is ResultadoDigitos.INVALIDO:
            plano[nome] = Disparo.DIGITO_VERIFICADOR
    return plano


def _normalizado(definicao: DefinicaoCampo, status: StatusLeitura, texto: str) -> str | None:
    if status is not StatusLeitura.LEGIVEL:
        return None
    return definicao.normalizar(texto)


def votar(
    campo: CampoParaReler,
    disparo: Disparo,
    ancoragem: ResultadoAncoragem | None,
    releitura: Releitura,
    leitura_texto: LeituraTexto,
    parametros: ParametrosAncoragem,
    modelo_principal: str,
) -> ResultadoReleitura:
    """Duas de três leituras concordando, depois da normalização (código, não modelo)."""
    defn = campo.definicao
    nome = defn.nome
    rel = releitura.campo
    v = _normalizado(defn, campo.status_leitura, campo.texto_vlm)
    r = _normalizado(defn, rel.status, rel.texto_literal.strip())
    diferentes = releitura.metricas.modelo != modelo_principal
    base = ResultadoReleitura(
        nome, executada=True, releitura=releitura, disparo=disparo, modelos_diferentes=diferentes
    )

    if defn.escrita is Escrita.MANUSCRITO:
        if v is not None and v == r and diferentes:
            return replace(
                base,
                concordancia="2_DE_2",
                leituras_concordantes=(VLM, RELEITURA),
                valor_final=campo.texto_vlm,
            )
        return base

    ocr_v = ancoragem is not None and ancoragem.status is StatusAncoragem.ANCORADO
    anc_r = (
        ocr_concorda(
            defn,
            rel.texto_literal,
            rel.rotulo_visto or campo.rotulo_visto,
            leitura_texto,
            parametros,
        )
        if r is not None
        else None
    )
    ocr_r = anc_r is not None and anc_r.status is StatusAncoragem.ANCORADO

    if v is not None and v == r:
        if ocr_v or ocr_r:
            return replace(
                base,
                concordancia="3_DE_3",
                leituras_concordantes=(VLM, OCR, RELEITURA),
                valor_final=campo.texto_vlm,
                ocr_concorda=True,
                ancoragem_final=ancoragem if ocr_v else anc_r,
            )
        return replace(
            base,
            concordancia="2_DE_3",
            leituras_concordantes=(VLM, RELEITURA),
            valor_final=campo.texto_vlm,
        )
    if r is not None and ocr_r:
        return replace(
            base,
            concordancia="2_DE_3",
            leituras_concordantes=(OCR, RELEITURA),
            valor_final=rel.texto_literal.strip(),
            ocr_concorda=True,
            ancoragem_final=anc_r,
        )
    if v is not None and ocr_v:
        return replace(
            base,
            concordancia="2_DE_3",
            leituras_concordantes=(VLM, OCR),
            valor_final=campo.texto_vlm,
            ocr_concorda=True,
            ancoragem_final=ancoragem,
        )
    return base


async def executar(
    plano: Mapping[str, Disparo],
    campos: Mapping[str, CampoParaReler],
    ancoragens: Mapping[str, ResultadoAncoragem],
    tem_caixa: Callable[[str], bool],
    ler: Callable[[str], Awaitable[Releitura]],
    leitura_texto: LeituraTexto,
    parametros: ParametrosAncoragem,
    modelo_principal: str,
) -> dict[str, ResultadoReleitura]:
    """Uma releitura por campo, em paralelo. Falha do provider numa releitura não derruba o
    documento: o campo fica sem releitura (e, portanto, em revisão)."""

    async def um(nome: str, disparo: Disparo) -> ResultadoReleitura:
        if not tem_caixa(nome):
            return ResultadoReleitura(
                nome, executada=False, disparo=disparo, motivo_nao_executada="sem_caixa"
            )
        try:
            releitura = await ler(nome)
        except ErroProvider as erro:
            return ResultadoReleitura(
                nome,
                executada=False,
                disparo=disparo,
                motivo_nao_executada=f"erro_provider:{type(erro).__name__}",
            )
        return votar(
            campos[nome],
            disparo,
            ancoragens.get(nome),
            releitura,
            leitura_texto,
            parametros,
            modelo_principal,
        )

    nomes = sorted(plano)
    resultados = await asyncio.gather(*(um(n, plano[n]) for n in nomes))
    return dict(zip(nomes, resultados, strict=True))
