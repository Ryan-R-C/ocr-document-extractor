"""Mede tempo e memória do leitor de texto por imagem (rodar dentro do container).

Uso: `uv run medir-ocr --imagens <dir> [--limite N] [--saida medicao.json]`. Imprime só
números e nomes de arquivo (sem texto lido).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import resource
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.preprocess.decodificacao import ArquivoRecebido, detectar_formato
from app.preprocess.preparacao import preparar
from app.providers.paddleocr import LeitorTextoPaddleOCR
from avaliacao.metricas import percentil

_EXTENSOES = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".pdf"}


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def listar(diretorio: Path, limite: int | None) -> list[Path]:
    arquivos = sorted(p for p in diretorio.rglob("*") if p.suffix.lower() in _EXTENSOES)
    return arquivos[:limite] if limite else arquivos


async def medir(arquivos: list[Path], settings: Settings) -> dict[str, Any]:
    rss_inicial = _rss_mb()
    leitor = LeitorTextoPaddleOCR(settings)
    inicio = time.perf_counter()
    await asyncio.to_thread(leitor.aquecer)
    carga_s = time.perf_counter() - inicio
    rss_carregado = _rss_mb()

    por_imagem: list[dict[str, Any]] = []
    for caminho in arquivos:
        conteudo = await asyncio.to_thread(caminho.read_bytes)
        formato = detectar_formato(conteudo)
        if formato is None:
            continue
        arquivo = ArquivoRecebido(conteudo, formato, hashlib.sha256(conteudo).hexdigest())
        imagem = (await asyncio.to_thread(preparar, arquivo, settings)).imagem
        leitura = await leitor.ler(imagem)
        por_imagem.append(
            {
                "arquivo": caminho.name,
                "largura": imagem.largura,
                "altura": imagem.altura,
                "palavras": len(leitura.palavras),
                "ms": round(leitura.metricas.latencia_ms, 1),
                "rss_pico_mb": round(_rss_mb(), 1),
            }
        )
    leitor.fechar()
    tempos = [p["ms"] for p in por_imagem]
    return {
        "modelo": leitor.modelo,
        "instancias": settings.paddleocr_instancias,
        "threads_cpu": settings.paddleocr_threads_cpu,
        "imagens": len(por_imagem),
        "carga_e_aquecimento_s": round(carga_s, 2),
        "rss_inicial_mb": round(rss_inicial, 1),
        "rss_depois_da_carga_mb": round(rss_carregado, 1),
        "rss_pico_mb": round(_rss_mb(), 1),
        "ms_p50": percentil(tempos, 50),
        "ms_p95": percentil(tempos, 95),
        "ms_max": max(tempos) if tempos else None,
        "por_imagem": por_imagem,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imagens", type=Path, required=True)
    parser.add_argument("--limite", type=int)
    parser.add_argument("--saida", type=Path)
    args = parser.parse_args(argv)
    resultado = asyncio.run(medir(listar(args.imagens, args.limite), Settings()))
    texto = json.dumps(resultado, ensure_ascii=False, indent=2)
    if args.saida:
        args.saida.write_text(texto, encoding="utf-8")
    resumo = {k: v for k, v in resultado.items() if k != "por_imagem"}
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
