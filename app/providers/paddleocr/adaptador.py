"""Adaptador PaddleOCR (3.x, CPU) para a interface `LeitorTexto`.

Conferido na versão instalada (paddleocr 3.7.0, paddlepaddle 3.3.1, paddlex 3.7.2):
- `PaddleOCR(lang="pt")` escolhe PP-OCRv6 medium (detecção e reconhecimento latino); os
  pesos vêm do Hugging Face (`PADDLE_PDX_MODEL_SOURCE`) e ficam em `~/.paddlex`.
- `predict(img, return_word_box=True)` devolve, por linha, `text_word` (fragmentos: palavras,
  espaços e pontuação) e `text_word_boxes` ([x0, y0, x1, y1] por fragmento).
- Com oneDNN (MKLDNN) ligado, a inferência falha no Paddle 3.3.1 em CPU
  (`ConvertPirAttribute2RuntimeAttribute`); por isso `enable_mkldnn=False` por padrão.
- Orientação do documento, retificação e orientação de linha ficam desligadas: a esteira
  já recorta e gira a imagem.

O modelo é carregado uma vez por instância e reaproveitado; a inferência roda num pool de
threads próprio, fora do event loop, com uma instância do modelo por thread.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol

import numpy as np

from app.config import Settings
from app.providers.interfaces import (
    Caixa,
    ErroProvider,
    Imagem,
    LeituraTexto,
    MetricasChamada,
    Palavra,
)

NOME = "paddleocr"


class MotorOCR(Protocol):
    def predict(self, entrada: Any, **opcoes: Any) -> Sequence[Mapping[str, Any]]: ...


def _versao_paddleocr() -> str:
    try:
        return version("paddleocr")
    except PackageNotFoundError:
        return "ausente"


def descrever_modelo(settings: Settings) -> str:
    """Identifica o OCR em `versao_pipeline` e nas métricas: versão, idioma e modelos."""
    det = settings.paddleocr_modelo_deteccao or "padrao"
    rec = settings.paddleocr_modelo_reconhecimento or "padrao"
    return f"paddleocr-{_versao_paddleocr()}:{settings.paddleocr_idioma}:{det}/{rec}"


def criar_motor(settings: Settings) -> MotorOCR:
    """Constrói o PaddleOCR real. Import tardio: só quem usa o adaptador precisa do pacote."""
    from paddleocr import PaddleOCR

    opcoes: dict[str, Any] = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "device": "cpu",
        "enable_mkldnn": settings.paddleocr_mkldnn,
        "cpu_threads": settings.paddleocr_threads_cpu,
    }
    if settings.paddleocr_modelo_deteccao or settings.paddleocr_modelo_reconhecimento:
        # Com modelos explícitos, o idioma não escolhe nada (e o PaddleOCR recusa os dois).
        if settings.paddleocr_modelo_deteccao:
            opcoes["text_detection_model_name"] = settings.paddleocr_modelo_deteccao
        if settings.paddleocr_modelo_reconhecimento:
            opcoes["text_recognition_model_name"] = settings.paddleocr_modelo_reconhecimento
    else:
        opcoes["lang"] = settings.paddleocr_idioma
    if settings.paddleocr_score_min is not None:
        opcoes["text_rec_score_thresh"] = settings.paddleocr_score_min
    motor: MotorOCR = PaddleOCR(**opcoes)
    return motor


def _caixa(valores: Sequence[float]) -> Caixa:
    x0, y0, x1, y1 = (round(float(v)) for v in valores[:4])
    return Caixa(x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def _uniao(caixas: Sequence[Caixa]) -> Caixa:
    x0 = min(c.x for c in caixas)
    y0 = min(c.y for c in caixas)
    x1 = max(c.x + c.largura for c in caixas)
    y1 = max(c.y + c.altura for c in caixas)
    return Caixa(x0, y0, x1 - x0, y1 - y0)


def _dividir_proporcional(texto: str, caixa: Caixa) -> list[tuple[str, Caixa]]:
    """Sem caixas por palavra: divide a linha em palavras com largura proporcional ao
    número de caracteres (aproximação; só usada se o PaddleOCR não devolver as caixas)."""
    total = max(1, len(texto))
    resultado: list[tuple[str, Caixa]] = []
    posicao = 0
    for parte in texto.split(" "):
        if parte:
            x = caixa.x + caixa.largura * posicao // total
            largura = max(1, caixa.largura * len(parte) // total)
            resultado.append((parte, Caixa(x, caixa.y, largura, caixa.altura)))
        posicao += len(parte) + 1
    return resultado


def _palavras_da_linha(
    texto_linha: str,
    caixa_linha: Caixa,
    fragmentos: Sequence[str] | None,
    caixas_fragmentos: Sequence[Sequence[float]] | None,
) -> list[tuple[str, Caixa]]:
    """Junta fragmentos consecutivos sem espaço numa palavra (`01`, `/`, `02` → `01/02`)."""
    if not fragmentos or caixas_fragmentos is None or len(fragmentos) != len(caixas_fragmentos):
        return _dividir_proporcional(texto_linha, caixa_linha)
    palavras: list[tuple[str, Caixa]] = []
    texto_atual, caixas_atuais = "", list[Caixa]()
    for fragmento, valores in zip(fragmentos, caixas_fragmentos, strict=True):
        if not fragmento.strip():
            if texto_atual:
                palavras.append((texto_atual, _uniao(caixas_atuais)))
            texto_atual, caixas_atuais = "", []
            continue
        texto_atual += fragmento
        caixas_atuais.append(_caixa(valores))
    if texto_atual:
        palavras.append((texto_atual, _uniao(caixas_atuais)))
    return palavras


def _lista(valor: Any) -> list[Any]:
    if valor is None:
        return []
    if isinstance(valor, np.ndarray):
        return list(valor.tolist())
    return list(valor)


def palavras_do_resultado(
    resultado: Mapping[str, Any],
) -> tuple[list[Palavra], list[dict[str, Any]]]:
    """Converte um resultado do PaddleOCR em palavras (id = ordem de leitura) e linhas
    (texto, caixa, confiança e ids das palavras), que vão para o pacote de auditoria."""
    textos = _lista(resultado.get("rec_texts"))
    scores = _lista(resultado.get("rec_scores"))
    caixas_linhas = _lista(resultado.get("rec_boxes"))
    fragmentos = _lista(resultado.get("text_word"))
    caixas_fragmentos = _lista(resultado.get("text_word_boxes"))

    palavras: list[Palavra] = []
    linhas: list[dict[str, Any]] = []
    for i, texto in enumerate(textos):
        if i >= len(caixas_linhas):
            break
        caixa_linha = _caixa(caixas_linhas[i])
        confianca = float(scores[i]) if i < len(scores) else 0.0
        ids: list[int] = []
        for texto_palavra, caixa in _palavras_da_linha(
            str(texto),
            caixa_linha,
            [str(f) for f in fragmentos[i]] if i < len(fragmentos) else None,
            _lista(caixas_fragmentos[i]) if i < len(caixas_fragmentos) else None,
        ):
            ids.append(len(palavras))
            palavras.append(Palavra(len(palavras), texto_palavra, caixa, confianca))
        linhas.append(
            {
                "texto": str(texto),
                "caixa": [caixa_linha.x, caixa_linha.y, caixa_linha.largura, caixa_linha.altura],
                "confianca": round(confianca, 4),
                "palavras": ids,
            }
        )
    return palavras, linhas


def _decodificar(imagem: Imagem) -> np.ndarray[Any, Any]:
    import cv2

    matriz = cv2.imdecode(np.frombuffer(imagem.conteudo, dtype=np.uint8), cv2.IMREAD_COLOR)
    if matriz is None:
        raise ErroProvider("imagem não decodificável")
    return matriz


class LeitorTextoPaddleOCR:
    nome = NOME

    def __init__(
        self,
        settings: Settings,
        fabrica: Callable[[], MotorOCR] | None = None,
    ) -> None:
        self.settings = settings
        self.modelo = descrever_modelo(settings)
        self._fabrica = fabrica or (lambda: criar_motor(settings))
        self._instancias_max = settings.paddleocr_instancias
        self._livres: queue.Queue[MotorOCR] = queue.Queue()
        self._criadas = 0
        self._trava = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=self._instancias_max, thread_name_prefix="paddleocr"
        )

    def _obter(self) -> MotorOCR:
        try:
            return self._livres.get_nowait()
        except queue.Empty:
            pass
        with self._trava:
            if self._criadas < self._instancias_max:
                self._criadas += 1
                criar = True
            else:
                criar = False
        if criar:
            try:
                return self._fabrica()
            except Exception:
                with self._trava:
                    self._criadas -= 1
                raise
        return self._livres.get()

    def _inferir(self, matriz: np.ndarray[Any, Any]) -> Mapping[str, Any]:
        motor = self._obter()
        try:
            resultados = motor.predict(matriz, return_word_box=True)
        finally:
            self._livres.put(motor)
        return resultados[0] if resultados else {}

    def aquecer(self) -> None:
        """Carrega todas as instâncias e roda uma inferência em cada (no startup)."""
        branca = np.full((64, 256, 3), 255, dtype=np.uint8)
        motores = [self._obter() for _ in range(self._instancias_max)]
        try:
            for motor in motores:
                motor.predict(branca, return_word_box=True)
        finally:
            for motor in motores:
                self._livres.put(motor)

    async def ler(self, imagem: Imagem) -> LeituraTexto:
        loop = asyncio.get_running_loop()
        inicio = time.perf_counter()
        try:
            matriz = _decodificar(imagem)
            resultado = await loop.run_in_executor(self._pool, self._inferir, matriz)
        except ErroProvider:
            raise
        except Exception as erro:
            raise ErroProvider(f"paddleocr: {type(erro).__name__}") from None
        latencia = (time.perf_counter() - inicio) * 1000
        palavras, linhas = palavras_do_resultado(resultado)
        return LeituraTexto(
            palavras=palavras,
            resposta_bruta={
                "modelo": self.modelo,
                "largura": imagem.largura,
                "altura": imagem.altura,
                "linhas": linhas,
            },
            metricas=MetricasChamada(
                provider=NOME, modelo=self.modelo, etapa="leitura_texto", latencia_ms=latencia
            ),
        )

    def fechar(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
