"""Tabelas de referência versionadas: órgãos emissores, municípios do IBGE e CBO.

Cada tabela tem fonte, data de extração e versão em `fontes.toml`. Tabela sem arquivo
fica None, e a regra que depende dela devolve NAO_VERIFICAVEL (nunca VALIDO).
"""

from __future__ import annotations

import csv
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DIRETORIO = Path(__file__).parent


def chave_nome(texto: str) -> str:
    """Maiúsculas, sem acento, só letras e dígitos (compara nomes de município)."""
    sem = "".join(
        c for c in unicodedata.normalize("NFKD", texto.upper()) if not unicodedata.combining(c)
    )
    return re.sub(r"[^0-9A-Z]", "", sem)


@dataclass(frozen=True)
class Tabelas:
    orgaos: dict[str, frozenset[str]]  # sigla -> UFs
    municipios: dict[str, frozenset[str]] | None  # UF -> chaves de nome (atuais e históricos)
    cbo: frozenset[str] | None  # códigos de 6 dígitos
    versoes: dict[str, str]

    @property
    def versao(self) -> str:
        return ";".join(f"{k}={v or 'ausente'}" for k, v in sorted(self.versoes.items()))


def _ler_csv(caminho: Path) -> list[dict[str, str]] | None:
    if not caminho.exists():
        return None
    with caminho.open(encoding="utf-8", newline="") as arquivo:
        return list(csv.DictReader(arquivo))


def carregar(diretorio: Path = DIRETORIO) -> Tabelas:
    fontes = tomllib.loads((diretorio / "fontes.toml").read_text(encoding="utf-8"))
    versoes: dict[str, str] = {}

    def linhas(nome: str) -> list[dict[str, str]] | None:
        dados = _ler_csv(diretorio / fontes[nome]["arquivo"])
        versoes[nome] = fontes[nome]["versao"] if dados is not None else ""
        return dados

    orgaos: dict[str, set[str]] = {}
    for linha in linhas("orgaos_emissores") or []:
        orgaos.setdefault(chave_nome(linha["sigla"]), set()).add(linha["uf"].strip().upper())

    municipios: dict[str, set[str]] | None = None
    atuais = linhas("municipios")
    if atuais is not None:
        municipios = {}
        for linha in atuais:
            municipios.setdefault(linha["uf"], set()).add(chave_nome(linha["municipio"]))
        for linha in linhas("municipios_historicos") or []:
            municipios.setdefault(linha["uf"], set()).add(chave_nome(linha["nome_historico"]))
    else:
        versoes["municipios_historicos"] = ""

    cbo_linhas = linhas("cbo")
    cbo = (
        frozenset(re.sub(r"\D", "", linha["codigo"]) for linha in cbo_linhas)
        if cbo_linhas is not None
        else None
    )

    return Tabelas(
        orgaos={k: frozenset(v) for k, v in orgaos.items()},
        municipios={k: frozenset(v) for k, v in municipios.items()} if municipios else None,
        cbo=cbo,
        versoes=versoes,
    )


@cache
def padrao() -> Tabelas:
    return carregar()
