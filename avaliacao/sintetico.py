"""Gabarito sintético: imagens geradas, valores inventados e respostas gravadas para os fakes.

Serve para testar o avaliador ponta a ponta e demonstrar o fluxo sem documento real.
Nenhum dado aqui é real: CPFs gerados (dígitos verificadores corretos), nomes montados com
sílabas sem sentido e cartões sem layout de documento oficial.

Comportamentos gravados (determinísticos pela semente) para exercitar as métricas:
leitura exata, campo crítico ilegível, caractere duvidoso, dígito trocado, alucinação em
campo ausente, confusão do OCR (letra no lugar de dígito) e frente sem dados
(LADO_SEM_DADOS). O OCR gravado tem rótulos e valores; nos casos de dígito trocado e de
caractere duvidoso, a releitura gravada (modelo de escalada) traz o valor desenhado.
"""

from __future__ import annotations

import hashlib
import io
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.api.contrato import UF, Lado, StatusLeitura, Subtipo, TipoDocumento
from app.providers.fake import gravacoes
from app.providers.fake.gravacoes import (
    CampoGravado,
    Gravacao,
    LeituraTextoGravada,
    LeituraVisualGravada,
    PalavraGravada,
    ReleituraGravada,
)
from app.providers.interfaces import QualidadeCaptura
from app.regras import digitos
from app.regras.base import idade, somar_anos
from app.regras.conjuntos.cin import prazo_cin
from app.regras.conjuntos.cnh import LEI_14071_VIGENCIA, teto_antes_de_2021, teto_desde_2021
from app.schemas.campos import SUBTIPOS_POR_TIPO, DefinicaoCampo, Verificador, campos_do_subtipo
from app.schemas.normalizacao import TipoValor
from avaliacao.gabarito import ItemGabarito, Qualidade, ValorGabarito, escrever_manifesto
from avaliacao.particao import atribuir_particoes

_SILABAS = ["LA", "RE", "MI", "TO", "VA", "NU", "PE", "SO", "QUI", "DA", "BE", "LO", "ZI", "FA"]
_ORGAOS = ["SSP", "SESP", "PC", "IGP", "DETRAN"]
_UFS = [u for u in UF if u is not UF.NAO_IDENTIFICADO]
_SUBTIPOS = [
    Subtipo.RG_ANTIGO,
    Subtipo.RG_2018_2022,
    Subtipo.CIN,
    Subtipo.CNH,
    Subtipo.CPF_CARTAO,
    Subtipo.CTPS_DIGITAL,
]
_QUALIDADE_CAPTURA: dict[str, QualidadeCaptura] = {
    "foto": QualidadeCaptura.FOTO,
    "xerox": QualidadeCaptura.COPIA_REPROGRAFICA,
    "print": QualidadeCaptura.PRINT_DE_TELA,
}
_QUALIDADES_SORTEIO: list[Qualidade] = ["foto", "foto", "xerox", "print"]
LARGURA, ALTURA = 1100, 720


def gerar_cpf(rng: random.Random) -> str:
    while True:
        base = [rng.randint(0, 9) for _ in range(9)]
        if len(set(base)) > 1:
            break
    for tamanho in (9, 10):
        soma = sum(d * p for d, p in zip(base, range(tamanho + 1, 1, -1), strict=True))
        resto = soma % 11
        base.append(0 if resto < 2 else 11 - resto)
    d = "".join(map(str, base))
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def _palavra(rng: random.Random) -> str:
    return "".join(rng.choice(_SILABAS) for _ in range(rng.randint(2, 3)))


def _data(rng: random.Random, inicio: date, fim: date) -> str:
    dia = inicio + timedelta(days=rng.randint(0, (fim - inicio).days))
    return dia.strftime("%d/%m/%Y")


_TAMANHOS_DV = {
    Verificador.CNH: 11,
    Verificador.PIS: 11,
    Verificador.TITULO_ELEITOR: 12,
    Verificador.CNS: 15,
}


