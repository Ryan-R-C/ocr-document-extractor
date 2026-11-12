"""Ancoragem: o `texto_literal` do VLM precisa aparecer no OCR, perto do rótulo visto.

Regras (Fase 2, ver docs/decisoes.md):
- Comparação por "chave": maiúsculas, sem acento, só letras e dígitos (tolera espaços e
  pontuação). `?` no texto do VLM casa com qualquer caractere.
- Confusões do OCR (O/0, I/1, S/5… lista em configuração) valem só para o texto do OCR e
  conforme o tipo do campo. O valor do VLM nunca é alterado.
- VLM com caractere incompatível com o tipo (letra num CPF): PARCIAL, e o núcleo marca
  LEITURAS_DIVERGENTES. A busca no OCR continua, só para localizar o campo.
- Região: mesma linha à direita do rótulo ou linhas logo abaixo (configurável por subtipo).
- Resultado:
  - ANCORADO: valor igual (com as tolerâncias) dentro da região do rótulo;
  - PARCIAL: valor igual fora da região ou sem rótulo encontrado, ou diferença de um único
    caractere dentro da região num valor com pelo menos N caracteres;
  - NAO_ANCORADO: nada disso;
  - NAO_APLICAVEL: campo AUSENTE/ILEGIVEL (nada a ancorar) ou manuscrito (exige dupla
    leitura de modelos diferentes, não ancoragem).
- A evidência (ids e caixas das palavras do rótulo e do valor) vai para a auditoria.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum

from app.api.contrato import StatusLeitura
from app.config import RegiaoAncoragem, Settings
from app.providers.interfaces import Caixa, CampoLido, LeituraTexto, Palavra
from app.schemas.campos import DefinicaoCampo, Escrita
from app.schemas.normalizacao import TipoValor

IMPLEMENTADO = True
VERSAO_ANCORAGEM = "1.0"

_LIDOS = (StatusLeitura.LEGIVEL, StatusLeitura.PARCIAL)
_NUMERICOS = {TipoValor.CPF, TipoValor.DATA, TipoValor.DIGITOS}
_LETRAS = {TipoValor.TEXTO, TipoValor.UF, TipoValor.CATEGORIA_CNH}
_CORINGA = "?"


class StatusAncoragem(StrEnum):
    ANCORADO = "ANCORADO"
    PARCIAL = "PARCIAL"
    NAO_ANCORADO = "NAO_ANCORADO"
    NAO_APLICAVEL = "NAO_APLICAVEL"


@dataclass(frozen=True)
class ResultadoAncoragem:
    campo: str
    status: StatusAncoragem
    palavras_ids: list[int] = field(default_factory=list)
    implementado: bool = IMPLEMENTADO
    rotulo_ids: list[int] = field(default_factory=list)
    caixa_valor: Caixa | None = None
    caixa_rotulo: Caixa | None = None
    # exato | um_caractere | fora_da_regiao | sem_rotulo | vlm_incompativel | manuscrito |
    # nao_encontrado | sem_leitura
    detalhe: str = ""
    # O VLM devolveu caractere incompatível com o tipo do campo.
    vlm_incompativel: bool = False


@dataclass(frozen=True)
class ParametrosAncoragem:
    regiao: RegiaoAncoragem
    confusoes: tuple[tuple[str, str], ...]
    rotulo_similaridade_min: float
    parcial_tamanho_min: int
    palavras_max: int

    @classmethod
    def de(cls, settings: Settings, subtipo: str) -> ParametrosAncoragem:
        pares = tuple(
            (p[0].upper(), p[1])
            for p in settings.ancoragem_confusoes
            if len(p) == 2 and p[1].isdigit()
        )
        return cls(
            regiao=settings.regiao_ancoragem(subtipo),
            confusoes=pares,
            rotulo_similaridade_min=settings.ancoragem_rotulo_similaridade_min,
            parcial_tamanho_min=settings.ancoragem_parcial_tamanho_min,
            palavras_max=settings.ancoragem_palavras_max,
        )


# --- Texto --------------------------------------------------------------------------------------


def chave(texto: str, manter_coringa: bool = False) -> str:
    """Maiúsculas, sem acento, só letras e dígitos (e `?` no texto do VLM)."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto.upper()) if not unicodedata.combining(c)
    )
    permitido = r"[^0-9A-Z?]" if manter_coringa else r"[^0-9A-Z]"
    return re.sub(permitido, "", sem_acento)


