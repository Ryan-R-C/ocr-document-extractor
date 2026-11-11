"""Heurísticas de captura em CPU: cópia reprográfica e print de tela.

Só geram sinalização (nunca bloqueiam). A sinalização final combina a heurística com a
classificação do VLM conforme `SINALIZACAO_CAPTURA_FONTE`; o avaliador mede a precisão de
cada fonte contra o campo `qualidade` do gabarito.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from app.config import Settings


@dataclass(frozen=True)
class IndiciosCaptura:
    saturacao_media: float
    ruido: float
    pixels_repetidos: float
    copia_reprografica: bool
    print_de_tela: bool


def analisar(imagem: Image.Image, settings: Settings) -> IndiciosCaptura:
    rgb: NDArray[Any] = np.asarray(imagem.convert("RGB"), dtype=np.uint8)
    if max(rgb.shape[:2]) > 1200:
        fator = 1200 / max(rgb.shape[:2])
        rgb = cv2.resize(rgb, None, fx=fator, fy=fator, interpolation=cv2.INTER_NEAREST)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturacao = float(hsv[..., 1].mean())

    cinza = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.int16)
    # Ruído: diferença entre vizinhos nas regiões lisas (fotos e cópias têm grão; prints não).
    dif = np.abs(np.diff(cinza, axis=1))
    lisas = dif[dif < 12]
    ruido = float(lisas.mean()) if lisas.size else 0.0
    # Prints têm muitos pixels exatamente iguais ao vizinho (áreas chapadas).
    repetidos = float((dif == 0).mean())

    print_de_tela = (
        repetidos >= settings.captura_print_repetidos_min
        and ruido <= settings.captura_print_ruido_max
    )
    copia = (not print_de_tela) and saturacao <= settings.captura_copia_saturacao_max
    return IndiciosCaptura(saturacao, ruido, repetidos, copia, print_de_tela)