def _com_dv(rng: random.Random, verificador: Verificador) -> str:
    """Número com dígito verificador válido, sorteado com o `rng` (determinístico)."""
    while True:
        if verificador is Verificador.CNPJ:
            candidato = "".join(str(rng.randint(0, 9)) for _ in range(14))
        else:
            tamanho = _TAMANHOS_DV[verificador]
            candidato = "".join(str(rng.randint(0, 9)) for _ in range(tamanho))
        if digitos.verificar(verificador, candidato):
            return candidato


def _rg(rng: random.Random, uf: UF) -> str:
    if uf is UF.SP:
        base = "".join(str(rng.randint(1, 9)) for _ in range(8))
        d = base + digitos.dv_rg_sp(base)
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}-{d[8]}"
    d = "".join(str(rng.randint(0, 9)) for _ in range(8))
    return f"{d[:2]}.{d[2:5]}.{d[5:]}-{rng.randint(0, 9)}"


def _valor(rng: random.Random, definicao: DefinicaoCampo, uf: UF) -> str:
    nome = definicao.nome
    tipo = definicao.tipo_valor
    if tipo is TipoValor.CPF:
        return gerar_cpf(rng)
    if nome == "numero_rg":
        return _rg(rng, uf)
    if nome == "numero_ctps":
        return "".join(str(rng.randint(0, 9)) for _ in range(7))
    if nome == "serie_ctps":
        return "".join(str(rng.randint(0, 9)) for _ in range(4))
    if nome == "contrato_cbo":
        return "".join(str(rng.randint(0, 9)) for _ in range(6))
    if definicao.verificador in _TAMANHOS_DV or definicao.verificador is Verificador.CNPJ:
        assert definicao.verificador is not None
        return _com_dv(rng, definicao.verificador)
    if tipo in (TipoValor.DATA, TipoValor.VALIDADE):
        if nome == "data_nascimento":
            return _data(rng, date(1950, 1, 1), date(2005, 12, 31))
        if nome == "validade":
            return _data(rng, date(2027, 1, 1), date(2035, 12, 31))
        return _data(rng, date(2006, 1, 1), date(2026, 6, 30))
    if tipo is TipoValor.UF:
        return uf.value
    if tipo is TipoValor.CATEGORIA_CNH:
        return rng.choice(["A", "B", "AB", "C", "D", "AE"])
    if tipo is TipoValor.CNPJ:
        return "".join(str(rng.randint(0, 9)) for _ in range(14))
    if tipo is TipoValor.DIGITOS:
        return "".join(str(rng.randint(0, 9)) for _ in range(11))
    if tipo is TipoValor.ALFANUMERICO:
        d = "".join(str(rng.randint(0, 9)) for _ in range(8))
        return f"{d[:2]}.{d[2:5]}.{d[5:]}-{rng.randint(0, 9)}"
    if nome == "orgao_emissor":
        # Quase sempre SSP/UF (coerente); às vezes um órgão fora da tabela (vai para revisão).
        return f"SSP/{uf.value}" if rng.random() < 0.8 else rng.choice(_ORGAOS)
    if nome.startswith("nome") or nome.startswith("filiacao") or nome == "contrato_empregador":
        return " ".join(_palavra(rng) for _ in range(3))
    return " ".join(_palavra(rng) for _ in range(2))


@dataclass
class _Desenho:
    imagem: Image.Image
    caixas: dict[str, tuple[int, int, int, int]]
    palavras: list[PalavraGravada]


