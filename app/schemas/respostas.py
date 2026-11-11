"""Interpretação do JSON devolvido pelo leitor visual. Igual para todo provider.

Mesmo com saída estruturada estrita, a resposta é conferida aqui: um JSON fora do schema
vira `RespostaInvalida` (tratada como falha do provider, 502), nunca um valor aceito.
"""

from __future__ import annotations

from typing import Any

from app.api.contrato import UF, Lado, StatusLeitura, Subtipo, TipoDocumento
from app.providers.interfaces import (
    CampoLido,
    ErroProvider,
    LeituraVisual,
    MetricasChamada,
    QualidadeCaptura,
    Rotacao,
)
from app.schemas.campos import campos_do_subtipo


class RespostaInvalida(ErroProvider):
    """O JSON do provider não segue o schema pedido."""


def _enum[E](tipo: type[E], valor: Any, campo: str) -> E:
    try:
        return tipo(valor)  # type: ignore[call-arg]
    except (ValueError, TypeError):
        raise RespostaInvalida(f"valor fora da lista em {campo}") from None


def _texto(valor: Any, campo: str) -> str:
    if not isinstance(valor, str):
        raise RespostaInvalida(f"texto esperado em {campo}")
    return valor


def interpretar_campo(dados: Any, nome: str = "campo") -> CampoLido:
    if not isinstance(dados, dict):
        raise RespostaInvalida(f"objeto esperado em {nome}")
    return CampoLido(
        status=_enum(StatusLeitura, dados.get("status"), f"{nome}.status"),
        rotulo_visto=_texto(dados.get("rotulo_visto", ""), f"{nome}.rotulo_visto"),
        texto_literal=_texto(dados.get("texto_literal", ""), f"{nome}.texto_literal"),
    )


def interpretar_campos(dados: Any, subtipo: Subtipo) -> dict[str, CampoLido]:
    """Campos do subtipo. Campo que faltar fica de fora (a decisão o trata como ILEGIVEL);
    campo que não pertence ao subtipo é descartado."""
    if not isinstance(dados, dict):
        raise RespostaInvalida("objeto esperado em campos")
    nomes = {d.nome for d in campos_do_subtipo(subtipo)}
    return {nome: interpretar_campo(valor, nome) for nome, valor in dados.items() if nome in nomes}


def _classificacao(dados: dict[str, Any]) -> dict[str, Any]:
    tipo = dados.get("tipo_documento_detectado")
    return {
        "tipo_documento_detectado": None
        if tipo in (None, "NAO_IDENTIFICADO")
        else _enum(TipoDocumento, tipo, "tipo_documento_detectado"),
        "uf": _enum(UF, dados.get("uf"), "uf"),
        "lado": _enum(Lado, dados.get("lado"), "lado"),
        "qualidade_captura": _enum(
            QualidadeCaptura, dados.get("qualidade_captura"), "qualidade_captura"
        ),
        "rotacao": _enum(Rotacao, dados.get("rotacao", "0"), "rotacao"),
        "modelo_documento": _texto(dados.get("modelo_documento", ""), "modelo_documento")[:120],
    }


def interpretar_leitura(
    dados: dict[str, Any], resposta_bruta: dict[str, Any], metricas: MetricasChamada
) -> LeituraVisual:
    """Desenho único: classificação + `documento: {subtipo, campos}`."""
    documento = dados.get("documento")
    if not isinstance(documento, dict):
        raise RespostaInvalida("objeto esperado em documento")
    subtipo = _enum(Subtipo, documento.get("subtipo"), "documento.subtipo")
    return LeituraVisual(
        subtipo=subtipo,
        campos=interpretar_campos(documento.get("campos", {}), subtipo),
        resposta_bruta=resposta_bruta,
        metricas=metricas,
        **_classificacao(dados),
    )


def interpretar_classificacao(
    dados: dict[str, Any], resposta_bruta: dict[str, Any], metricas: MetricasChamada
) -> LeituraVisual:
    """Desenho em duas etapas, 1ª chamada: leitura ainda sem campos."""
    return LeituraVisual(
        subtipo=_enum(Subtipo, dados.get("subtipo"), "subtipo"),
        campos={},
        resposta_bruta=resposta_bruta,
        metricas=metricas,
        **_classificacao(dados),
    )
