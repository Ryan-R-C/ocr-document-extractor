"""Pacote de auditoria por processamento.

Conteúdo (sob `<prefixo>/<id_processamento>/`):
- `imagem_recebida.<ext>` e o hash em `auditoria.json`
- `imagem_normalizada.png`, `imagem_enviada.png` (recortada, girada e redimensionada,
  quando difere) e `recortes/<campo>.png`
- `auditoria.json`: respostas brutas dos providers com tokens e custo, ancoragens,
  releituras, regras aplicadas (id e versão), decisão final, versao_pipeline, horários,
  id_externo, tempos por etapa e stubs ativos
- `resposta.json`: código HTTP e corpo devolvido (usado pelo cache)

Contém dados pessoais: fica só no armazenamento de objetos, criptografado, com TTL.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from app.core.pipeline import ResultadoPipeline
from app.preprocess.decodificacao import EXTENSAO

ARQUIVO_RESPOSTA = "resposta.json"
ARQUIVO_AUDITORIA = "auditoria.json"


def _json(dados: Any) -> bytes:
    return json.dumps(dados, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _dc(objeto: Any) -> Any:
    if objeto is None:
        return None
    if dataclasses.is_dataclass(objeto) and not isinstance(objeto, type):
        return dataclasses.asdict(objeto)
    return objeto


def manifesto(
    resultado: ResultadoPipeline, http_status: int, corpo: dict[str, Any]
) -> dict[str, Any]:
    r = resultado.rastro
    lv, lt = r.leitura_visual, r.leitura_texto
    return {
        "id_processamento": r.id_processamento,
        "id_externo": r.id_externo,
        "tipo_documento": r.tipo_documento.value,
        "versao_pipeline": r.versao_pipeline,
        "recebido_em": r.recebido_em.isoformat(),
        "concluido_em": r.concluido_em.isoformat() if r.concluido_em else None,
        "stubs_ativos": r.stubs,
        "imagem_recebida": {
            "sha256": r.arquivo.sha256,
            "formato": r.arquivo.formato.value,
            "bytes": len(r.arquivo.conteudo),
        }
        if r.arquivo
        else None,
        "imagem_normalizada": {
            "sha256": r.imagem_normalizada.sha256,
            "largura": r.imagem_normalizada.largura,
            "altura": r.imagem_normalizada.altura,
            "paginas_pdf": r.paginas_pdf,
        }
        if r.imagem_normalizada
        else None,
        "imagem_enviada": {
            "sha256": r.imagem_preparada.sha256,
            "largura": r.imagem_preparada.largura,
            "altura": r.imagem_preparada.altura,
        }
        if r.imagem_preparada
        else None,
        "documento": {
            "encontrado": r.documento.encontrado,
            "contorno": r.documento.contorno,
            "area_relativa": r.documento.area_relativa,
            "perimetro_na_borda": r.documento.perimetro_na_borda,
        }
        if r.documento
        else None,
        "qualidade": _dc(r.qualidade),
        "captura": _dc(r.captura),
        "classificacao": {
            "subtipo": r.classificacao.subtipo.value,
            "rotacao": r.classificacao.rotacao.value,
            "modelo_documento": r.classificacao.modelo_documento,
            "metricas": _dc(r.classificacao.metricas),
        }
        if r.classificacao
        else None,
        "leitura_visual": {
            "resposta_bruta": lv.resposta_bruta,
            "metricas": _dc(lv.metricas),
        }
        if lv
        else None,
        "leitura_texto": {"resposta_bruta": lt.resposta_bruta, "metricas": _dc(lt.metricas)}
        if lt
        else None,
        "ancoragens": {k: _dc(v) for k, v in r.ancoragens.items()},
        "caixas": {k: _dc(v) for k, v in r.caixas.items()},
        "releituras": {
            k: {
                "executada": v.executada,
                "concordancia": v.concordancia,
                "motivo_nao_executada": v.motivo_nao_executada,
                "resposta_bruta": v.releitura.resposta_bruta if v.releitura else None,
                "metricas": _dc(v.releitura.metricas) if v.releitura else None,
            }
            for k, v in r.releituras.items()
        },
        "regras": {
            "versao": r.validacao.versao,
            "aplicadas": [_dc(a) for a in r.validacao.regras_aplicadas],
        }
        if r.validacao
        else None,
        "decisao": {
            "status": r.decisao.status.value,
            "bloqueios": r.decisao.bloqueios,
        }
        if r.decisao
        else None,
        "falha": {
            "codigo": resultado.falha.codigo.value,
            "motivos": [m.model_dump(mode="json") for m in resultado.falha.motivos],
        }
        if resultado.falha
        else None,
        "chamadas": [_dc(c) for c in r.chamadas],
        "custo_brl": r.custo_brl,
        "tempos_ms": r.tempos_ms,
        "http_status": http_status,
        "resposta": corpo,
    }


def montar_pacote(
    resultado: ResultadoPipeline, http_status: int, corpo: dict[str, Any]
) -> dict[str, tuple[bytes, str]]:
    """Mapa caminho relativo → (conteúdo, content-type)."""
    r = resultado.rastro
    arquivos: dict[str, tuple[bytes, str]] = {
        ARQUIVO_AUDITORIA: (_json(manifesto(resultado, http_status, corpo)), "application/json"),
        ARQUIVO_RESPOSTA: (
            _json({"http_status": http_status, "corpo": corpo}),
            "application/json",
        ),
    }
    if r.arquivo:
        arquivos[f"imagem_recebida.{EXTENSAO[r.arquivo.formato]}"] = (
            r.arquivo.conteudo,
            r.arquivo.mime,
        )
    if r.imagem_normalizada:
        arquivos["imagem_normalizada.png"] = (r.imagem_normalizada.conteudo, "image/png")
    if r.imagem_preparada and r.imagem_preparada is not r.imagem_normalizada:
        # O que de fato foi ao provider: documento recortado, girado e redimensionado.
        arquivos["imagem_enviada.png"] = (r.imagem_preparada.conteudo, "image/png")
    for campo, recorte in r.recortes.items():
        arquivos[f"recortes/{campo}.png"] = (recorte, "image/png")
    return arquivos
