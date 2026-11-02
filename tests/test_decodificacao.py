"""Decodificação: formatos aceitos, conversão de PDF/HEIC e limites."""

from __future__ import annotations

import base64
import io

import pillow_heif
import pypdfium2 as pdfium
import pytest
from PIL import Image

from app.core.erros import FalhaArquivoGrande, FalhaRequisicao
from app.preprocess import decodificacao
from app.preprocess.decodificacao import Formato


def _salvar(formato: str, **kwargs) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 10, 10)).save(buffer, format=formato, **kwargs)
    return buffer.getvalue()


def _pdf() -> bytes:
    documento = pdfium.PdfDocument.new()
    documento.new_page(200, 100)
    documento.new_page(200, 100)
    buffer = io.BytesIO()
    documento.save(buffer)
    return buffer.getvalue()


def _heic() -> bytes:
    pillow_heif.register_heif_opener()
    return _salvar("HEIF")


@pytest.mark.parametrize(
    ("fabrica", "formato"),
    [
        (lambda: _salvar("JPEG"), Formato.JPEG),
        (lambda: _salvar("PNG"), Formato.PNG),
        (lambda: _salvar("WEBP"), Formato.WEBP),
        (_heic, Formato.HEIC),
        (_pdf, Formato.PDF),
    ],
)
def test_formatos_aceitos(settings, fabrica, formato) -> None:
    conteudo = fabrica()
    arquivo = decodificacao.decodificar_base64(base64.b64encode(conteudo).decode(), settings)
    assert arquivo.formato is formato
    normalizada = decodificacao.normalizar(arquivo, settings)
    assert normalizada.imagem.mime == "image/png"
    assert normalizada.imagem.sha256_origem == arquivo.sha256
    if formato is Formato.PDF:
        assert normalizada.paginas_pdf == 2
        # 200 pt a 200 dpi
        assert normalizada.imagem.largura == pytest.approx(200 * 200 / 72, abs=2)


def test_lado_maximo(settings) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (5000, 1000), "white").save(buffer, format="PNG")
    arquivo = decodificacao.decodificar_base64(
        base64.b64encode(buffer.getvalue()).decode(), settings
    )
    imagem = decodificacao.normalizar(arquivo, settings).imagem
    assert max(imagem.largura, imagem.altura) == settings.imagem_lado_max_px


def test_base64_com_quebras_de_linha(settings) -> None:
    texto = base64.encodebytes(_salvar("PNG")).decode()
    assert "\n" in texto
    assert decodificacao.decodificar_base64(texto, settings).formato is Formato.PNG


def test_erros(settings) -> None:
    with pytest.raises(FalhaRequisicao, match="BASE64_INVALIDO"):
        decodificacao.decodificar_base64("***", settings)
    with pytest.raises(FalhaRequisicao, match="FORMATO_NAO_SUPORTADO"):
        decodificacao.decodificar_base64(base64.b64encode(b"texto").decode(), settings)
    pequeno = settings.model_copy(update={"arquivo_max_bytes": 10})
    with pytest.raises(FalhaArquivoGrande):
        decodificacao.decodificar_base64(base64.b64encode(_salvar("PNG")).decode(), pequeno)
    pdf_quebrado = base64.b64encode(b"%PDF-1.7 lixo").decode()
    arquivo = decodificacao.decodificar_base64(pdf_quebrado, settings)
    with pytest.raises(FalhaRequisicao, match="FORMATO_NAO_SUPORTADO"):
        decodificacao.normalizar(arquivo, settings)
