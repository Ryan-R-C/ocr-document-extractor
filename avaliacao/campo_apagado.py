"""Teste do campo apagado: variantes das imagens do gabarito com um campo crítico borrado
ou apagado digitalmente. A resposta certa é AUSENTE ou ILEGIVEL; qualquer valor devolvido
nesse campo conta como alucinação.

Caixa do campo: a do gabarito, quando existe. Sem ela, um `LocalizadorDeValor` encontra o
valor conferido nas palavras do OCR (Fase 2: PaddleOCR). Sem caixa nem localizador, o campo
é pulado e contado no relatório. Os arquivos originais nunca são alterados.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image, ImageFilter, ImageOps, ImageStat

from app.providers.interfaces import Caixa, Imagem, LeitorTexto, Palavra
from avaliacao.gabarito import ItemGabarito, ValorGabarito

Metodo = Literal["borrar", "apagar"]
METODOS: tuple[Metodo, ...] = ("borrar", "apagar")
STATUS_ESPERADO: dict[Metodo, Literal["ILEGIVEL", "AUSENTE"]] = {
    "borrar": "ILEGIVEL",
    "apagar": "AUSENTE",
}


class LocalizadorDeValor(Protocol):
    async def localizar_valor(self, imagem: Imagem, valor: str) -> Caixa | None: ...


def _chave(texto: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", texto.upper())


class LocalizadorValorPorOCR:
    """Procura o valor conferido numa sequência contígua de palavras do OCR."""

    def __init__(self, leitor: LeitorTexto) -> None:
        self.leitor = leitor

    async def localizar_valor(self, imagem: Imagem, valor: str) -> Caixa | None:
        alvo = _chave(valor)
        if not alvo:
            return None
        palavras = (await self.leitor.ler(imagem)).palavras
        for inicio in range(len(palavras)):
            acumulado = ""
            for fim in range(inicio, len(palavras)):
                acumulado += _chave(palavras[fim].texto)
                if acumulado == alvo:
                    return _uniao(palavras[inicio : fim + 1])
                if not alvo.startswith(acumulado):
                    break
        return None


def _uniao(palavras: Sequence[Palavra]) -> Caixa:
    x0 = min(p.caixa.x for p in palavras)
    y0 = min(p.caixa.y for p in palavras)
    x1 = max(p.caixa.x + p.caixa.largura for p in palavras)
    y1 = max(p.caixa.y + p.caixa.altura for p in palavras)
    return Caixa(x0, y0, x1 - x0, y1 - y0)


def aplicar(imagem: Image.Image, caixa: Caixa, metodo: Metodo, margem: int = 4) -> Image.Image:
    resultado = imagem.copy()
    area = (
        max(0, caixa.x - margem),
        max(0, caixa.y - margem),
        min(imagem.width, caixa.x + caixa.largura + margem),
        min(imagem.height, caixa.y + caixa.altura + margem),
    )
    recorte = resultado.crop(area)
    if metodo == "borrar":
        raio = max(6, (area[3] - area[1]) // 2)
        recorte = recorte.filter(ImageFilter.GaussianBlur(raio))
    else:
        # Preenche com a cor do fundo: mediana da área (o texto é minoria dos pixels).
        cor = tuple(int(v) for v in ImageStat.Stat(recorte).median)
        recorte = Image.new(resultado.mode, recorte.size, cor)
    resultado.paste(recorte, area[:2])
    return resultado


@dataclass
class ResultadoVariantes:
    itens: list[ItemGabarito] = field(default_factory=list)
    pulados_sem_caixa: int = 0
    pulados_formato: int = 0


async def gerar_variantes(
    itens: Sequence[ItemGabarito],
    dir_gabarito: Path,
    dir_saida: Path,
    metodos: Sequence[Metodo] = ("borrar", "apagar"),
    localizador: LocalizadorDeValor | None = None,
    campos_por_item: int | None = 1,
) -> ResultadoVariantes:
    """Gera uma variante por (item, campo crítico escolhido, método). Determinístico:
    os campos de cada item são escolhidos pela ordem do hash (sha256 do item + campo);
    `campos_por_item=None` usa todos os campos críticos com valor."""
    await asyncio.to_thread(dir_saida.mkdir, parents=True, exist_ok=True)
    resultado = ResultadoVariantes()
    for item in itens:
        if item.variante_de is not None:
            continue
        caminho = dir_gabarito / item.arquivo
        if item.arquivo.lower().endswith(".pdf"):
            resultado.pulados_formato += 1
            continue
        original = await asyncio.to_thread(_abrir, caminho)
        candidatos = sorted(
            (n for n in item.campos_criticos() if n in item.campos and item.campos[n].tem_valor),
            key=lambda n: hashlib.sha256(f"{item.sha256}|{n}".encode()).hexdigest(),
        )
        gerados = 0
        for nome in candidatos:
            if campos_por_item is not None and gerados >= campos_por_item:
                break
            esperado = item.campos[nome]
            caixa = Caixa(*esperado.caixa) if esperado.caixa else None
            if caixa is None and localizador is not None and esperado.valor:
                imagem = _para_imagem(original, item.sha256)
                caixa = await localizador.localizar_valor(imagem, esperado.valor)
            if caixa is None:
                resultado.pulados_sem_caixa += 1
                continue
            gerados += 1
            for metodo in metodos:
                variante = aplicar(original, caixa, metodo)
                destino = dir_saida / f"{Path(item.arquivo).stem}__{nome}__{metodo}.png"
                buffer = io.BytesIO()
                variante.save(buffer, format="PNG", compress_level=1)
                conteudo = buffer.getvalue()
                await asyncio.to_thread(destino.write_bytes, conteudo)
                campos = dict(item.campos)
                campos[nome] = ValorGabarito(status=STATUS_ESPERADO[metodo])
                resultado.itens.append(
                    item.model_copy(
                        update={
                            "arquivo": destino.name,
                            "sha256": hashlib.sha256(conteudo).hexdigest(),
                            "campos": campos,
                            "variante_de": item.arquivo,
                            "campo_apagado": nome,
                            "origem": f"campo_apagado:{metodo}",
                        }
                    )
                )
    return resultado


def _abrir(caminho: Path) -> Image.Image:
    with Image.open(caminho) as aberta:
        return ImageOps.exif_transpose(aberta).convert("RGB")


def _para_imagem(imagem: Image.Image, sha256_origem: str) -> Imagem:
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG", compress_level=1)
    conteudo = buffer.getvalue()
    return Imagem(
        conteudo=conteudo,
        mime="image/png",
        largura=imagem.width,
        altura=imagem.height,
        sha256=hashlib.sha256(conteudo).hexdigest(),
        sha256_origem=sha256_origem,
    )
