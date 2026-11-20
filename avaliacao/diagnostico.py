"""Diagnóstico da etapa que deixou passar cada erro escapado e cada alucinação em campo
crítico aprovado (Portão 2+3: "id do caso, campo, etapa que deixou passar e proposta de
correção").

Duas partes:
- `evidencias`: chamada por `metricas.registrar`, extrai do rastro só o que o diagnóstico
  precisa, sem valor de campo (invariante 10): o tipo de casamento da ancoragem efetiva,
  se a releitura acertou e se trocou o valor, o status das regras e os ids das regras
  aplicadas ao campo, e se o valor errado é o de outro campo do mesmo documento.
- `diagnosticar` e `calcular`: classificam cada campo escapado numa categoria, por
  precedência (a primeira que se aplica):

  1. CAMPO_APAGADO: alucinação numa variante do teste do campo apagado.
  2. RELEITURA_INTRODUZIU: o valor final veio da releitura (concordância sem o VLM
     principal) e a leitura principal estava certa ou vazia.
  3. DUPLA_LEITURA_CONCORDOU_ERRADO: manuscrito aprovado pela dupla leitura (2_DE_2).
  4. ANCORAGEM_TOLERANTE_CONFUSAO: a ancoragem só casou graças às confusões do OCR
     (O/0, I/1, S/5…): as chaves do VLM e do OCR diferem.
  5. ANCORAGEM_TOLERANTE_PONTUACAO: as chaves são iguais, mas espaços, pontuação ou
     acentos que mudam o valor normalizado foram ignorados.
  6. REGRAS_NAO_PEGARAM: ancoragem exata e o valor errado é o valor conferido de outro
     campo do mesmo documento (valor plausível, que só uma regra de coerência pegaria).
  7. VLM_ERROU_OCR_CONFIRMOU: ancoragem exata; o OCR tinha o mesmo texto errado.
  8. OUTRA: nada acima (por exemplo, sem evidência de ancoragem no rastro).

A sugestão de cada categoria é texto fixo; a proposta concreta de correção é escrita à mão.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.core.ancoragem import ResultadoAncoragem, StatusAncoragem, chave
from app.core.releitura import VLM, ResultadoReleitura
from app.schemas.campos import DefinicaoCampo, definicao

if TYPE_CHECKING:
    from app.core.pipeline import Rastro
    from avaliacao.gabarito import ItemGabarito
    from avaliacao.metricas import RegistroCampo


class Categoria(StrEnum):
    CAMPO_APAGADO = "CAMPO_APAGADO"
    RELEITURA_INTRODUZIU = "RELEITURA_INTRODUZIU"
    DUPLA_LEITURA_CONCORDOU_ERRADO = "DUPLA_LEITURA_CONCORDOU_ERRADO"
    ANCORAGEM_TOLERANTE_CONFUSAO = "ANCORAGEM_TOLERANTE_CONFUSAO"
    ANCORAGEM_TOLERANTE_PONTUACAO = "ANCORAGEM_TOLERANTE_PONTUACAO"
    REGRAS_NAO_PEGARAM = "REGRAS_NAO_PEGARAM"
    VLM_ERROU_OCR_CONFIRMOU = "VLM_ERROU_OCR_CONFIRMOU"
    OUTRA = "OUTRA"


SUGESTOES: dict[Categoria, str] = {
    Categoria.CAMPO_APAGADO: (
        "o VLM preencheu um campo apagado e o OCR o ancorou: rever o prompt e o schema "
        "(AUSENTE/ILEGIVEL) e a região de ancoragem do campo"
    ),
    Categoria.RELEITURA_INTRODUZIU: (
        "a releitura trocou um valor certo por um errado que o OCR confirmou: exigir que a "
        "releitura concorde com o VLM principal para substituir o valor, ou rever o recorte"
    ),
    Categoria.DUPLA_LEITURA_CONCORDOU_ERRADO: (
        "dois modelos concordaram no mesmo erro em manuscrito: usar modelos de providers "
        "diferentes na dupla leitura ou mandar este campo sempre para revisão"
    ),
    Categoria.ANCORAGEM_TOLERANTE_CONFUSAO: (
        "endurecer a tolerância da ancoragem para este tipo de campo (tirar as confusões "
        "O/0, I/1, S/5… da lista ou exigir releitura quando forem usadas)"
    ),
    Categoria.ANCORAGEM_TOLERANTE_PONTUACAO: (
        "comparar espaços, pontuação e acentos na ancoragem deste tipo de campo, porque "
        "eles mudam o valor normalizado"
    ),
    Categoria.REGRAS_NAO_PEGARAM: (
        "valor plausível de outro campo do documento: criar ou endurecer regra de "
        "coerência entre campos e rever a região de ancoragem do rótulo"
    ),
    Categoria.VLM_ERROU_OCR_CONFIRMOU: (
        "VLM e OCR leram o mesmo texto errado: conferir o gabarito e a imagem; se o erro "
        "for real, exigir releitura independente ou revisão para esta qualidade/campo"
    ),
    Categoria.OUTRA: "analisar o pacote de auditoria do caso à mão",
}


class Casamento(StrEnum):
    """Como o texto aprovado casou com as palavras do OCR na ancoragem efetiva."""

    EXATO = "EXATO"  # mesmas letras e dígitos; o resto não muda o valor normalizado
    PONTUACAO = "PONTUACAO"  # mesmas letras e dígitos; espaço/pontuação/acento muda o valor
    CONFUSAO = "CONFUSAO"  # letras e dígitos diferentes: casou pelas confusões do OCR
    CORINGA = "CORINGA"  # `?` no texto do VLM
    INDETERMINADO = "INDETERMINADO"  # sem palavras do OCR ou tamanhos que não batem


def casamento(
    defn: DefinicaoCampo,
    texto_vlm: str,
    textos_ocr: Sequence[str],
    rotulos: Sequence[str] = (),
) -> Casamento:
    """Tipo de casamento entre o texto aprovado e as palavras que o ancoraram.

    Recalcula, sem mudar a ancoragem, qual tolerância foi usada: a comparação da
    ancoragem é feita por chave (só letras e dígitos, sem acento) e com as confusões."""
    if not textos_ocr or not texto_vlm.strip():
        return Casamento.INDETERMINADO
    texto_ocr = " ".join(textos_ocr)
    kv = chave(texto_vlm, manter_coringa=True)
    ko = chave(texto_ocr)
    if "?" in kv:
        return Casamento.CORINGA
    if len(ko) > len(kv):
        # Rótulo grudado no valor (`CPF:123…` numa palavra só).
        for rotulo in rotulos:
            rk = chave(rotulo)
            if rk and ko.startswith(rk) and len(ko) - len(rk) == len(kv):
                ko = ko[len(rk) :]
                texto_ocr = ko
                break
    if kv == ko:
        nv, no = defn.normalizar(texto_vlm), defn.normalizar(texto_ocr)
        if nv is not None and no is not None and nv != no:
            return Casamento.PONTUACAO
        return Casamento.EXATO
    if len(kv) == len(ko):
        return Casamento.CONFUSAO
    return Casamento.INDETERMINADO


def _alvo(defn: DefinicaoCampo, valor: str | None) -> str:
    return defn.normalizar(valor) or (valor or "").strip().upper()


def evidencias(
    item: ItemGabarito,
    nome: str,
    rastro: Rastro,
    valor_normalizado_final: str | None,
) -> dict[str, Any]:
    """Campos do `RegistroCampo` usados pelo diagnóstico. Nenhum valor de campo sai daqui:
    só booleanos, status, ids de regra e o tipo de casamento."""
    defn = definicao(item.subtipo, nome)
    assert defn is not None
    esperado = item.campos[nome]
    alvo = _alvo(defn, esperado.valor) if esperado.tem_valor else None

    anc: ResultadoAncoragem | None = rastro.ancoragens.get(nome)
    rel: ResultadoReleitura | None = rastro.releituras.get(nome)
    lido = rastro.leitura_visual.campos.get(nome) if rastro.leitura_visual else None

    # Ancoragem efetiva: a da votação da releitura, quando o OCR confirmou o valor dela.
    texto = lido.texto_literal if lido else ""
    anc_efetiva = anc
    if rel is not None and rel.ocr_concorda and rel.ancoragem_final is not None:
        anc_efetiva = rel.ancoragem_final
        texto = rel.valor_final or texto
    tipo_casamento: str | None = None
    if (
        anc_efetiva is not None
        and anc_efetiva.status is StatusAncoragem.ANCORADO
        and rastro.leitura_texto is not None
    ):
        por_id = {p.id: p.texto for p in rastro.leitura_texto.palavras}
        textos = [por_id[i] for i in anc_efetiva.palavras_ids if i in por_id]
        rotulos = [r for r in ((lido.rotulo_visto if lido else ""), *defn.rotulos) if r]
        tipo_casamento = casamento(defn, texto, textos, rotulos).value

    correto_releitura: bool | None = None
    trocou = False
    if rel is not None and rel.releitura is not None:
        if alvo is not None:
            correto_releitura = defn.normalizar(rel.releitura.campo.texto_literal) == alvo
        trocou = (
            rel.valor_final is not None
            and (rel.ocr_concorda or rel.dupla_leitura)
            and VLM not in rel.leituras_concordantes
        )

    validacao = rastro.validacao
    status_validacao = (
        validacao.por_campo[nome].value
        if validacao is not None and nome in validacao.por_campo
        else None
    )
    regras = (
        tuple(dict.fromkeys(r.id for r in validacao.regras_aplicadas if r.campo == nome))
        if validacao is not None
        else ()
    )

    return {
        "casamento_ancoragem": tipo_casamento,
        "correto_releitura": correto_releitura,
        "releitura_trocou_valor": trocou,
        "status_validacao": status_validacao,
        "regras": regras,
        "valor_de_outro_campo": _valor_de_outro_campo(item, nome, valor_normalizado_final, alvo),
    }


def _valor_de_outro_campo(
    item: ItemGabarito, nome: str, final: str | None, alvo: str | None
) -> bool:
    """O valor devolvido (errado) é o valor conferido de outro campo do mesmo documento?"""
    if final is None or final == alvo:
        return False
    for outro, esperado in item.campos.items():
        if outro == nome or not esperado.tem_valor:
            continue
        d = definicao(item.subtipo, outro)
        if d is not None and _alvo(d, esperado.valor) == final:
            return True
    return False


# --- Classificação ------------------------------------------------------------------------------


def diagnosticar(c: RegistroCampo) -> Categoria:
    """Categoria de um campo crítico aprovado com erro ou alucinação (ver o docstring)."""
    if c.campo_apagado and c.alucinacao:
        return Categoria.CAMPO_APAGADO
    if c.releitura_trocou_valor and (c.correto_vlm is True or not c.vlm_devolveu_valor):
        return Categoria.RELEITURA_INTRODUZIU
    if c.manuscrito:
        if c.concordancia == "2_DE_2":
            return Categoria.DUPLA_LEITURA_CONCORDOU_ERRADO
        return Categoria.OUTRA
    if c.casamento_ancoragem == Casamento.CONFUSAO:
        return Categoria.ANCORAGEM_TOLERANTE_CONFUSAO
    if c.casamento_ancoragem == Casamento.PONTUACAO:
        return Categoria.ANCORAGEM_TOLERANTE_PONTUACAO
    if c.casamento_ancoragem == Casamento.EXATO:
        if c.valor_de_outro_campo:
            return Categoria.REGRAS_NAO_PEGARAM
        return Categoria.VLM_ERROU_OCR_CONFIRMOU
    return Categoria.OUTRA


def calcular(campos: Sequence[RegistroCampo]) -> dict[str, Any]:
    """Casos diagnosticados e contagem por categoria (todas as categorias, mesmo zeradas)."""
    casos: list[dict[str, Any]] = []
    for c in campos:
        if not c.erro_escapado:
            continue
        categoria = diagnosticar(c)
        casos.append(
            {
                "id": c.id,
                "campo": c.campo,
                "subtipo": c.subtipo,
                "uf": c.uf,
                "qualidade": c.qualidade,
                "tipo": "alucinacao" if c.alucinacao else "valor_errado",
                "categoria": categoria.value,
                "sugestao": SUGESTOES[categoria],
                "casamento_ancoragem": c.casamento_ancoragem,
                "status_validacao": c.status_validacao,
                "regras": list(c.regras),
            }
        )
    contagem: Mapping[str, int] = Counter(c["categoria"] for c in casos)
    return {
        "casos": casos,
        "por_categoria": {cat.value: contagem.get(cat.value, 0) for cat in Categoria},
    }