def incompativel_com_tipo(tipo: TipoValor, texto_vlm: str) -> bool:
    """Caractere do VLM que o tipo do campo não admite (o valor nunca é corrigido)."""
    k = chave(texto_vlm, manter_coringa=True).replace(_CORINGA, "")
    if tipo in _NUMERICOS:
        return bool(re.search(r"[A-Z]", k))
    if tipo in (TipoValor.UF, TipoValor.CATEGORIA_CNH):
        return bool(re.search(r"\d", k))
    return False


class Comparador:
    """Igualdade caractere a caractere: VLM × OCR, com coringa e confusões por tipo."""

    def __init__(self, tipo: TipoValor, confusoes: Sequence[tuple[str, str]]) -> None:
        self.letra_para_digito = {letra: digito for letra, digito in confusoes}
        self.digito_para_letras: dict[str, set[str]] = {}
        for letra, digito in confusoes:
            self.digito_para_letras.setdefault(digito, set()).add(letra)
        self.tipo = tipo

    def igual(self, vlm: str, ocr: str) -> bool:
        if vlm in (_CORINGA, ocr):
            return True
        if self.tipo in _NUMERICOS:
            return self.letra_para_digito.get(ocr) == vlm
        if self.tipo in _LETRAS:
            return vlm in self.digito_para_letras.get(ocr, set())
        # Alfanumérico (RG, série da CTPS, CNPJ alfanumérico): os dois sentidos.
        return self.letra_para_digito.get(ocr) == vlm or vlm in self.digito_para_letras.get(
            ocr, set()
        )

    def diferencas(self, vlm: str, ocr: str, limite: int = 1) -> int:
        """Distância de edição com a igualdade tolerante; para em `limite + 1`."""
        if abs(len(vlm) - len(ocr)) > limite:
            return limite + 1
        anterior = list(range(len(ocr) + 1))
        for i, cv in enumerate(vlm, start=1):
            atual = [i] + [0] * len(ocr)
            for j, co in enumerate(ocr, start=1):
                custo = 0 if self.igual(cv, co) else 1
                atual[j] = min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + custo)
            if min(atual) > limite:
                return limite + 1
            anterior = atual
        return min(anterior[-1], limite + 1)


# --- Geometria ----------------------------------------------------------------------------------


def uniao(caixas: Sequence[Caixa]) -> Caixa:
    x0 = min(c.x for c in caixas)
    y0 = min(c.y for c in caixas)
    x1 = max(c.x + c.largura for c in caixas)
    y1 = max(c.y + c.altura for c in caixas)
    return Caixa(x0, y0, x1 - x0, y1 - y0)


def _mesma_linha(a: Caixa, b: Caixa) -> bool:
    topo = max(a.y, b.y)
    base = min(a.y + a.altura, b.y + b.altura)
    return base - topo >= 0.5 * min(a.altura, b.altura)


def na_regiao(rotulo: Caixa, valor: Caixa, regiao: RegiaoAncoragem) -> bool:
    h = max(1, rotulo.altura)
    fim_rotulo = rotulo.x + rotulo.largura
    if _mesma_linha(rotulo, valor):
        return (
            valor.x >= fim_rotulo - 0.5 * h
            and valor.x - fim_rotulo <= regiao.direita_max_alturas * h
        )
    abaixo = valor.y - (rotulo.y + rotulo.altura)
    if -0.5 * h <= abaixo <= regiao.abaixo_max_alturas * h:
        folga = regiao.abaixo_folga_alturas * h
        return valor.x <= fim_rotulo + folga and valor.x + valor.largura >= rotulo.x - folga
    return False


def distancia(rotulo: Caixa, valor: Caixa) -> float:
    return abs(valor.x - rotulo.x) + abs(valor.y - rotulo.y)


# --- Busca --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Trecho:
    """Palavras consecutivas do OCR na mesma linha."""

    palavras: tuple[Palavra, ...]

    @property
    def ids(self) -> list[int]:
        return [p.id for p in self.palavras]

    @property
    def caixa(self) -> Caixa:
        return uniao([p.caixa for p in self.palavras])

    @property
    def chave(self) -> str:
        return "".join(chave(p.texto) for p in self.palavras)


