"""Pré-processamento em CPU: localização, perspectiva, qualidade, captura, resolução e rotação."""

from __future__ import annotations

import random

import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.config import Settings
from app.preprocess import captura, documento, qualidade


@pytest.fixture
def config() -> Settings:
    return Settings(_env_file=None)


def cartao(
    largura: int = 860, altura: int = 540, fundo=(205, 190, 160), grao: bool = True
) -> Image.Image:
    """Cartão sintético com texto e linhas, com grão de câmera."""
    rng = random.Random(1)
    imagem = Image.new("RGB", (largura, altura), fundo)
    desenho = ImageDraw.Draw(imagem)
    desenho.rectangle((10, 10, largura - 11, altura - 11), outline=(90, 70, 40), width=4)
    for linha in range(8):
        y = 60 + linha * 55
        desenho.text((40, y), "CAMPO SINTETICO", fill=(80, 80, 80))
        desenho.text(
            (40, y + 18),
            "".join(rng.choice("ABCDEFG0123456789 ") for _ in range(24)),
            fill=(10, 10, 10),
        )
    if grao:
        ruido = Image.effect_noise(imagem.size, 20).convert("RGB")
        imagem = Image.blend(imagem, ruido, 0.05)
    return imagem


def foto(
    cartao_img: Image.Image, posicao: tuple[int, int], tamanho=(1600, 1200), angulo: float = 0
) -> Image.Image:
    fundo = Image.new("RGB", tamanho, (35, 45, 40))
    fundo = Image.blend(fundo, Image.effect_noise(tamanho, 15).convert("RGB"), 0.1)
    girado = cartao_img.rotate(angulo, expand=True, fillcolor=(35, 45, 40))
    fundo.paste(girado, posicao)
    return fundo


def _avaliar(imagem: Image.Image, config: Settings):
    local = documento.localizar(imagem, config)
    return local, qualidade.avaliar(imagem, local, config)


def test_localiza_documento_e_corrige_perspectiva(config) -> None:
    imagem = foto(cartao(), (300, 250), angulo=3)
    local, avaliacao = _avaliar(imagem, config)
    assert local.encontrado
    assert local.contorno is not None and len(local.contorno) == 4
    assert 0.2 < local.area_relativa < 0.5
    # Recorte do tamanho do cartão (com folga pela rotação), sem o fundo.
    assert abs(local.imagem.width - 860) < 80 and abs(local.imagem.height - 540) < 80
    assert avaliacao.reprovacoes == []


def test_documento_ocupando_a_imagem_toda_nao_e_recortado_a_toa(config) -> None:
    imagem = cartao()
    local, avaliacao = _avaliar(imagem, config)
    # Ou usa a imagem inteira, ou recorta só a moldura impressa do cartão.
    assert not local.encontrado or local.area_relativa > 0.9
    assert local.perimetro_na_borda == 0.0
    assert avaliacao.reprovacoes == []


def test_documento_cortado(config) -> None:
    imagem = foto(cartao(1100, 700), (800, 250))  # sai pela direita
    _, avaliacao = _avaliar(imagem, config)
    assert avaliacao.corte is not None and avaliacao.corte > config.qualidade_corte_max
    assert [m.codigo.value for m in avaliacao.reprovacoes] == ["DOCUMENTO_CORTADO"]


def test_imagem_desfocada(config) -> None:
    imagem = cartao(grao=False).filter(ImageFilter.GaussianBlur(7))
    _, avaliacao = _avaliar(imagem, config)
    assert "IMAGEM_DESFOCADA" in [m.codigo.value for m in avaliacao.reprovacoes]


def test_reflexo_excessivo(config) -> None:
    imagem = cartao()
    ImageDraw.Draw(imagem).ellipse((250, 150, 560, 380), fill=(255, 255, 255))
    _, avaliacao = _avaliar(imagem, config)
    assert avaliacao.reflexo is not None and avaliacao.reflexo > config.qualidade_reflexo_max
    assert "REFLEXO_EXCESSIVO" in [m.codigo.value for m in avaliacao.reprovacoes]


def test_fundo_branco_nao_e_reflexo(config) -> None:
    imagem = cartao(fundo=(255, 255, 255), grao=False)
    _, avaliacao = _avaliar(imagem, config)
    assert avaliacao.reflexo == 0.0


def test_imagem_sem_conteudo_e_nenhum_documento(config) -> None:
    _, avaliacao = _avaliar(Image.new("RGB", (800, 500), (240, 240, 240)), config)
    assert [m.codigo.value for m in avaliacao.reprovacoes] == ["NENHUM_DOCUMENTO"]


def test_limiares_vem_da_configuracao(config) -> None:
    imagem = cartao()
    exigente = config.model_copy(update={"qualidade_nitidez_min": 1e9})
    _, avaliacao = _avaliar(imagem, exigente)
    assert "IMAGEM_DESFOCADA" in [m.codigo.value for m in avaliacao.reprovacoes]


def test_captura_print_copia_e_foto(config) -> None:
    print_tela = cartao(fundo=(255, 255, 255), grao=False)
    ImageDraw.Draw(print_tela).rectangle((0, 0, 860, 30), fill=(20, 20, 20))
    indicios = captura.analisar(print_tela, config)
    assert indicios.print_de_tela and not indicios.copia_reprografica

    copia = cartao().convert("L").convert("RGB")
    copia = Image.blend(copia, Image.effect_noise(copia.size, 30).convert("RGB"), 0.1)
    indicios = captura.analisar(copia, config)
    assert indicios.copia_reprografica and not indicios.print_de_tela

    indicios = captura.analisar(cartao(), config)
    assert not indicios.copia_reprografica and not indicios.print_de_tela


@pytest.mark.parametrize(
    ("tamanho", "esperado"),
    [
        ((4000, 3000), (1333, 1000)),
        ((3000, 4000), (1000, 1333)),
        ((3200, 1000), (1600, 500)),
        ((800, 500), (800, 500)),
    ],
)
def test_limite_de_resolucao(config, tamanho, esperado) -> None:
    assert documento.limitar_resolucao(Image.new("RGB", tamanho), config).size == esperado


def test_alvo_do_plano_fica_dentro_dos_patches(config) -> None:
    """1600 × 1000 px em patches de 32 px = 1.600 patches (plano: 1.920 tokens com 1,2)."""
    import math

    largura, altura = documento.limitar_resolucao(Image.new("RGB", (6000, 3750)), config).size
    regra = config.regra_imagem("gpt-6-astra")
    patches = math.ceil(largura / regra.patch_px) * math.ceil(altura / regra.patch_px)
    assert patches == 1600
    assert patches * regra.multiplicador == pytest.approx(1920)


@pytest.mark.parametrize(
    ("graus", "tamanho"), [(0, (300, 100)), (90, (100, 300)), (180, (300, 100)), (270, (100, 300))]
)
def test_girar(graus, tamanho) -> None:
    imagem = Image.new("RGB", (300, 100), "white")
    imagem.putpixel((0, 0), (255, 0, 0))
    girada = documento.girar(imagem, graus)
    assert girada.size == tamanho
    if graus == 90:  # canto superior esquerdo vai para o superior direito no sentido horário
        assert girada.getpixel((99, 0)) == (255, 0, 0)
