"""Atualiza as tabelas de referência a partir das fontes oficiais.

    uv run tabelas municipios [--arquivo municipios.json]
    uv run tabelas cbo --arquivo "CBO2002 - Ocupacao.csv"

Sem `--arquivo`, os municípios vêm da API do IBGE. A CBO não tem download automático (o
site do MTE exige captcha): baixe o arquivo à mão e passe o caminho. Cada atualização
grava o CSV e a data de extração em `fontes.toml`, o que muda a versão das regras.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.request
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from app.regras.tabelas import DIRETORIO

URL_MUNICIPIOS = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"


def _uf_do_municipio(item: dict[str, Any]) -> str | None:
    micro = item.get("microrregiao") or {}
    uf = (micro.get("mesorregiao") or {}).get("UF")
    if uf is None:
        imediata = item.get("regiao-imediata") or {}
        uf = (imediata.get("regiao-intermediaria") or {}).get("UF")
    return uf.get("sigla") if uf else None


def municipios_de_json(dados: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    linhas = []
    for item in dados:
        uf = _uf_do_municipio(item)
        if uf is None:
            raise ValueError(f"município sem UF no arquivo do IBGE: id {item.get('id')}")
        linhas.append((uf, item["nome"], str(item["id"])))
    return sorted(linhas)


def cbo_de_csv(caminho: Path) -> list[tuple[str, str]]:
    """Arquivo do MTE: `CODIGO;TITULO`, em latin-1."""
    texto = caminho.read_bytes().decode("latin-1")
    leitor = csv.reader(texto.splitlines(), delimiter=";")
    linhas = []
    for linha in leitor:
        if len(linha) < 2:
            continue
        codigo = re.sub(r"\D", "", linha[0])
        if len(codigo) == 6:
            linhas.append((codigo, linha[1].strip()))
    if not linhas:
        raise ValueError("nenhum código de 6 dígitos no arquivo da CBO")
    return sorted(set(linhas))


def _gravar(nome: str, cabecalho: list[str], linhas: Sequence[tuple[str, ...]]) -> None:
    with (DIRETORIO / f"{nome}.csv").open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(cabecalho)
        escritor.writerows(linhas)
    _registrar(nome, date.today().isoformat())


def _registrar(secao: str, dia: str) -> None:
    caminho = DIRETORIO / "fontes.toml"
    texto = caminho.read_text(encoding="utf-8")
    inicio = texto.index(f"[{secao}]")
    fim = texto.find("\n[", inicio + 1)
    fim = len(texto) if fim == -1 else fim
    bloco = re.sub(r'extraido_em = ".*"', f'extraido_em = "{dia}"', texto[inicio:fim])
    bloco = re.sub(r'versao = ".*"', f'versao = "{dia}"', bloco)
    caminho.write_text(texto[:inicio] + bloco + texto[fim:], encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(prog="tabelas", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    p_mun = sub.add_parser("municipios", help="municípios do IBGE")
    p_mun.add_argument("--arquivo", type=Path, help="JSON já baixado da API do IBGE")
    p_cbo = sub.add_parser("cbo", help="ocupações da CBO 2002")
    p_cbo.add_argument("--arquivo", type=Path, required=True)
    args = parser.parse_args()

    if args.comando == "municipios":
        if args.arquivo:
            dados = json.loads(args.arquivo.read_text(encoding="utf-8"))
        else:
            with urllib.request.urlopen(URL_MUNICIPIOS, timeout=120) as resposta:
                dados = json.load(resposta)
        linhas = municipios_de_json(dados)
        _gravar("municipios", ["uf", "municipio", "codigo_ibge"], linhas)
        print(f"municipios.csv: {len(linhas)} municípios")
    else:
        linhas_cbo = cbo_de_csv(args.arquivo)
        _gravar("cbo", ["codigo", "titulo"], linhas_cbo)
        print(f"cbo.csv: {len(linhas_cbo)} ocupações")


if __name__ == "__main__":
    main()
