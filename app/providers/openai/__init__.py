"""Adaptador OpenAI do leitor visual (Responses API, saída estruturada estrita).

Conferido no SDK `openai` 3.44.0 instalado (tipos e docstrings gerados da especificação
oficial): `detail` aceita `high`, `low`, `auto` e `original`; `reasoning.effort` aceita
`none`, `minimal`, `low`, `medium`, `high`, `xhigh` e `max`; `service_tier="flex"`; Batch
aceita `/v1/responses`; `usage.output_tokens_details.reasoning_tokens`. A documentação web
(patches, multiplicadores, limites de `anyOf`, preços) não estava acessível nesta sessão:
esses valores ficam em configuração. Ver docs/decisoes.md.

Só este pacote importa o SDK da OpenAI (invariante 8).
"""

from __future__ import annotations

from app.providers.openai.adaptador import (
    ImagemAcimaDoLimite,
    LeitorVisualOpenAI,
    ProviderMalConfigurado,
)
from app.providers.openai.imagem import estimar_tokens_imagem, patches

__all__ = [
    "ImagemAcimaDoLimite",
    "LeitorVisualOpenAI",
    "ProviderMalConfigurado",
    "estimar_tokens_imagem",
    "patches",
]
