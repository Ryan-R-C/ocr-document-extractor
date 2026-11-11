"""Importador de gabarito em CSV para o manifesto JSONL. Nunca altera os originais.

Formato esperado do CSV (uma linha por imagem, UTF-8, separador `,` ou `;`):
    arquivo, tipo_documento, subtipo, uf, lado, qualidade[, particao]
    campo:<nome>   valor conferido, ou AUSENTE / ILEGIVEL
    caixa:<nome>   opcional, "x,y,largura,altura"

Se o gabarito existente estiver em outro formato, este módulo ganha um leitor novo;
o manifesto de saída é sempre o mesmo.
"""

from __future__ import annotations

import csv
from pathlib import Path

from avaliacao.gabarito import ItemGabarito, ValorGabarito, sha256_arquivo

_FIXAS = ("arquivo", "tipo_documento", "subtipo", "uf", "lado", "qualidade")


def _valor(texto: str, caixa: str | None) -> ValorGabarito | None:
    texto = texto.strip()
    if not texto:
        return None
    caixa_t = tuple(int(v) for v in caixa.split(",")) if caixa and caixa.strip() else None
    if texto.upper() in {"AUSENTE", "ILEGIVEL"}:
        return ValorGabarito.model_validate({"status": texto.upper()})
    return ValorGabarito.model_validate({"valor": texto, "caixa": caixa_t})


def importar_csv(caminho_csv: Path, dir_imagens: Path) -> tuple[list[ItemGabarito], list[str]]:
    """Lê o CSV e devolve (itens válidos, problemas). `arquivo` é relativo a `dir_imagens`."""
    itens: list[ItemGabarito] = []
    problemas: list[str] = []
    amostra = caminho_csv.read_text(encoding="utf-8-sig")
    dialeto = csv.Sniffer().sniff(amostra.splitlines()[0], delimiters=",;")
    leitor = csv.DictReader(amostra.splitlines(), dialect=dialeto)
    faltando = [c for c in _FIXAS if c not in (leitor.fieldnames or [])]
    if faltando:
        raise ValueError(f"colunas obrigatórias ausentes: {faltando}")
    for numero, linha in enumerate(leitor, start=2):
        try:
            caminho = dir_imagens / linha["arquivo"]
            if not caminho.is_file():
                raise ValueError("arquivo não encontrado")
            campos: dict[str, ValorGabarito] = {}
            for coluna, texto in linha.items():
                if coluna and coluna.startswith("campo:"):
                    nome = coluna.removeprefix("campo:")
                    valor = _valor(texto or "", linha.get(f"caixa:{nome}"))
                    if valor is not None:
                        campos[nome] = valor
            itens.append(
                ItemGabarito.model_validate(
                    {
                        "arquivo": linha["arquivo"],
                        "sha256": sha256_arquivo(caminho),
                        "tipo_documento": linha["tipo_documento"].strip(),
                        "subtipo": linha["subtipo"].strip(),
                        "uf": linha["uf"].strip().upper(),
                        "lado": linha["lado"].strip().upper(),
                        "qualidade": linha["qualidade"].strip().lower(),
                        "particao": (linha.get("particao") or "").strip() or None,
                        "campos": campos,
                        "origem": f"importado:{caminho_csv.name}",
                    }
                )
            )
        except ValueError as erro:
            # Mensagem sem o conteúdo da linha (pode ter dados pessoais).
            problemas.append(
                f"linha {numero}: {type(erro).__name__}: {str(erro).splitlines()[0][:200]}"
            )
    return itens, problemas