def trechos(palavras: Sequence[Palavra], tamanho_max: int) -> list[Trecho]:
    """Todas as sequências de até `tamanho_max` palavras consecutivas na mesma linha."""
    resultado: list[Trecho] = []
    for i in range(len(palavras)):
        atual = [palavras[i]]
        resultado.append(Trecho(tuple(atual)))
        for j in range(i + 1, min(len(palavras), i + tamanho_max)):
            anterior, proxima = atual[-1], palavras[j]
            if (
                not _mesma_linha(anterior.caixa, proxima.caixa)
                or proxima.caixa.x < anterior.caixa.x
            ):
                break
            atual.append(proxima)
            resultado.append(Trecho(tuple(atual)))
    return resultado


def encontrar_rotulos(
    candidatos: Sequence[Trecho], rotulos: Sequence[str], similaridade_min: float
) -> list[Trecho]:
    """Trechos do OCR parecidos com algum rótulo (o OCR erra acento e cedilha)."""
    achados: list[tuple[float, Trecho]] = []
    for rotulo in rotulos:
        alvo = chave(rotulo)
        if not alvo:
            continue
        for trecho in candidatos:
            k = trecho.chave
            if not k or abs(len(k) - len(alvo)) > max(2, len(alvo) // 3):
                continue
            razao = 1.0 if k == alvo else SequenceMatcher(None, alvo, k).ratio()
            if razao >= similaridade_min:
                achados.append((razao, trecho))
    achados.sort(key=lambda a: (-a[0], len(a[1].palavras)))
    vistos: set[tuple[int, ...]] = set()
    unicos: list[Trecho] = []
    for _, trecho in achados:
        ids = tuple(trecho.ids)
        if ids not in vistos:
            vistos.add(ids)
            unicos.append(trecho)
    return unicos


@dataclass(frozen=True)
class _Candidato:
    trecho: Trecho
    diferencas: int
    rotulo: Trecho | None
    na_regiao: bool

    def ordem(self) -> tuple[int, int, float]:
        # Exato na região > exato fora > um caractere na região; mais perto do rótulo antes.
        grupo = (
            0 if (self.diferencas == 0 and self.na_regiao) else (1 if self.diferencas == 0 else 2)
        )
        dist = distancia(self.rotulo.caixa, self.trecho.caixa) if self.rotulo else float("inf")
        return grupo, len(self.trecho.palavras), dist


def _rotulo_colado(trecho: Trecho, nomes_rotulo: Sequence[str]) -> tuple[str, str] | None:
    """Rótulo grudado no início do valor (`CPF:123…` lido como uma palavra só): devolve
    (chave do rótulo, chave do resto). Mesma palavra é evidência de posição."""
    if len(trecho.palavras) != 1:
        return None
    k = trecho.chave
    for nome in nomes_rotulo:
        rk = chave(nome)
        if rk and k.startswith(rk) and len(k) > len(rk):
            return rk, k[len(rk) :]
    return None


def localizar_valor(
    texto_vlm: str,
    tipo: TipoValor,
    candidatos: Sequence[Trecho],
    rotulos: Sequence[Trecho],
    parametros: ParametrosAncoragem,
    nomes_rotulo: Sequence[str] = (),
) -> tuple[_Candidato | None, bool]:
    """Melhor trecho do OCR para o texto do VLM. Devolve (candidato, vlm_incompativel)."""
    alvo = chave(texto_vlm, manter_coringa=True)
    if not alvo or not alvo.replace(_CORINGA, ""):
        return None, False
    incompativel = incompativel_com_tipo(tipo, texto_vlm)
    if incompativel:
        # Só para localizar: o caractere incompatível vira coringa na busca.
        padrao = r"[A-Z]" if tipo in _NUMERICOS else r"\d"
        alvo = re.sub(padrao, _CORINGA, alvo)
    comparador = Comparador(tipo, parametros.confusoes)
    aceita_um = len(alvo) >= parametros.parcial_tamanho_min
    melhores: list[_Candidato] = []
    for trecho in candidatos:
        k = trecho.chave
        colado = _rotulo_colado(trecho, nomes_rotulo) if abs(len(k) - len(alvo)) > 1 else None
        if colado is not None:
            k = colado[1]
        if abs(len(k) - len(alvo)) > 1:
            continue
        dif = comparador.diferencas(alvo, k, limite=1)
        if dif > 1 or (dif == 1 and not aceita_um):
            continue
        if colado is not None:
            melhores.append(_Candidato(trecho, dif, trecho, True))
            continue
        perto = [r for r in rotulos if na_regiao(r.caixa, trecho.caixa, parametros.regiao)]
        rotulo = min(perto, key=lambda r: distancia(r.caixa, trecho.caixa)) if perto else None
        if rotulo is None and rotulos:
            rotulo = min(rotulos, key=lambda r: distancia(r.caixa, trecho.caixa))
        melhores.append(_Candidato(trecho, dif, rotulo, bool(perto)))
    if not melhores:
        return None, incompativel
    return min(melhores, key=_Candidato.ordem), incompativel


def ancorar_campo(
    definicao: DefinicaoCampo,
    lido: CampoLido | None,
    leitura_texto: LeituraTexto,
    parametros: ParametrosAncoragem,
    candidatos: Sequence[Trecho] | None = None,
) -> ResultadoAncoragem:
    nome = definicao.nome
    if lido is None or lido.status not in _LIDOS or not lido.texto_literal.strip():
        return ResultadoAncoragem(nome, StatusAncoragem.NAO_APLICAVEL, detalhe="sem_leitura")
    if definicao.escrita is Escrita.MANUSCRITO:
        return ResultadoAncoragem(nome, StatusAncoragem.NAO_APLICAVEL, detalhe="manuscrito")

    if candidatos is None:
        candidatos = trechos(leitura_texto.palavras, parametros.palavras_max)
    nomes_rotulo = [r for r in (lido.rotulo_visto, *definicao.rotulos) if r.strip()]
    rotulos = encontrar_rotulos(candidatos, nomes_rotulo, parametros.rotulo_similaridade_min)
    candidato, incompativel = localizar_valor(
        lido.texto_literal, definicao.tipo_valor, candidatos, rotulos, parametros, nomes_rotulo
    )

    if candidato is None:
        return ResultadoAncoragem(
            nome,
            StatusAncoragem.PARCIAL if incompativel else StatusAncoragem.NAO_ANCORADO,
            rotulo_ids=rotulos[0].ids if rotulos else [],
            caixa_rotulo=rotulos[0].caixa if rotulos else None,
            detalhe="vlm_incompativel" if incompativel else "nao_encontrado",
            vlm_incompativel=incompativel,
        )

    if incompativel:
        status, detalhe = StatusAncoragem.PARCIAL, "vlm_incompativel"
    elif candidato.diferencas == 0 and candidato.na_regiao:
        status, detalhe = StatusAncoragem.ANCORADO, "exato"
    elif candidato.diferencas == 0:
        status = StatusAncoragem.PARCIAL
        detalhe = "fora_da_regiao" if rotulos else "sem_rotulo"
    elif candidato.na_regiao:
        status, detalhe = StatusAncoragem.PARCIAL, "um_caractere"
    else:
        # Um caractere de diferença e longe do rótulo: evidência fraca demais.
        status, detalhe = StatusAncoragem.NAO_ANCORADO, "um_caractere_fora_da_regiao"
    return ResultadoAncoragem(
        nome,
        status,
        palavras_ids=candidato.trecho.ids,
        rotulo_ids=candidato.rotulo.ids if candidato.rotulo else [],
        caixa_valor=candidato.trecho.caixa,
        caixa_rotulo=candidato.rotulo.caixa if candidato.rotulo else None,
        detalhe=detalhe,
        vlm_incompativel=incompativel,
    )


def ancorar(
    definicoes: Sequence[DefinicaoCampo],
    campos: dict[str, CampoLido],
    leitura_texto: LeituraTexto,
    parametros: ParametrosAncoragem,
) -> dict[str, ResultadoAncoragem]:
    candidatos = trechos(leitura_texto.palavras, parametros.palavras_max)
    return {
        d.nome: ancorar_campo(d, campos.get(d.nome), leitura_texto, parametros, candidatos)
        for d in definicoes
    }


def ocr_concorda(
    definicao: DefinicaoCampo,
    texto: str,
    rotulo_visto: str,
    leitura_texto: LeituraTexto,
    parametros: ParametrosAncoragem,
) -> ResultadoAncoragem:
    """O OCR confirma este texto (exato, dentro da região do rótulo)? Usado na votação da
    releitura, com o mesmo critério da ancoragem."""
    lido = CampoLido(StatusLeitura.LEGIVEL, rotulo_visto, texto)
    return ancorar_campo(definicao, lido, leitura_texto, parametros)
