"""Localização do documento na foto, recorte do fundo, correção de perspectiva e rotação.

Tudo em CPU com OpenCV. Se nenhum quadrilátero plausível aparece (documento ocupando a
imagem toda, scan, print), a imagem inteira é usada: não localizar não reprova nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from app.config import Settings

Pontos = list[tuple[int, int]]


@dataclass(frozen=True)
class DocumentoLocalizado:
    imagem: Image.Image
    encontrado: bool
    # Quadrilátero na imagem de entrada (sentido horário a partir do canto superior esquerdo).
    contorno: Pontos | None
    # Fração da área da imagem ocupada pelo quadrilátero (1.0 quando não encontrado).
    area_relativa: float
    # Fração do perímetro do quadrilátero colada na borda da imagem.
    perimetro_na_borda: float


def _ordenar(pontos: NDArray[Any]) -> NDArray[Any]:
    soma = pontos.sum(axis=1)
    diferenca = np.diff(pontos, axis=1).ravel()
    return np.array(
        [
            pontos[np.argmin(soma)],
            pontos[np.argmin(diferenca)],
            pontos[np.argmax(soma)],
            pontos[np.argmax(diferenca)],
        ],
        dtype=np.float32,
    )


def _perimetro_na_borda(poligono: NDArray[Any], largura: int, altura: int, margem: float) -> float:
    """Amostra cada lado do polígono e mede quanto do perímetro está colado na borda."""
    tol_x, tol_y = largura * margem, altura * margem
    na_borda = total = 0.0
    for i in range(len(poligono)):
        a, b = poligono[i], poligono[(i + 1) % len(poligono)]
        comprimento = float(np.linalg.norm(b - a))
        for t in np.linspace(0.0, 1.0, 25):
            x, y = a + (b - a) * t
            total += comprimento
            if x <= tol_x or y <= tol_y or x >= largura - 1 - tol_x or y >= altura - 1 - tol_y:
                na_borda += comprimento
    return na_borda / total if total else 0.0


def _bordas(cinza: NDArray[Any]) -> NDArray[Any]:
    # Mediana larga apaga textura de fundo (mesa, tecido) e preserva a borda do documento.
    suave = cv2.medianBlur(cinza, 7)
    bordas = cv2.Canny(suave, 50, 150)
    return cv2.morphologyEx(bordas, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))


def _quadrilatero(bordas: NDArray[Any]) -> NDArray[Any] | None:
    contornos, _ = cv2.findContours(bordas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contorno in sorted(contornos, key=cv2.contourArea, reverse=True)[:5]:
        perimetro = cv2.arcLength(contorno, True)
        aproximado = cv2.approxPolyDP(contorno, 0.02 * perimetro, True)
        if len(aproximado) == 4 and cv2.isContourConvex(aproximado):
            return _ordenar(aproximado.reshape(4, 2).astype(np.float32))
    return None


def _lados_tocados(poligono: NDArray[Any], largura: int, altura: int, margem: float) -> int:
    tol_x, tol_y = largura * margem, altura * margem
    xs, ys = poligono[:, 0], poligono[:, 1]
    lados = (
        (xs <= tol_x).any(),
        (xs >= largura - 1 - tol_x).any(),
        (ys <= tol_y).any(),
        (ys >= altura - 1 - tol_y).any(),
    )
    return sum(1 for lado in lados if lado)


def _indicio_de_corte(bordas: NDArray[Any], settings: Settings) -> float:
    """Documento saindo do quadro não fecha contorno sozinho: fechamos com a moldura da
    imagem e olhamos as regiões grandes que tocam um ou dois lados do quadro (documento
    que saiu por um lado ou por um canto). Regiões que tocam três ou quatro lados são fundo
    ou faixas de um print (barra de status). Devolve a menor fração do perímetro colada
    na borda entre essas regiões."""
    alto, largo = bordas.shape
    fechada = bordas.copy()
    fechada[0, :] = fechada[-1, :] = fechada[:, 0] = fechada[:, -1] = 255
    contornos, _ = cv2.findContours(fechada, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    fracoes = []
    for contorno in contornos:
        area = cv2.contourArea(contorno) / (alto * largo)
        if not settings.documento_area_min <= area <= settings.documento_area_max:
            continue
        perimetro = cv2.arcLength(contorno, True)
        poligono = cv2.approxPolyDP(contorno, 0.01 * perimetro, True).reshape(-1, 2)
        poligono = poligono.astype(np.float32)
        if 1 <= _lados_tocados(poligono, largo, alto, settings.documento_margem_borda) <= 2:
            fracoes.append(
                _perimetro_na_borda(poligono, largo, alto, settings.documento_margem_borda)
            )
    return float(min(fracoes)) if fracoes else 0.0


def localizar(imagem: Image.Image, settings: Settings) -> DocumentoLocalizado:
    largura, altura = imagem.size
    escala = min(1.0, 1000 / max(largura, altura))
    reduzida = imagem.convert("L").resize(
        (max(1, int(largura * escala)), max(1, int(altura * escala)))
    )
    cinza = np.asarray(reduzida, dtype=np.uint8)
    alto, largo = int(cinza.shape[0]), int(cinza.shape[1])
    bordas = _bordas(cinza)
    quad = _quadrilatero(bordas)

    def area_de(poligono: NDArray[Any]) -> float:
        return float(cv2.contourArea(poligono)) / (alto * largo)

    if quad is None or not (
        settings.documento_area_min <= area_de(quad) <= settings.documento_area_max
    ):
        # Sem quadrilátero plausível: usa a imagem toda e guarda o indício de corte.
        return DocumentoLocalizado(imagem, False, None, 1.0, _indicio_de_corte(bordas, settings))

    area = area_de(quad)
    na_borda = _perimetro_na_borda(quad, largo, alto, settings.documento_margem_borda)
    quad = quad / escala
    sup_esq, sup_dir, inf_dir, inf_esq = quad
    largura_doc = int(max(np.linalg.norm(sup_dir - sup_esq), np.linalg.norm(inf_dir - inf_esq)))
    altura_doc = int(max(np.linalg.norm(inf_esq - sup_esq), np.linalg.norm(inf_dir - sup_dir)))
    destino = np.array(
        [[0, 0], [largura_doc - 1, 0], [largura_doc - 1, altura_doc - 1], [0, altura_doc - 1]],
        dtype=np.float32,
    )
    matriz = cv2.getPerspectiveTransform(quad, destino)
    rgb = np.asarray(imagem.convert("RGB"))
    corrigida = cv2.warpPerspective(rgb, matriz, (largura_doc, altura_doc))
    contorno: Pontos = [(int(x), int(y)) for x, y in quad]
    return DocumentoLocalizado(Image.fromarray(corrigida), True, contorno, area, na_borda)


def limitar_resolucao(imagem: Image.Image, settings: Settings) -> Image.Image:
    """Cabe o documento em ALVO_LADO_MAIOR × ALVO_LADO_MENOR (cerca de 1600 × 1000 px),
    respeitando a orientação. Nunca amplia."""
    maior, menor = settings.alvo_lado_maior_px, settings.alvo_lado_menor_px
    largura, altura = imagem.size
    limite = (maior, menor) if largura >= altura else (menor, maior)
    fator = min(1.0, limite[0] / largura, limite[1] / altura)
    if fator >= 1.0:
        return imagem
    return imagem.resize(
        (max(1, round(largura * fator)), max(1, round(altura * fator))), Image.Resampling.LANCZOS
    )


def girar(imagem: Image.Image, graus_horario: int) -> Image.Image:
    """Gira no sentido horário (0, 90, 180 ou 270), para deixar o documento de pé."""
    if graus_horario % 360 == 0:
        return imagem
    return imagem.rotate(-graus_horario, expand=True)
