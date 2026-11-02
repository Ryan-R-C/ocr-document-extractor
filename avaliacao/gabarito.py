"""Manifesto do gabarito (JSONL, uma linha por imagem).

Exemplo de linha:
    {"arquivo": "rg/0001_verso.jpg", "sha256": "…", "tipo_documento": "IDENTITY_DOCUMENT",
     "subtipo": "RG_ANTIGO", "uf": "SP", "lado": "VERSO", "qualidade": "foto",
     "particao": "ajuste",
     "campos": {"cpf": {"valor": "12345678909", "caixa": [10, 20, 200, 30]},
                "data_expedicao": {"status": "ILEGIVEL"}}}

- `arquivo` é relativo ao diretório do manifesto.
- `valor` é o valor conferido; é comparado com `valor_normalizado` depois de passar pelo
  mesmo normalizador do campo (então `123.456.789-09` e `12345678909` são equivalentes).
- `status` AUSENTE ou ILEGIVEL: qualquer valor devolvido nesse campo é alucinação.
- `caixa` (x, y, largura, altura, em pixels da imagem com orientação EXIF aplicada) é
  opcional; serve ao teste do campo apagado.
- `particao` é atribuída uma única vez (`gabarito particionar`) e nunca muda.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.api.contrato import UF, Lado, Subtipo, TipoDocumento
from app.schemas.campos import SUBTIPOS_POR_TIPO, campos_do_subtipo, definicao

Qualidade = Literal["foto", "xerox", "print"]
Particao = Literal["ajuste", "holdout"]
QUALIDADES: tuple[Qualidade, ...] = ("foto", "xerox", "print")


class ValorGabarito(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valor: str | None = None
    status: Literal["LEGIVEL", "AUSENTE", "ILEGIVEL"] = "LEGIVEL"
    caixa: tuple[int, int, int, int] | None = None

    @model_validator(mode="after")
    def _coerente(self) -> Self:
        if self.status == "LEGIVEL" and not (self.valor and self.valor.strip()):
            raise ValueError("campo LEGIVEL precisa de valor conferido")
        if self.status != "LEGIVEL" and self.valor is not None:
            raise ValueError(f"campo {self.status} não pode ter valor")
        return self

    @property
    def tem_valor(self) -> bool:
        return self.status == "LEGIVEL"


class ItemGabarito(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arquivo: str
    sha256: str
    tipo_documento: TipoDocumento
    subtipo: Subtipo
    uf: UF
    lado: Lado
    qualidade: Qualidade
    particao: Particao | None = None
    campos: dict[str, ValorGabarito]
    origem: str | None = None
    # Preenchidos só nas variantes geradas pelo teste do campo apagado.
    variante_de: str | None = None
    campo_apagado: str | None = None

    @model_validator(mode="after")
    def _coerente(self) -> Self:
        if self.subtipo not in SUBTIPOS_POR_TIPO[self.tipo_documento]:
            raise ValueError(f"subtipo {self.subtipo} não pertence a {self.tipo_documento}")
        desconhecidos = [n for n in self.campos if definicao(self.subtipo, n) is None]
        if desconhecidos:
            raise ValueError(f"campos fora do subtipo {self.subtipo}: {desconhecidos}")
        return self

    @property
    def estrato(self) -> tuple[str, str, str]:
        return (self.subtipo.value, self.uf.value, self.qualidade)

    def campos_criticos(self) -> list[str]:
        return [d.nome for d in campos_do_subtipo(self.subtipo) if d.critico]


def sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_manifesto(caminho: Path) -> list[ItemGabarito]:
    itens: list[ItemGabarito] = []
    with caminho.open(encoding="utf-8") as arquivo:
        for numero, linha in enumerate(arquivo, start=1):
            if not linha.strip():
                continue
            try:
                itens.append(ItemGabarito.model_validate_json(linha))
            except ValueError as erro:
                raise ValueError(f"{caminho.name}:{numero}: {erro}") from None
    return itens


def escrever_manifesto(caminho: Path, itens: Iterable[ItemGabarito]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        for item in itens:
            dados = item.model_dump(mode="json", exclude_none=True)
            arquivo.write(json.dumps(dados, ensure_ascii=False, sort_keys=True) + "\n")
    temporario.replace(caminho)


def verificar_arquivos(diretorio: Path, itens: Iterable[ItemGabarito]) -> list[str]:
    """Confere existência, hash e duplicidade de cada arquivo. Devolve a lista de problemas."""
    problemas: list[str] = []
    vistos: dict[str, str] = {}
    for item in itens:
        if item.sha256 in vistos:
            problemas.append(f"{item.arquivo}: mesma imagem que {vistos[item.sha256]}")
        vistos.setdefault(item.sha256, item.arquivo)
        caminho = diretorio / item.arquivo
        if not caminho.is_file():
            problemas.append(f"{item.arquivo}: arquivo não encontrado")
        elif sha256_arquivo(caminho) != item.sha256:
            problemas.append(f"{item.arquivo}: sha256 diferente do manifesto")
    return problemas
