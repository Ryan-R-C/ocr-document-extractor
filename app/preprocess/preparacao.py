"""Pré-processamento completo em CPU: decodificação → orientação EXIF → localização e
recorte do documento → métricas de qualidade → indícios de captura → limite de resolução.

O resultado é a imagem que vai aos leitores. Reprovação de qualidade é 422.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.preprocess import captura, documento, qualidade
from app.preprocess.captura import IndiciosCaptura
from app.preprocess.decodificacao import ArquivoRecebido, normalizar_pil, para_imagem
from app.preprocess.documento import DocumentoLocalizado
from app.preprocess.qualidade import AvaliacaoQualidade
from app.providers.interfaces import Imagem


@dataclass(frozen=True)
class ImagemPreparada:
    imagem: Imagem  # documento recortado e redimensionado: é o que os leitores recebem
    normalizada: Imagem  # imagem inteira, orientada, antes do recorte (auditoria)
    paginas_pdf: int | None
    documento: DocumentoLocalizado
    qualidade: AvaliacaoQualidade
    captura: IndiciosCaptura


def preparar(arquivo: ArquivoRecebido, settings: Settings) -> ImagemPreparada:
    pil, paginas = normalizar_pil(arquivo, settings)
    localizado = documento.localizar(pil, settings)
    avaliacao = qualidade.avaliar(pil, localizado, settings)
    indicios = captura.analisar(localizado.imagem, settings)
    final = documento.limitar_resolucao(localizado.imagem, settings)
    normalizada = para_imagem(pil, arquivo.sha256)
    return ImagemPreparada(
        # Sem recorte nem redução, a imagem enviada é a própria normalizada.
        imagem=normalizada if final is pil else para_imagem(final, arquivo.sha256),
        normalizada=normalizada,
        paginas_pdf=paginas,
        documento=localizado,
        qualidade=avaliacao,
        captura=indicios,
    )
