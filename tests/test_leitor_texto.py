"""Contrato comum a todo `LeitorTexto` e testes do adaptador PaddleOCR.

O PaddleOCR real só roda com o marcador `ocr_real`; os demais testes usam o resultado
gravado de uma execução real sobre uma imagem sintética (`tests/dados/`).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.config import Settings
from app.providers.fake import FakeLeitorTexto, gravacoes
from app.providers.fake.gravacoes import Gravacao, LeituraTextoGravada, PalavraGravada
from app.providers.interfaces import ErroProvider, Imagem, LeitorTexto, LeituraTexto
from app.providers.paddleocr import LeitorTextoPaddleOCR, palavras_do_resultado

DADOS = Path(__file__).parent / "fixtures"
RESULTADO_GRAVADO: dict[str, Any] = json.loads(
    (DADOS / "paddleocr_resultado.json").read_text(encoding="utf-8")
)


def imagem_sintetica() -> Imagem:
    conteudo = (DADOS / "paddleocr_sintetico.png").read_bytes()
    with Image.open(io.BytesIO(conteudo)) as aberta:
        largura, altura = aberta.size
    sha = hashlib.sha256(conteudo).hexdigest()
    return Imagem(conteudo, "image/png", largura, altura, sha, sha)


class MotorGravado:
    """Devolve o resultado gravado; conta as chamadas e a thread de cada uma."""

    def __init__(self, resultado: Mapping[str, Any] | None = None, erro: bool = False) -> None:
        self.resultado = resultado if resultado is not None else RESULTADO_GRAVADO
        self.erro = erro
        self.chamadas: list[str] = []

    def predict(self, entrada: Any, **opcoes: Any) -> Sequence[Mapping[str, Any]]:
        assert opcoes.get("return_word_box") is True
        self.chamadas.append(threading.current_thread().name)
        if self.erro:
            raise RuntimeError("falha simulada")
        return [self.resultado]


def _paddle(fabrica: Callable[[], Any] | None = None, **ajustes: Any) -> LeitorTextoPaddleOCR:
    settings = Settings(_env_file=None, **ajustes)
    return LeitorTextoPaddleOCR(settings, fabrica or MotorGravado)


def _fake(tmp_path: Path) -> LeitorTexto:
    imagem = imagem_sintetica()
    palavras, _ = palavras_do_resultado(RESULTADO_GRAVADO)
    gravacoes.salvar(
        tmp_path,
        imagem.sha256_origem,
        Gravacao(
            leitura_texto=LeituraTextoGravada(
                palavras=[
                    PalavraGravada(
                        id=p.id,
                        texto=p.texto,
                        caixa=(p.caixa.x, p.caixa.y, p.caixa.largura, p.caixa.altura),
                        confianca=p.confianca,
                    )
                    for p in palavras
                ]
            )
        ),
    )
    return FakeLeitorTexto(Settings(_env_file=None), tmp_path)


ADAPTADORES: dict[str, Callable[[Path], LeitorTexto]] = {
    "fake": _fake,
    "paddleocr": lambda _: _paddle(),
}


# --- Contrato: todo LeitorTexto (Fase 4: novos adaptadores entram em ADAPTADORES) ----------


@pytest.mark.parametrize("nome", list(ADAPTADORES))
def test_contrato_leitor_texto(nome: str, tmp_path: Path) -> None:
    leitor = ADAPTADORES[nome](tmp_path)
    assert isinstance(leitor, LeitorTexto)
    imagem = imagem_sintetica()
    leitura = asyncio.run(leitor.ler(imagem))
    assert isinstance(leitura, LeituraTexto)
    assert leitura.palavras, "a imagem sintética tem texto"
    # Ids estáveis: únicos, na ordem de leitura, a partir de 0.
    assert [p.id for p in leitura.palavras] == list(range(len(leitura.palavras)))
    for p in leitura.palavras:
        assert p.texto and p.texto == p.texto.strip() and " " not in p.texto
        assert 0.0 <= p.confianca <= 1.0
        assert p.caixa.largura > 0 and p.caixa.altura > 0
        assert p.caixa.x >= 0 and p.caixa.x + p.caixa.largura <= imagem.largura
        assert p.caixa.y >= 0 and p.caixa.y + p.caixa.altura <= imagem.altura
    assert leitura.metricas.etapa == "leitura_texto"
    assert leitura.metricas.provider == leitor.nome
    assert leitura.metricas.custo_brl == 0.0
    # Determinístico: a mesma imagem dá as mesmas palavras.
    assert asyncio.run(leitor.ler(imagem)).palavras == leitura.palavras
    # Valor com pontuação vira uma palavra só, com caixa à direita do rótulo ou abaixo dele.
    textos = [p.texto for p in leitura.palavras]
    assert "158.813.998-03" in textos and "03/07/1988" in textos


# --- PaddleOCR --------------------------------------------------------------------------


def test_palavras_juntam_fragmentos_e_guardam_linhas() -> None:
    palavras, linhas = palavras_do_resultado(RESULTADO_GRAVADO)
    textos = [p.texto for p in palavras]
    assert textos[:4] == ["NOME", "LAREMI", "TOVANU", "PESO"]
    assert "12.345.678-X" in textos and "15/01/2019" in textos
    assert linhas[1]["texto"] == "LAREMI TOVANU PESO"
    assert linhas[1]["palavras"] == [1, 2, 3]
    # A caixa da palavra fica dentro da caixa da linha.
    x, _y, w, _h = linhas[1]["caixa"]
    for i in linhas[1]["palavras"]:
        c = palavras[i].caixa
        assert x - 2 <= c.x and c.x + c.largura <= x + w + 2


def test_sem_caixas_por_palavra_divide_a_linha_proporcionalmente() -> None:
    resultado = {
        "rec_texts": ["DATA 01/02/1990"],
        "rec_scores": [0.9],
        "rec_boxes": [[10, 10, 160, 40]],
    }
    palavras, _ = palavras_do_resultado(resultado)
    assert [p.texto for p in palavras] == ["DATA", "01/02/1990"]
    assert palavras[0].caixa.x == 10 and palavras[1].caixa.x > palavras[0].caixa.x


def test_falha_do_motor_vira_erro_provider() -> None:
    leitor = _paddle(lambda: MotorGravado(erro=True))
    with pytest.raises(ErroProvider, match="paddleocr"):
        asyncio.run(leitor.ler(imagem_sintetica()))


def test_imagem_invalida_vira_erro_provider() -> None:
    leitor = _paddle()
    with pytest.raises(ErroProvider):
        asyncio.run(leitor.ler(Imagem(b"nao-e-imagem", "image/png", 1, 1, "x", "x")))


def test_modelo_carregado_uma_vez_e_inferencia_fora_do_event_loop() -> None:
    criados: list[MotorGravado] = []

    def fabrica() -> MotorGravado:
        criados.append(MotorGravado())
        return criados[-1]

    leitor = _paddle(fabrica, paddleocr_instancias=2)

    async def varias() -> list[LeituraTexto]:
        return await asyncio.gather(*(leitor.ler(imagem_sintetica()) for _ in range(6)))

    leituras = asyncio.run(varias())
    assert len(leituras) == 6
    assert 1 <= len(criados) <= 2
    threads = {t for m in criados for t in m.chamadas}
    assert threads and all(t.startswith("paddleocr") for t in threads)


def test_aquecer_carrega_todas_as_instancias() -> None:
    criados: list[MotorGravado] = []

    def fabrica() -> MotorGravado:
        criados.append(MotorGravado())
        return criados[-1]

    leitor = _paddle(fabrica, paddleocr_instancias=3)
    leitor.aquecer()
    assert len(criados) == 3 and all(len(m.chamadas) == 1 for m in criados)
    asyncio.run(leitor.ler(imagem_sintetica()))
    assert len(criados) == 3  # reaproveita


def test_modelo_identificado_nas_metricas() -> None:
    leitura = asyncio.run(_paddle(paddleocr_idioma="pt").ler(imagem_sintetica()))
    assert leitura.metricas.modelo.startswith("paddleocr-")
    assert ":pt:padrao/padrao" in leitura.metricas.modelo
    assert leitura.resposta_bruta["linhas"]


@pytest.mark.ocr_real
def test_paddleocr_real_le_a_imagem_sintetica() -> None:
    pytest.importorskip("paddleocr")
    leitor = LeitorTextoPaddleOCR(Settings(_env_file=None))
    leitor.aquecer()
    leitura = asyncio.run(leitor.ler(imagem_sintetica()))
    textos = [p.texto for p in leitura.palavras]
    assert "158.813.998-03" in textos and "03/07/1988" in textos
