"""Métricas de qualidade da imagem em CPU: conteúdo, nitidez, reflexo e corte.

Os limiares vêm da configuração. Os valores padrão são provisórios e conservadores (só
reprovam casos extremos); a calibração final é feita na partição de ajuste do gabarito.
Reprovar é 422, então errar para o lado de deixar passar só custa uma revisão.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from app.api.contrato import MotivoErro, MotivoErroDetalhado
from app.config import Settings
from app.preprocess.documento import DocumentoLocalizado

IMPLEMENTADO = True


@dataclass(frozen=True)
class AvaliacaoQualidade:
    conteudo: float | None = None
    nitidez: float | None = None
    reflexo: float | None = None
    corte: float | None = None
    documento_encontrado: bool | None = None
    reprovacoes: list[MotivoErroDetalhado] = field(default_factory=list)
    implementado: bool = IMPLEMENTADO


def _cinza_padronizado(imagem: Image.Image, largura: int = 1000) -> NDArray[np.uint8]:
    """Escala fixa para que as métricas não dependam da resolução de entrada."""
    cinza = imagem.convert("L")
    if cinza.width != largura:
        altura = max(1, round(cinza.height * largura / cinza.width))
        cinza = cinza.resize((largura, altura), Image.Resampling.BILINEAR)
    return np.asarray(cinza, dtype=np.uint8)


def medir_nitidez(cinza: NDArray[np.uint8]) -> float:
    """Variância do Laplaciano nas regiões com conteúdo (fundo liso não conta)."""
    laplaciano = cv2.Laplacian(cinza, cv2.CV_64F)
    bordas = np.abs(laplaciano) > 4
    if bordas.mean() < 0.002:
        return float(laplaciano.var())
    return float(laplaciano[bordas].var())


def medir_reflexo(cinza: NDArray[np.uint8]) -> float:
    """Maior mancha estourada (≥ 250) que pareça brilho especular, como fração da imagem.

    Fundo branco (papel, print) não conta. Brilho é uma mancha compacta e sem texto dentro
    (área cheia ≈ área do contorno preenchido), menor que metade da imagem e cercada por
    pixels bem mais escuros. Fundo branco tem texto vazando (buracos) ou ocupa quase tudo.
    """
    estourado = (cinza >= 250).astype(np.uint8)
    quantos, rotulos, estatisticas, _ = cv2.connectedComponentsWithStats(estourado, connectivity=8)
    total = cinza.size
    maior = 0.0
    nucleo = np.ones((15, 15), np.uint8)
    for indice in range(1, quantos):
        area = int(estatisticas[indice, cv2.CC_STAT_AREA])
        if area < total * 0.002 or area > total * 0.5:
            continue
        mancha = (rotulos == indice).astype(np.uint8)
        contornos, _ = cv2.findContours(mancha, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        preenchida = np.zeros_like(mancha)
        cv2.drawContours(preenchida, contornos, -1, (1,), thickness=cv2.FILLED)
        if area / max(1, int(preenchida.sum())) < 0.95:
            continue  # tem buracos (texto): é fundo claro, não brilho
        anel = cv2.dilate(mancha, nucleo) - mancha
        if anel.sum() == 0:
            continue
        if float(cinza[anel.astype(bool)].mean()) < 215:
            maior = max(maior, area / total)
    return maior


def avaliar(
    imagem: Image.Image, documento: DocumentoLocalizado, settings: Settings
) -> AvaliacaoQualidade:
    cinza = _cinza_padronizado(documento.imagem)
    conteudo = float(cinza.std())
    nitidez = medir_nitidez(cinza)
    reflexo = medir_reflexo(cinza)
    corte = documento.perimetro_na_borda

    reprovacoes: list[MotivoErroDetalhado] = []
    if conteudo < settings.qualidade_conteudo_min:
        reprovacoes.append(MotivoErroDetalhado(codigo=MotivoErro.NENHUM_DOCUMENTO))
    else:
        if nitidez < settings.qualidade_nitidez_min:
            reprovacoes.append(MotivoErroDetalhado(codigo=MotivoErro.IMAGEM_DESFOCADA))
        if reflexo > settings.qualidade_reflexo_max:
            reprovacoes.append(MotivoErroDetalhado(codigo=MotivoErro.REFLEXO_EXCESSIVO))
        if corte > settings.qualidade_corte_max:
            reprovacoes.append(MotivoErroDetalhado(codigo=MotivoErro.DOCUMENTO_CORTADO))
    return AvaliacaoQualidade(
        conteudo=conteudo,
        nitidez=nitidez,
        reflexo=reflexo,
        corte=corte,
        documento_encontrado=documento.encontrado,
        reprovacoes=reprovacoes,
    )
