"""Localizador por OCR: a caixa de cada campo vem das palavras do OCR clássico.

Ordem: a evidência da ancoragem (palavras do valor); sem ela, a região do rótulo (à direita
na mesma linha e linhas logo abaixo). Sem caixa, não há recorte nem releitura, e o campo
fica em revisão.
"""

from __future__ import annotations

from app.config import RegiaoAncoragem, Settings
from app.core import ancoragem as mod_ancoragem
from app.core.ancoragem import ParametrosAncoragem, uniao
from app.providers.interfaces import (
    Caixa,
    Imagem,
    LeituraTexto,
    LeituraVisual,
    Localizacao,
)
from app.schemas.campos import definicao


def regiao_do_rotulo(rotulo: Caixa, regiao: RegiaoAncoragem, largura: int, altura: int) -> Caixa:
    """Retângulo onde o valor deve estar: à direita do rótulo e linhas logo abaixo."""
    h = max(1, rotulo.altura)
    x0 = max(0, int(rotulo.x - regiao.abaixo_folga_alturas * h / 4))
    x1 = min(largura, int(rotulo.x + rotulo.largura + regiao.direita_max_alturas * h / 2))
    y0 = max(0, rotulo.y)
    y1 = min(altura, int(rotulo.y + rotulo.altura + regiao.abaixo_max_alturas * h))
    return Caixa(x0, y0, max(1, x1 - x0), max(1, y1 - y0))


class LocalizadorPorOCR:
    nome = "ocr"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def localizar(
        self,
        imagem: Imagem,
        leitura_visual: LeituraVisual,
        leitura_texto: LeituraTexto,
        campos: list[str],
    ) -> Localizacao:
        subtipo = leitura_visual.subtipo
        parametros = ParametrosAncoragem.de(self.settings, subtipo.value)
        candidatos = mod_ancoragem.trechos(leitura_texto.palavras, parametros.palavras_max)
        caixas: dict[str, Caixa] = {}
        for nome in campos:
            defn = definicao(subtipo, nome)
            if defn is None:
                continue
            resultado = mod_ancoragem.ancorar_campo(
                defn, leitura_visual.campos.get(nome), leitura_texto, parametros, candidatos
            )
            if resultado.caixa_valor is not None:
                caixas[nome] = resultado.caixa_valor
                continue
            lido = leitura_visual.campos.get(nome)
            nomes = [r for r in ((lido.rotulo_visto if lido else ""), *defn.rotulos) if r.strip()]
            rotulos = mod_ancoragem.encontrar_rotulos(
                candidatos, nomes, parametros.rotulo_similaridade_min
            )
            if rotulos:
                caixas[nome] = uniao(
                    [
                        rotulos[0].caixa,
                        regiao_do_rotulo(
                            rotulos[0].caixa, parametros.regiao, imagem.largura, imagem.altura
                        ),
                    ]
                )
        return Localizacao(caixas=caixas)