def _br(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def _de_br(texto: str) -> date:
    dia, mes, ano = (int(p) for p in texto.split("/"))
    return date(ano, mes, dia)


def _coerentes(rng: random.Random, subtipo: Subtipo, valores: dict[str, str]) -> None:
    """Datas coerentes entre si, como num documento real, para as regras da Fase 3."""
    if "data_nascimento" not in valores:
        return
    nascimento = _de_br(valores["data_nascimento"])
    if "primeira_habilitacao" in valores:
        primeira = somar_anos(nascimento, rng.randint(18, 30))
        valores["primeira_habilitacao"] = _br(primeira)
        if "data_emissao" in valores and _de_br(valores["data_emissao"]) < primeira:
            valores["data_emissao"] = _br(primeira + timedelta(days=rng.randint(0, 3000)))
    if subtipo is Subtipo.CIN and {"data_expedicao", "validade"} <= valores.keys():
        expedicao = _de_br(valores["data_expedicao"])
        prazo = prazo_cin(idade(nascimento, expedicao))
        valores["validade"] = (
            "INDETERMINADA" if prazo is None else _br(somar_anos(expedicao, prazo))
        )
    if subtipo is Subtipo.CNH and {"data_emissao", "validade"} <= valores.keys():
        emissao = _de_br(valores["data_emissao"])
        anos = idade(nascimento, emissao)
        teto = teto_desde_2021(anos) if emissao >= LEI_14071_VIGENCIA else teto_antes_de_2021(anos)
        valores["validade"] = _br(somar_anos(emissao, teto))
    if {"contrato_admissao", "contrato_saida"} <= valores.keys():
        a, b = sorted((_de_br(valores["contrato_admissao"]), _de_br(valores["contrato_saida"])))
        valores["contrato_admissao"], valores["contrato_saida"] = _br(a), _br(b)


def _desenhar(
    subtipo: Subtipo,
    valores: dict[str, str],
    definicoes: list[DefinicaoCampo],
    qualidade: str,
    amostra: int,
) -> _Desenho:
    fundo = (250, 248, 240)
    imagem = Image.new("RGB", (LARGURA, ALTURA), fundo)
    desenho = ImageDraw.Draw(imagem)
    titulo = ImageFont.load_default(size=26)
    rotulo = ImageFont.load_default(size=16)
    fonte = ImageFont.load_default(size=28)
    desenho.text(
        (40, 50),
        f"CARTAO SINTETICO DE TESTE · {subtipo.value} · AMOSTRA {amostra:04d}",
        fill=(40, 40, 90),
        font=titulo,
    )

    caixas: dict[str, tuple[int, int, int, int]] = {}
    palavras: list[PalavraGravada] = []
    for posicao, defn in enumerate(definicoes):
        coluna, linha = divmod(posicao, 8)
        x, y = 40 + coluna * 540, 110 + linha * 75
        rotulo_txt = defn.rotulos[0] if defn.rotulos else defn.nome.upper()
        cursor_rotulo = float(x)
        for parte in rotulo_txt.split(" "):
            esq, topo, dir_, base = (
                int(v) for v in desenho.textbbox((cursor_rotulo, y), parte, font=rotulo)
            )
            desenho.text((cursor_rotulo, y), parte, fill=(90, 90, 90), font=rotulo)
            palavras.append(
                PalavraGravada(
                    id=len(palavras),
                    texto=parte,
                    caixa=(esq, topo, max(1, dir_ - esq), max(1, base - topo)),
                    confianca=0.95,
                )
            )
            cursor_rotulo = dir_ + desenho.textlength(" ", font=rotulo)
        valor = valores.get(defn.nome)
        if valor is None:
            continue
        cursor = float(x)
        caixa_campo: list[int] | None = None
        for texto in valor.split(" "):
            esq, topo, dir_, base = (
                int(v) for v in desenho.textbbox((cursor, y + 22), texto, font=fonte)
            )
            desenho.text((cursor, y + 22), texto, fill=(15, 15, 15), font=fonte)
            palavras.append(
                PalavraGravada(
                    id=len(palavras),
                    texto=texto,
                    caixa=(esq, topo, dir_ - esq, base - topo),
                    confianca=0.97,
                )
            )
            if caixa_campo is None:
                caixa_campo = [esq, topo, dir_, base]
            else:
                caixa_campo = [
                    min(caixa_campo[0], esq),
                    min(caixa_campo[1], topo),
                    max(caixa_campo[2], dir_),
                    max(caixa_campo[3], base),
                ]
            cursor = dir_ + desenho.textlength(" ", font=fonte)
        assert caixa_campo is not None
        caixas[defn.nome] = (
            caixa_campo[0],
            caixa_campo[1],
            caixa_campo[2] - caixa_campo[0],
            caixa_campo[3] - caixa_campo[1],
        )

    if qualidade == "foto":
        # Grão e leve desfoque de câmera, para não parecer um arquivo digital limpo.
        ruido = Image.effect_noise(imagem.size, 18).convert("RGB")
        imagem = Image.blend(imagem, ruido, 0.06).filter(ImageFilter.GaussianBlur(0.6))
    elif qualidade == "xerox":
        cinza = imagem.convert("L").filter(ImageFilter.GaussianBlur(0.8))
        ruido = Image.effect_noise(cinza.size, 25)
        imagem = Image.blend(cinza, ruido, 0.12).convert("RGB")
    elif qualidade == "print":
        desenho = ImageDraw.Draw(imagem)
        desenho.rectangle((0, 0, LARGURA, 34), fill=(30, 30, 30))
        desenho.text((12, 6), "12:34   tela capturada", fill=(230, 230, 230), font=rotulo)
    return _Desenho(imagem, caixas, palavras)


def _comportamento(rng: random.Random) -> str:
    sorteio = rng.random()
    if sorteio < 0.56:
        return "exato"
    if sorteio < 0.62:
        return "ocr_confusao"
    if sorteio < 0.74:
        return "critico_ilegivel"
    if sorteio < 0.84:
        return "duvidoso"
    if sorteio < 0.92:
        return "digito_trocado"
    return "alucina_ausente"


def _trocar_digito(valor: str) -> str:
    for i in range(len(valor) - 1, -1, -1):
        if valor[i].isdigit():
            return valor[:i] + str((int(valor[i]) + 1) % 10) + valor[i + 1 :]
    return valor + "X"


def gerar(
    saida: Path, quantidade: int = 48, semente: int = 7, fracao_holdout: float = 0.2
) -> list[ItemGabarito]:
    """Gera `saida/imagens/*.png`, `saida/manifesto.jsonl` e `saida/gravacoes/*.json`."""
    rng = random.Random(semente)  # noqa: S311 - dados sintéticos, não criptografia
    dir_imagens = saida / "imagens"
    dir_gravacoes = saida / "gravacoes"
    dir_imagens.mkdir(parents=True, exist_ok=True)
    itens: list[ItemGabarito] = []

    for indice in range(quantidade):
        subtipo = _SUBTIPOS[indice % len(_SUBTIPOS)]
        tipo = next(t for t, subs in SUBTIPOS_POR_TIPO.items() if subtipo in subs)
        uf = rng.choice(_UFS[:8])  # poucas UFs, para o relatório mostrar células vazias
        qualidade: Qualidade = rng.choice(_QUALIDADES_SORTEIO)
        definicoes = list(campos_do_subtipo(subtipo))[:16]
        frente_sem_dados = subtipo is Subtipo.RG_ANTIGO and indice % 12 == 0
        lado = (
            Lado.FRENTE
            if frente_sem_dados
            else (Lado.PAGINA if tipo is TipoDocumento.CTPS else Lado.VERSO)
        )

        valores: dict[str, str] = {}
        if not frente_sem_dados:
            for defn in definicoes:
                if defn.critico or rng.random() < 0.6:
                    valores[defn.nome] = _valor(rng, defn, uf)
            _coerentes(rng, subtipo, valores)

        desenho = _desenhar(subtipo, valores, definicoes, qualidade, indice)
        buffer = io.BytesIO()
        desenho.imagem.save(buffer, format="PNG")
        conteudo = buffer.getvalue()
        sha = hashlib.sha256(conteudo).hexdigest()
        arquivo = f"imagens/sint_{indice:04d}_{subtipo.value.lower()}.png"
        (saida / arquivo).write_bytes(conteudo)

        campos_gabarito = {
            d.nome: ValorGabarito(valor=valores[d.nome], caixa=desenho.caixas[d.nome])
            if d.nome in valores
            else ValorGabarito(status="AUSENTE")
            for d in definicoes
        }

        comportamento = "frente" if frente_sem_dados else _comportamento(rng)
        criticos = [d.nome for d in definicoes if d.critico]
        ausentes = [d.nome for d in definicoes if d.nome not in valores]
        alvo = rng.choice(criticos) if criticos else None
        lidos: dict[str, CampoGravado] = {}
        for defn in definicoes:
            valor = valores.get(defn.nome)
            rotulo = defn.rotulos[0] if defn.rotulos else ""
            if valor is None:
                lidos[defn.nome] = CampoGravado(status=StatusLeitura.AUSENTE, rotulo_visto=rotulo)
            else:
                lidos[defn.nome] = CampoGravado(
                    status=StatusLeitura.LEGIVEL, rotulo_visto=rotulo, texto_literal=valor
                )
        if comportamento == "critico_ilegivel" and alvo:
            lidos[alvo] = CampoGravado(
                status=StatusLeitura.ILEGIVEL, rotulo_visto=lidos[alvo].rotulo_visto
            )
        elif comportamento == "duvidoso" and alvo:
            texto = valores[alvo]
            lidos[alvo] = CampoGravado(
                status=StatusLeitura.PARCIAL,
                rotulo_visto=lidos[alvo].rotulo_visto,
                texto_literal=texto[:-1] + "?",
            )
        elif comportamento == "digito_trocado" and alvo:
            lidos[alvo] = lidos[alvo].model_copy(
                update={"texto_literal": _trocar_digito(valores[alvo])}
            )
        elif comportamento == "ocr_confusao" and alvo and "0" in valores[alvo]:
            # O OCR lê O no lugar de 0: a ancoragem tolera em campo numérico.
            palavras_ocr = [
                p.model_copy(update={"texto": p.texto.replace("0", "O")})
                if p.texto in valores[alvo].split(" ")
                else p
                for p in desenho.palavras
            ]
            desenho = _Desenho(desenho.imagem, desenho.caixas, palavras_ocr)
        elif comportamento == "alucina_ausente" and ausentes:
            inventado = rng.choice(ausentes)
            defn = next(d for d in definicoes if d.nome == inventado)
            lidos[inventado] = CampoGravado(
                status=StatusLeitura.LEGIVEL, rotulo_visto="", texto_literal=_valor(rng, defn, uf)
            )

        releituras: dict[str, ReleituraGravada] = {}
        if comportamento in ("digito_trocado", "duvidoso") and alvo:
            releituras[alvo] = ReleituraGravada(
                status=StatusLeitura.LEGIVEL,
                rotulo_visto=lidos[alvo].rotulo_visto,
                texto_literal=valores[alvo],
                modelo="gpt-6.1-sol",
                tokens_entrada=900,
                tokens_saida=40,
                latencia_ms=round(700 + rng.random() * 600, 1),
            )

        gravacao = Gravacao(
            leitura_visual=LeituraVisualGravada(
                tipo_documento_detectado=tipo,
                subtipo=subtipo,
                uf=uf,
                lado=lado,
                qualidade_captura=_QUALIDADE_CAPTURA[qualidade],
                campos=lidos,
                tokens_entrada=3420,
                tokens_saida=600,
                # Desenho em duas etapas: classificação com a mesma imagem e prompt curto.
                classificacao_tokens_entrada=2300,
                classificacao_tokens_saida=60,
                classificacao_latencia_ms=round(500 + rng.random() * 400, 1),
                latencia_ms=round(900 + rng.random() * 1400, 1),
            ),
            leitura_texto=LeituraTextoGravada(
                palavras=desenho.palavras, latencia_ms=round(300 + rng.random() * 500, 1)
            ),
            caixas=desenho.caixas,
            releituras=releituras,
        )
        gravacoes.salvar(dir_gravacoes, sha, gravacao)
        itens.append(
            ItemGabarito(
                arquivo=arquivo,
                sha256=sha,
                tipo_documento=tipo,
                subtipo=subtipo,
                uf=uf,
                lado=lado,
                qualidade=qualidade,
                campos=campos_gabarito,
                origem=f"sintetico:{comportamento}",
            )
        )

    itens = atribuir_particoes(itens, fracao_holdout)
    escrever_manifesto(saida / "manifesto.jsonl", itens)
    return itens
