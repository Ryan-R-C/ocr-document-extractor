"""Métricas de qualidade da imagem (nitidez, reflexo, corte).

STUB da Fase 0: não mede nada e não reprova. As métricas reais (OpenCV, CPU) entram na
Fase 2. O stub não leva a APROVADO por si: aprovação exige ancoragem e regras reais.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.api.contrato import MotivoErroDetalhado
from app.providers.interfaces import Imagem

IMPLEMENTADO = False


@dataclass(frozen=True)
class AvaliacaoQualidade:
    nitidez: float | None = None
    reflexo: float | None = None
    corte: float | None = None
    reprovacoes: list[MotivoErroDetalhado] = field(default_factory=list)
    implementado: bool = IMPLEMENTADO


def avaliar(imagem: Imagem) -> AvaliacaoQualidade:
    return AvaliacaoQualidade()
