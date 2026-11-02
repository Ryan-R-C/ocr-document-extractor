"""Linha de comando do avaliador e do gabarito.

avaliar --config <arquivo.toml> --particao <ajuste|holdout> [--modo padrao|batch|flex]

gabarito sintetico    --saida DIR [--quantidade N] [--semente S]
gabarito importar-csv --csv ARQ --imagens DIR --saida MANIFESTO
gabarito particionar  --manifesto ARQ [--fracao 0.2]
gabarito validar      --manifesto ARQ
gabarito estratificar --manifesto ARQ --saida DIR [--minimo 5]
gabarito apagar-campos --manifesto ARQ --saida DIR [--metodo borrar --metodo apagar]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.logs import configurar_logs
from app.providers.interfaces import ModoChamada


def main_avaliar(argv: list[str] | None = None) -> int:
    from avaliacao.executor import ConfigAvaliacao, ModoNaoImplementado, avaliar

    parser = argparse.ArgumentParser(prog="avaliar", description="Roda a esteira sobre o gabarito.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--particao", choices=["ajuste", "holdout"], required=True)
    parser.add_argument("--modo", choices=[m.value for m in ModoChamada], default=None)
    parser.add_argument("--saida", type=Path, default=None)
    args = parser.parse_args(argv)

    configurar_logs("WARNING", json=False)
    config = ConfigAvaliacao.carregar(args.config)
    if args.modo:
        config.modo = ModoChamada(args.modo)
    if args.saida:
        config.saida = args.saida
    try:
        relatorio, pasta = asyncio.run(avaliar(config, args.particao))
    except ModoNaoImplementado as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 2
    metas = relatorio["metricas"]["metas"]
    print(f"relatório: {pasta / 'relatorio.md'}")
    for nome, meta in metas.items():
        print(f"  {nome}: {meta['valor']} (meta atingida: {meta['atingida']})")
    return 0


def main_gabarito(argv: list[str] | None = None) -> int:
    from avaliacao import campo_apagado, estratificacao, sintetico
    from avaliacao.gabarito import escrever_manifesto, ler_manifesto, verificar_arquivos
    from avaliacao.importador import importar_csv
    from avaliacao.particao import FRACAO_HOLDOUT, atribuir_particoes

    parser = argparse.ArgumentParser(prog="gabarito", description="Ferramentas do gabarito.")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("sintetico", help="gera gabarito sintético com gravações para os fakes")
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--quantidade", type=int, default=48)
    p.add_argument("--semente", type=int, default=7)

    p = sub.add_parser("importar-csv", help="converte CSV em manifesto (não altera os originais)")
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--imagens", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True)

    p = sub.add_parser("particionar", help="atribui ajuste/holdout aos itens sem partição")
    p.add_argument("--manifesto", type=Path, required=True)
    p.add_argument("--fracao", type=float, default=FRACAO_HOLDOUT)

    p = sub.add_parser("validar", help="confere arquivos e hashes do manifesto")
    p.add_argument("--manifesto", type=Path, required=True)

    p = sub.add_parser("estratificar", help="relatório subtipo × UF × qualidade")
    p.add_argument("--manifesto", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--minimo", type=int, default=5)

    p = sub.add_parser("apagar-campos", help="gera variantes com campo crítico apagado")
    p.add_argument("--manifesto", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--metodo", action="append", choices=["borrar", "apagar"])

    args = parser.parse_args(argv)

    if args.comando == "sintetico":
        itens = sintetico.gerar(args.saida, args.quantidade, args.semente)
        print(f"{len(itens)} itens em {args.saida / 'manifesto.jsonl'}")
    elif args.comando == "importar-csv":
        if args.saida.resolve() == args.csv.resolve():
            print("erro: a saída não pode sobrescrever o CSV original", file=sys.stderr)
            return 2
        itens, problemas = importar_csv(args.csv, args.imagens)
        escrever_manifesto(args.saida, itens)
        print(f"{len(itens)} itens importados; {len(problemas)} problemas")
        for problema in problemas:
            print(f"  {problema}")
        return 1 if problemas else 0
    elif args.comando == "particionar":
        itens = atribuir_particoes(ler_manifesto(args.manifesto), args.fracao)
        escrever_manifesto(args.manifesto, itens)
        holdout = sum(1 for i in itens if i.particao == "holdout")
        print(f"{len(itens)} itens · holdout {holdout} · ajuste {len(itens) - holdout}")
    elif args.comando == "validar":
        problemas = verificar_arquivos(args.manifesto.parent, ler_manifesto(args.manifesto))
        for problema in problemas:
            print(problema)
        print("ok" if not problemas else f"{len(problemas)} problemas")
        return 1 if problemas else 0
    elif args.comando == "estratificar":
        relatorio = estratificacao.gerar(ler_manifesto(args.manifesto), args.minimo)
        args.saida.mkdir(parents=True, exist_ok=True)
        (args.saida / "estratificacao.json").write_text(
            json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.saida / "estratificacao.md").write_text(
            estratificacao.markdown(relatorio), encoding="utf-8"
        )
        r = relatorio["resumo"]
        print(
            f"{r['itens']} itens · células vazias {r['celulas_vazias']} · "
            f"com poucos {r['celulas_com_poucos']} · relatório em {args.saida}"
        )
    elif args.comando == "apagar-campos":
        itens = ler_manifesto(args.manifesto)
        resultado = asyncio.run(
            campo_apagado.gerar_variantes(
                itens, args.manifesto.parent, args.saida, args.metodo or ["borrar", "apagar"]
            )
        )
        escrever_manifesto(args.saida / "manifesto.jsonl", resultado.itens)
        print(
            f"{len(resultado.itens)} variantes · sem caixa {resultado.pulados_sem_caixa} · "
            f"PDF {resultado.pulados_formato}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_avaliar())
