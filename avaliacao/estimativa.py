"""Estimativa de custo de uma rodada do avaliador, sem chamar provider, e orçamento.

`estimar` conta as imagens da partição e as variantes do campo apagado, prevê as chamadas
por etapa conforme o desenho da rodada e multiplica pelos tokens estimados por chamada e
pelos preços de `PRECOS_MODELOS` (com o fator do modo Batch/Flex e o câmbio da config).

Base dos tokens por chamada, nesta ordem:
1. `relatorio.json` de uma rodada anterior (`[estimativa] base` ou `--base`): médias medidas
   por etapa, quando o modelo da etapa é o mesmo; a taxa de releitura também sai dela.
2. Senão, o plano (`PLANO_TOKENS_*` em `avaliacao/metricas.py`) com dois ajustes locais:
   os tokens de imagem vêm da regra do modelo (`app/providers/openai/imagem.py`) sobre o
   tamanho de cada imagem depois do limite de resolução, e os de instruções e schema
   não ficam abaixo do tamanho real do prompt + schema enviados (caracteres / 4). O schema
   do desenho único com todos os subtipos de identidade passa bem dos 1.500 do plano.

Faixa: a baixa usa os tokens × (1 − folga), metade da taxa de releitura e só as variantes
certas; a alta usa os tokens × (1 + folga) (ou o p95 medido, se maior), o dobro da taxa
de releitura e todas as variantes que o OCR pode localizar. O orçamento é comparado
com a alta.

Orçamento durante a execução: `GuardaOrcamento` envolve o leitor visual e soma o custo
medido de cada chamada; depois que o acumulado passa do limite, toda chamada nova é
recusada com `OrcamentoEsgotado` antes de sair. As chamadas que já estavam em voo
terminam e são cobradas: o excedente aceito é o dessas chamadas (até `concorrencia`
documentos ao mesmo tempo, cada um com uma leitura ou com as releituras em paralelo).
No modo Batch todos os documentos estão em voo ao mesmo tempo; lá a proteção é o
bloqueio prévio pela estimativa alta, e a guarda só corta as etapas seguintes.
Chamadas que falham (ErroProvider) não trazem métricas e não entram na soma.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.prompts import carregar as carregar_prompt
from app.providers.custo import calcular_custo
from app.providers.interfaces import LeitorVisual, ModoChamada, PedidoVisual, RespostaVisual
from app.providers.openai.imagem import estimar_tokens_imagem
from app.schemas.campos import (
    schema_classificacao,
    schema_extracao,
    schema_leitura,
    schema_releitura,
)
from avaliacao.gabarito import ItemGabarito, Particao, ler_manifesto
from avaliacao.metricas import PLANO_TOKENS_ENTRADA, PLANO_TOKENS_IMAGEM, PLANO_TOKENS_SAIDA

if TYPE_CHECKING:
    from avaliacao.executor import ConfigAvaliacao

# Plano, "Custos estimados": 30% dos documentos com dois campos relidos = 0,6 releitura
# por documento. A faixa vai da metade ao dobro (ancoragem ainda não calibrada no gabarito
# real pode reler mais; a releitura é um recorte pequeno e pesa pouco no total).
TAXA_RELEITURA_PADRAO = 0.6
FATOR_FAIXA_RELEITURA = 2.0
FOLGA_TOKENS_PADRAO = 0.25
# Instruções e schema da leitura principal no plano (3.420 − 1.920).
PLANO_TOKENS_TEXTO = PLANO_TOKENS_ENTRADA - PLANO_TOKENS_IMAGEM
# Contagem local de tokens de texto, sem tokenizador do provider: ~4 caracteres por token.
CARACTERES_POR_TOKEN = 4
# Sem medida: saída da classificação (subtipo, UF, lado, rotação, captura) e da releitura
# (um campo). A da leitura principal é a do plano.
TOKENS_SAIDA_CLASSIFICACAO = 200
TOKENS_SAIDA_RELEITURA = 150
# Recorte típico da releitura: campo de ~400 × 40 px com margem, ampliado até 96+ px de
# altura (ver `recortar` na esteira); arredondado para cima.
RECORTE_TIPICO_PX = (1000, 128)

ETAPA_LEITURA = "leitura_visual"
ETAPA_CLASSIFICACAO = "classificacao"
ETAPA_EXTRACAO = "extracao"
ETAPA_RELEITURA = "releitura"


class ConfigEstimativa(BaseModel):
    """Seção `[estimativa]` da rodada."""

    model_config = ConfigDict(extra="forbid")

    # relatorio.json de uma rodada anterior com chamadas medidas (relativo ao arquivo .toml).
    base: Path | None = None
    # Releituras por documento; vazio = medida da base ou o padrão do plano (0,6).
    taxa_releitura: float | None = Field(default=None, ge=0)
    folga_tokens: float = Field(default=FOLGA_TOKENS_PADRAO, ge=0, lt=1)


class OrcamentoInsuficiente(RuntimeError):
    """A estimativa alta passa do orçamento: a rodada não começa."""


class OrcamentoEsgotado(RuntimeError):
    """O custo medido já passou do orçamento: nenhuma chamada nova sai."""


@dataclass(frozen=True)
class Faixa:
    baixa: float
    central: float
    alta: float

    def __mul__(self, fator: float) -> Faixa:
        return Faixa(self.baixa * fator, self.central * fator, self.alta * fator)


@dataclass(frozen=True)
class EstimativaEtapa:
    etapa: str
    modelo: str
    chamadas: Faixa
    entrada_por_chamada: float  # central
    saida_por_chamada: float  # central
    fonte: str
    usd: Faixa
    brl: Faixa


@dataclass(frozen=True)
class Estimativa:
    nome: str
    particao: str
    modo: str
    desenho: str
    cobra: bool
    imagens: int
    pdfs: int
    variantes: Faixa
    documentos: Faixa
    taxa_releitura: Faixa
    fonte_taxa_releitura: str
    base: str | None
    etapas: list[EstimativaEtapa]
    usd: Faixa
    brl: Faixa
    usd_por_documento: Faixa
    brl_por_documento: Faixa
    cambio_usd_brl: float
    orcamento_brl: float | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def cabe_no_orcamento(self) -> bool | None:
        if self.orcamento_brl is None:
            return None
        return not self.cobra or self.brl.alta <= self.orcamento_brl

    def para_dict(self) -> dict[str, Any]:
        return asdict(self) | {"cabe_no_orcamento": self.cabe_no_orcamento}

    def texto(self) -> str:
        return formatar(self)


# --- Base medida -------------------------------------------------------------------------------


@dataclass(frozen=True)
class EtapaMedida:
    modelos: tuple[str, ...]
    chamadas: int
    entrada_media: float
    entrada_p95: float
    saida_media: float


@dataclass(frozen=True)
class BaseMedida:
    caminho: str
    etapas: dict[str, EtapaMedida]
    documentos: int

    @classmethod
    def carregar(cls, caminho: Path) -> BaseMedida:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        execucao = dados.get("execucao") or {}
        tokens = (dados.get("metricas") or {}).get("tokens") or {}
        etapas: dict[str, EtapaMedida] = {}
        for etapa, t in tokens.items():
            if not t or not t.get("chamadas") or t.get("entrada_media") is None:
                continue
            etapas[etapa] = EtapaMedida(
                modelos=tuple(t.get("modelos") or ()),
                chamadas=int(t["chamadas"]),
                entrada_media=float(t["entrada_media"]),
                entrada_p95=float(t.get("entrada_p95") or t["entrada_media"]),
                saida_media=float(t.get("saida_media") or 0.0),
            )
        documentos = int(execucao.get("itens") or 0) + int(execucao.get("variantes") or 0)
        return cls(caminho=caminho.name, etapas=etapas, documentos=documentos)

    def etapa(self, etapa: str, modelo: str) -> EtapaMedida | None:
        medida = self.etapas.get(etapa)
        if medida is None or modelo not in medida.modelos:
            return None
        return medida


# --- Contagens ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Unidade:
    """Uma imagem da partição e quantas variantes do campo apagado ela gera (mín./máx.)."""

    item: ItemGabarito
    largura: int
    altura: int
    variantes_min: int
    variantes_max: int


def _variantes(item: ItemGabarito, config: ConfigAvaliacao) -> tuple[int, int]:
    """Mesma escolha de `campo_apagado.gerar_variantes`, sem gerar imagem: com caixa no
    gabarito a variante é certa; sem caixa ela depende do OCR achar o valor (máximo)."""
    ca = config.campo_apagado
    if not ca.ativo or item.arquivo.lower().endswith(".pdf"):
        return 0, 0
    candidatos = [
        n for n in item.campos_criticos() if n in item.campos and item.campos[n].tem_valor
    ]
    com_caixa = [n for n in candidatos if item.campos[n].caixa]
    localizaveis = candidatos if ca.usar_ocr_sem_caixa else com_caixa
    limite = ca.campos_por_item or None

    def quantos(n: int) -> int:
        return (n if limite is None else min(limite, n)) * len(ca.metodos)

    return quantos(len(com_caixa)), quantos(len(localizaveis))


def _dimensoes(caminho: Path, item: ItemGabarito, settings: Settings) -> tuple[int, int]:
    """Tamanho enviado ao provider: o do arquivo depois do limite de resolução (nunca amplia;
    o recorte do documento só reduz). PDF ou arquivo ilegível: o limite inteiro."""
    maior, menor = settings.alvo_lado_maior_px, settings.alvo_lado_menor_px
    if item.arquivo.lower().endswith(".pdf"):
        return maior, menor
    try:
        with Image.open(caminho) as imagem:
            largura, altura = imagem.size
    except OSError:
        return maior, menor
    limite = (maior, menor) if largura >= altura else (menor, maior)
    fator = min(1.0, limite[0] / largura, limite[1] / altura)
    return max(1, round(largura * fator)), max(1, round(altura * fator))


def _tokens_texto(*partes: str) -> int:
    return math.ceil(sum(len(p) for p in partes) / CARACTERES_POR_TOKEN)


def _texto_leitura(item: ItemGabarito) -> int:
    schema = json.dumps(schema_leitura(item.tipo_documento), ensure_ascii=False)
    return max(PLANO_TOKENS_TEXTO, _tokens_texto(carregar_prompt("leitura_visual"), schema))


def _texto_extracao(item: ItemGabarito) -> int:
    schema = json.dumps(schema_extracao(item.subtipo), ensure_ascii=False)
    return max(PLANO_TOKENS_TEXTO, _tokens_texto(carregar_prompt("extracao"), schema))


def _texto_classificacao(item: ItemGabarito) -> int:
    schema = json.dumps(schema_classificacao(item.tipo_documento), ensure_ascii=False)
    return _tokens_texto(carregar_prompt("classificacao"), schema)


def _texto_releitura() -> int:
    schema = json.dumps(schema_releitura(), ensure_ascii=False)
    return _tokens_texto(carregar_prompt("releitura"), schema)


def cobra(settings: Settings) -> bool:
    """Provider fake não custa nada: sem estimativa de custo e sem orçamento."""
    return settings.leitor_visual_provider != "fake"


def _tem_releitura(settings: Settings) -> bool:
    # Sem leitor de texto, o localizador por OCR não tem caixas: nada é recortado e relido.
    return not (
        settings.leitor_texto_provider == "nenhum" and settings.localizador_provider == "ocr"
    )


# --- Estimativa --------------------------------------------------------------------------------


def estimar(
    config: ConfigAvaliacao,
    particao: Particao,
    settings: Settings,
    orcamento_brl: float | None = None,
) -> Estimativa:
    """Estimativa da rodada sem chamar provider (só lê o manifesto e o tamanho das imagens)."""
    opcoes = config.estimativa
    folga = opcoes.folga_tokens
    avisos: list[str] = []
    base = BaseMedida.carregar(opcoes.base) if opcoes.base else None

    todos = ler_manifesto(config.gabarito)
    sem_particao = [i.arquivo for i in todos if i.particao is None]
    if sem_particao:
        raise ValueError(
            f"{len(sem_particao)} itens sem partição; rode `gabarito particionar` antes"
        )
    itens = [i for i in todos if i.particao == particao and i.variante_de is None]
    dir_gabarito = config.gabarito.parent
    unidades = []
    for item in itens:
        largura, altura = _dimensoes(dir_gabarito / item.arquivo, item, settings)
        vmin, vmax = _variantes(item, config)
        unidades.append(_Unidade(item, largura, altura, vmin, vmax))

    docs_min = sum(1 + u.variantes_min for u in unidades)
    docs_max = sum(1 + u.variantes_max for u in unidades)
    documentos = Faixa(docs_min, docs_max, docs_max)
    variantes = Faixa(docs_min - len(unidades), docs_max - len(unidades), docs_max - len(unidades))
    if docs_max > docs_min:
        avisos.append(
            f"{int(variantes.alta - variantes.baixa)} variantes dependem do OCR localizar o "
            "valor (sem caixa no gabarito); a central e a alta contam todas"
        )

    def por_documento(u: _Unidade) -> Faixa:
        return Faixa(1 + u.variantes_min, 1 + u.variantes_max, 1 + u.variantes_max)

    etapas: list[EstimativaEtapa] = []
    pagante = cobra(settings)

    def _etapa(
        etapa: str, modelo: str, chamadas: Faixa, entrada: Faixa, saida: Faixa, fonte: str
    ) -> EstimativaEtapa:
        return _precificar(
            etapa, modelo, chamadas, entrada, saida, fonte, settings, config.modo, pagante
        )

    def etapa_imagem(
        etapa: str,
        modelo: str,
        texto: Callable[[ItemGabarito], int],
        saida_plano: int,
    ) -> None:
        """Etapa com uma chamada por documento sobre a imagem inteira."""
        medida = base.etapa(etapa, modelo) if base else None
        if medida is not None:
            entrada = Faixa(
                medida.entrada_media * (1 - folga),
                medida.entrada_media,
                max(medida.entrada_media * (1 + folga), medida.entrada_p95),
            )
            saida = Faixa(
                medida.saida_media * (1 - folga),
                medida.saida_media,
                medida.saida_media * (1 + folga),
            )
            entrada_total = documentos.central * entrada.central
            totais_entrada = Faixa(docs_min * entrada.baixa, entrada_total, docs_max * entrada.alta)
            totais_saida = Faixa(
                docs_min * saida.baixa, documentos.central * saida.central, docs_max * saida.alta
            )
            fonte = f"medida ({base.caminho if base else ''})"
        else:
            regra = settings.regra_imagem(modelo)
            soma_entrada = Faixa(0.0, 0.0, 0.0)
            for u in unidades:
                tokens = estimar_tokens_imagem(u.largura, u.altura, regra) + texto(u.item)
                n = por_documento(u)
                soma_entrada = Faixa(
                    soma_entrada.baixa + n.baixa * tokens * (1 - folga),
                    soma_entrada.central + n.central * tokens,
                    soma_entrada.alta + n.alta * tokens * (1 + folga),
                )
            totais_entrada = soma_entrada
            totais_saida = Faixa(
                docs_min * saida_plano * (1 - folga),
                documentos.central * saida_plano,
                docs_max * saida_plano * (1 + folga),
            )
            fonte = "plano + imagem e schema locais"
            if base is not None:
                avisos.append(f"{etapa}: sem medida na base para {modelo}; usado o plano")
        etapas.append(_etapa(etapa, modelo, documentos, totais_entrada, totais_saida, fonte))

    if settings.leitor_visual_desenho == "duas_etapas":
        etapa_imagem(
            ETAPA_CLASSIFICACAO,
            settings.leitor_visual_modelo_classificacao,
            _texto_classificacao,
            TOKENS_SAIDA_CLASSIFICACAO,
        )
        etapa_imagem(
            ETAPA_EXTRACAO, settings.leitor_visual_modelo, _texto_extracao, PLANO_TOKENS_SAIDA
        )
    else:
        etapa_imagem(
            ETAPA_LEITURA, settings.leitor_visual_modelo, _texto_leitura, PLANO_TOKENS_SAIDA
        )

    # Releitura: recorte de um campo pelo modelo de escalada.
    modelo_escalada = settings.leitor_visual_modelo_escalada
    medida_rel = base.etapa(ETAPA_RELEITURA, modelo_escalada) if base else None
    if not _tem_releitura(settings):
        taxa, fonte_taxa = 0.0, "sem leitor de texto: nada ancora nem é recortado"
    elif opcoes.taxa_releitura is not None:
        taxa, fonte_taxa = opcoes.taxa_releitura, "configurada"
    elif base is not None and ETAPA_RELEITURA in base.etapas and base.documentos:
        taxa = base.etapas[ETAPA_RELEITURA].chamadas / base.documentos
        fonte_taxa = f"medida ({base.caminho})"
    else:
        taxa, fonte_taxa = TAXA_RELEITURA_PADRAO, "plano (30% dos documentos × 2 campos)"
    taxas = Faixa(taxa / FATOR_FAIXA_RELEITURA, taxa, taxa * FATOR_FAIXA_RELEITURA)
    if taxa > 0:
        chamadas = Faixa(
            docs_min * taxas.baixa, documentos.central * taxas.central, docs_max * taxas.alta
        )
        if medida_rel is not None:
            entrada = Faixa(
                medida_rel.entrada_media * (1 - folga),
                medida_rel.entrada_media,
                max(medida_rel.entrada_media * (1 + folga), medida_rel.entrada_p95),
            )
            saida = Faixa(
                medida_rel.saida_media * (1 - folga),
                medida_rel.saida_media,
                medida_rel.saida_media * (1 + folga),
            )
            fonte = f"medida ({base.caminho if base else ''})"
        else:
            tokens = (
                estimar_tokens_imagem(*RECORTE_TIPICO_PX, settings.regra_imagem(modelo_escalada))
                + _texto_releitura()
            )
            entrada = Faixa(tokens * (1 - folga), tokens, tokens * (1 + folga))
            s = TOKENS_SAIDA_RELEITURA
            saida = Faixa(s * (1 - folga), s, s * (1 + folga))
            fonte = "recorte típico + schema locais"
        etapas.append(
            _etapa(
                ETAPA_RELEITURA,
                modelo_escalada,
                chamadas,
                Faixa(
                    chamadas.baixa * entrada.baixa,
                    chamadas.central * entrada.central,
                    chamadas.alta * entrada.alta,
                ),
                Faixa(
                    chamadas.baixa * saida.baixa,
                    chamadas.central * saida.central,
                    chamadas.alta * saida.alta,
                ),
                fonte,
            )
        )

    usd = _somar([e.usd for e in etapas])
    brl = _somar([e.brl for e in etapas])

    if not pagante:
        avisos.append("provider fake: custo zero, orçamento não se aplica")
    esforco = settings.leitor_visual_esforco_raciocinio
    if pagante and esforco not in (None, "none") and base is None:
        avisos.append(
            f"raciocínio '{esforco}': tokens de raciocínio (cobrados como saída) não estão na "
            "estimativa do plano; use --base com uma rodada medida"
        )
    if pagante and settings.leitor_visual_detalhe == "low":
        avisos.append("detalhe 'low': os tokens de imagem estimados ficam acima do real")
    if any(u.item.arquivo.lower().endswith(".pdf") for u in unidades):
        avisos.append("PDF conta como uma imagem no tamanho máximo; várias páginas custam mais")

    def dividir(total: Faixa) -> Faixa:
        return Faixa(
            total.baixa / docs_min if docs_min else 0.0,
            total.central / documentos.central if documentos.central else 0.0,
            total.alta / docs_max if docs_max else 0.0,
        )

    return Estimativa(
        nome=config.nome,
        particao=particao,
        modo=config.modo.value,
        desenho=settings.leitor_visual_desenho,
        cobra=pagante,
        imagens=len(unidades),
        pdfs=sum(1 for u in unidades if u.item.arquivo.lower().endswith(".pdf")),
        variantes=variantes,
        documentos=documentos,
        taxa_releitura=taxas if taxa > 0 else Faixa(0.0, 0.0, 0.0),
        fonte_taxa_releitura=fonte_taxa,
        base=base.caminho if base else None,
        etapas=etapas,
        usd=usd,
        brl=brl,
        usd_por_documento=dividir(usd),
        brl_por_documento=dividir(brl),
        cambio_usd_brl=settings.cambio_usd_brl,
        orcamento_brl=orcamento_brl,
        avisos=avisos,
    )


def _precificar(
    etapa: str,
    modelo: str,
    chamadas: Faixa,
    entrada: Faixa,
    saida: Faixa,
    fonte: str,
    settings: Settings,
    modo: ModoChamada,
    pagante: bool,
) -> EstimativaEtapa:
    """Etapa com os totais de tokens de entrada e saída de cada ponta da faixa."""

    def custo(tokens_entrada: float, tokens_saida: float) -> tuple[float, float]:
        if not pagante:
            return 0.0, 0.0
        return calcular_custo(settings, modelo, round(tokens_entrada), round(tokens_saida), modo)

    baixa = custo(entrada.baixa, saida.baixa)
    central = custo(entrada.central, saida.central)
    alta = custo(entrada.alta, saida.alta)
    return EstimativaEtapa(
        etapa=etapa,
        modelo=modelo,
        chamadas=chamadas,
        entrada_por_chamada=entrada.central / chamadas.central if chamadas.central else 0.0,
        saida_por_chamada=saida.central / chamadas.central if chamadas.central else 0.0,
        fonte=fonte,
        usd=Faixa(baixa[0], central[0], alta[0]),
        brl=Faixa(baixa[1], central[1], alta[1]),
    )


def _somar(faixas: Sequence[Faixa]) -> Faixa:
    return Faixa(
        sum(f.baixa for f in faixas), sum(f.central for f in faixas), sum(f.alta for f in faixas)
    )


# --- Saída -------------------------------------------------------------------------------------


def _n(valor: float, casas: int = 0) -> str:
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def _faixa(f: Faixa, casas: int = 0) -> str:
    return f"{_n(f.baixa, casas)} / {_n(f.central, casas)} / {_n(f.alta, casas)}"


def formatar(e: Estimativa) -> str:
    linhas = [
        f"Estimativa · {e.nome} · partição {e.particao} · modo {e.modo} · desenho {e.desenho}",
        "Nenhum provider foi chamado. Faixas: baixa / central / alta.",
        "",
        f"Imagens da partição:          {e.imagens}" + (f" (PDF: {e.pdfs})" if e.pdfs else ""),
        f"Variantes do campo apagado:   {_faixa(e.variantes)}",
        f"Documentos processados:       {_faixa(e.documentos)}",
        f"Releituras por documento:     {_faixa(e.taxa_releitura, 2)} · {e.fonte_taxa_releitura}",
        f"Base dos tokens:              {e.base or 'plano + estimativa local (imagem e schema)'}",
        "",
        "Chamadas previstas por etapa:",
    ]
    for et in e.etapas:
        linhas += [
            f"  {et.etapa} · {et.modelo}",
            f"    chamadas {_faixa(et.chamadas)} · tokens por chamada (central): "
            f"entrada {_n(et.entrada_por_chamada)}, saída {_n(et.saida_por_chamada)} · "
            f"{et.fonte}",
            f"    custo US$ {_faixa(et.usd, 4)} · R$ {_faixa(et.brl, 2)}",
        ]
    linhas += [
        "",
        f"Custo estimado (câmbio R$ {_n(e.cambio_usd_brl, 2)}):",
        f"  por documento: US$ {_faixa(e.usd_por_documento, 5)} · "
        f"R$ {_faixa(e.brl_por_documento, 4)}",
        f"  total:         US$ {_faixa(e.usd, 4)} · R$ {_faixa(e.brl, 2)}",
    ]
    if e.orcamento_brl is not None:
        situacao = "cabe" if e.cabe_no_orcamento else "PASSA DO ORÇAMENTO"
        linhas.append(
            f"  orçamento:     R$ {_n(e.orcamento_brl, 2)} · alta R$ {_n(e.brl.alta, 2)} · "
            f"{situacao}"
        )
    if e.avisos:
        linhas += ["", "Avisos:"] + [f"  - {a}" for a in e.avisos]
    return "\n".join(linhas)


# --- Orçamento durante a execução --------------------------------------------------------------


class GuardaOrcamento:
    """Envolve o leitor visual: soma o custo medido e recusa chamada nova depois que o
    acumulado passa do limite. As chamadas em voo terminam (excedente documentado acima)."""

    def __init__(self, leitor: LeitorVisual, limite_brl: float) -> None:
        self.leitor = leitor
        self.nome = leitor.nome
        self.limite_brl = limite_brl
        self.gasto_brl = 0.0
        self.chamadas = 0
        self.recusadas = 0

    @property
    def estourado(self) -> bool:
        return self.gasto_brl > self.limite_brl

    async def ler(self, pedido: PedidoVisual) -> RespostaVisual:
        if self.estourado:
            self.recusadas += 1
            raise OrcamentoEsgotado(
                f"custo medido R$ {self.gasto_brl:.2f} passou do orçamento R$ {self.limite_brl:.2f}"
            )
        resposta = await self.leitor.ler(pedido)
        self.chamadas += 1
        self.gasto_brl += resposta.metricas.custo_brl
        return resposta


def eh_orcamento_esgotado(erro: BaseException) -> bool:
    """O erro (ou o grupo de erros de chamadas em paralelo) é só orçamento esgotado."""
    if isinstance(erro, OrcamentoEsgotado):
        return True
    if isinstance(erro, BaseExceptionGroup):
        _, resto = erro.split(OrcamentoEsgotado)
        return resto is None
    return False
