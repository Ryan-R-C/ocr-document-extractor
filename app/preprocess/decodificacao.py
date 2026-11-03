"""Decodificação da entrada: base64 → arquivo → imagem normalizada (PNG RGB).

Erros de requisição (400/413) nascem aqui. Mensagens nunca contêm conteúdo do documento.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
from dataclasses import dataclass
from enum import StrEnum

import pillow_heif
import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from app.api.contrato import MotivoErro
from app.config import Settings
from app.core.erros import FalhaArquivoGrande, FalhaRequisicao
from app.providers.interfaces import Imagem

pillow_heif.register_heif_opener()


class Formato(StrEnum):
    JPEG = "JPEG"
    PNG = "PNG"
    WEBP = "WEBP"
    HEIC = "HEIC"
    PDF = "PDF"


MIME = {
    Formato.JPEG: "image/jpeg",
    Formato.PNG: "image/png",
    Formato.WEBP: "image/webp",
    Formato.HEIC: "image/heic",
    Formato.PDF: "application/pdf",
}

EXTENSAO = {
    Formato.JPEG: "jpg",
    Formato.PNG: "png",
    Formato.WEBP: "webp",
    Formato.HEIC: "heic",
    Formato.PDF: "pdf",
}

_MARCAS_HEIF = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}


@dataclass(frozen=True)
class ArquivoRecebido:
    conteudo: bytes
    formato: Formato
    sha256: str

    @property
    def mime(self) -> str:
        return MIME[self.formato]


@dataclass(frozen=True)
class ImagemNormalizada:
    imagem: Imagem
    paginas_pdf: int | None = None


def detectar_formato(conteudo: bytes) -> Formato | None:
    if conteudo.startswith(b"\xff\xd8\xff"):
        return Formato.JPEG
    if conteudo.startswith(b"\x89PNG\r\n\x1a\n"):
        return Formato.PNG
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return Formato.WEBP
    if conteudo[4:8] == b"ftyp" and conteudo[8:12] in _MARCAS_HEIF:
        return Formato.HEIC
    if conteudo.startswith(b"%PDF-"):
        return Formato.PDF
    return None


def decodificar_base64(texto: str, settings: Settings) -> ArquivoRecebido:
    limite = settings.arquivo_max_bytes
    # Corte barato antes de decodificar: base64 ocupa ~4/3 do binário.
    if len(texto) > math.ceil(limite / 3) * 4 * 1.05 + 1024:
        raise FalhaArquivoGrande.de(MotivoErro.ARQUIVO_GRANDE_DEMAIS)

    bruto = texto.strip()
    if bruto.startswith("data:"):
        _, _, bruto = bruto.partition(",")
    bruto = "".join(bruto.split())
    if not bruto:
        raise FalhaRequisicao.de(MotivoErro.BASE64_INVALIDO, detalhe="conteúdo vazio")
    try:
        conteudo = base64.b64decode(bruto, validate=True)
    except (binascii.Error, ValueError):
        raise FalhaRequisicao.de(MotivoErro.BASE64_INVALIDO) from None

    if len(conteudo) > limite:
        raise FalhaArquivoGrande.de(MotivoErro.ARQUIVO_GRANDE_DEMAIS)

    formato = detectar_formato(conteudo)
    if formato is None:
        raise FalhaRequisicao.de(MotivoErro.FORMATO_NAO_SUPORTADO)
    return ArquivoRecebido(conteudo, formato, hashlib.sha256(conteudo).hexdigest())


def _abrir_pdf(arquivo: ArquivoRecebido, settings: Settings) -> tuple[Image.Image, int]:
    try:
        documento = pdfium.PdfDocument(arquivo.conteudo)
    except pdfium.PdfiumError:
        raise FalhaRequisicao.de(
            MotivoErro.FORMATO_NAO_SUPORTADO, detalhe="PDF não pôde ser aberto"
        ) from None
    try:
        paginas = len(documento)
        if paginas == 0:
            raise FalhaRequisicao.de(MotivoErro.FORMATO_NAO_SUPORTADO, detalhe="PDF sem páginas")
        # Fase 0: só a primeira página (PDF com várias páginas é decisão em aberto).
        pagina = documento[0]
        largura_pt, altura_pt = pagina.get_size()
        escala = settings.pdf_dpi / 72
        pixels = largura_pt * altura_pt * escala * escala
        if pixels > settings.imagem_pixels_max:
            escala *= math.sqrt(settings.imagem_pixels_max / pixels)
        imagem = pagina.render(scale=escala).to_pil()
        return imagem, paginas
    finally:
        documento.close()


def _abrir_imagem(arquivo: ArquivoRecebido, settings: Settings) -> Image.Image:
    try:
        imagem = Image.open(io.BytesIO(arquivo.conteudo))
    except (UnidentifiedImageError, OSError, ValueError):
        raise FalhaRequisicao.de(
            MotivoErro.FORMATO_NAO_SUPORTADO, detalhe="imagem não pôde ser decodificada"
        ) from None
    largura, altura = imagem.size
    if largura * altura > settings.imagem_pixels_max:
        raise FalhaArquivoGrande.de(MotivoErro.ARQUIVO_GRANDE_DEMAIS, detalhe="resolução")
    try:
        imagem.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise FalhaRequisicao.de(
            MotivoErro.FORMATO_NAO_SUPORTADO, detalhe="imagem não pôde ser decodificada"
        ) from None
    return imagem


def normalizar(arquivo: ArquivoRecebido, settings: Settings) -> ImagemNormalizada:
    """Converte qualquer formato aceito em PNG RGB, com orientação EXIF aplicada e lado limitado."""
    imagem, paginas = normalizar_pil(arquivo, settings)
    return ImagemNormalizada(imagem=para_imagem(imagem, arquivo.sha256), paginas_pdf=paginas)


def normalizar_pil(arquivo: ArquivoRecebido, settings: Settings) -> tuple[Image.Image, int | None]:
    paginas: int | None = None
    if arquivo.formato is Formato.PDF:
        imagem, paginas = _abrir_pdf(arquivo, settings)
    else:
        imagem = _abrir_imagem(arquivo, settings)
        imagem = ImageOps.exif_transpose(imagem)
    imagem = imagem.convert("RGB")
    imagem.thumbnail(
        (settings.imagem_lado_max_px, settings.imagem_lado_max_px), Image.Resampling.LANCZOS
    )
    return imagem, paginas


def para_imagem(imagem: Image.Image, sha256_origem: str) -> Imagem:
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    conteudo = buffer.getvalue()
    return Imagem(
        conteudo=conteudo,
        mime="image/png",
        largura=imagem.width,
        altura=imagem.height,
        sha256=hashlib.sha256(conteudo).hexdigest(),
        sha256_origem=sha256_origem,
    )


def abrir_pil(imagem: Imagem) -> Image.Image:
    return Image.open(io.BytesIO(imagem.conteudo))
