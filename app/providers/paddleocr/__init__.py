"""Leitor de texto com PaddleOCR em CPU. Único módulo que importa `paddleocr`/`paddle`."""

from app.providers.paddleocr.adaptador import LeitorTextoPaddleOCR, palavras_do_resultado

__all__ = ["LeitorTextoPaddleOCR", "palavras_do_resultado"]
