"""Casos em revisão: exportar para conferência humana e importar a correção no gabarito.

Sem interface gráfica nesta fase. Fluxo:

1. `uv run revisao exportar --destino <dir>`: para cada caso PENDENTE (não expirado), grava
   `<dir>/<id_caso>/` com a imagem recebida, os recortes, `leituras.json` (o que a esteira
   leu: resposta, ancoragens, releituras, linhas do OCR) e `correcao.json`, um modelo em
   branco para a pessoa preencher. Os valores não vêm preenchidos, para não induzir a
   conferência; quem confere consulta `leituras.json` e a imagem.
2. A pessoa preenche `correcao.json`: em cada campo, `status` (LEGIVEL, AUSENTE ou ILEGIVEL)
   e `valor` (só em LEGIVEL), além de `qualidade` (foto, xerox ou print) e, se preciso,
   corrige `subtipo`, `uf` e `lado`.
3. `uv run revisao importar --caso <dir>/<id_caso> --gabarito <dir_gabarito>`: valida a
   correção, copia a imagem para `<gabarito>/imagens/revisao/`, acrescenta o item ao
   manifesto (partição de ajuste por padrão, origem `revisao:<id_caso>`) e marca o caso como
   CONFERIDO.

Contém dados pessoais: o destino precisa ficar fora do git (por padrão, `saidas/revisao`,
que o .gitignore exclui) e deve ser apagado depois da conferência.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.api.contrato import UF, Lado, Subtipo, TipoDocumento
from app.armazenamento import auditoria
from app.armazenamento.modelos import CasoRevisao
from app.armazenamento.objetos import Objetos
from app.armazenamento.repositorio import Repositorio
from app.schemas.campos import campos_do_subtipo
from avaliacao.gabarito import (
    ItemGabarito,
    Particao,
    Qualidade,
    ValorGabarito,
    escrever_manifesto,
    ler_manifesto,
)

ARQUIVO_CORRECAO = "correcao.json"
ARQUIVO_LEITURAS = "leituras.json"
ARQUIVO_CASO = "caso.json"


class ErroRevisao(ValueError):
    pass


class CampoCorrigido(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    valor: str | None = None


class Correcao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_caso: str
    tipo_documento: TipoDocumento
    subtipo: Subtipo
    uf: UF
    lado: Lado
    qualidade: Qualidade | None = None
    campos: dict[str, CampoCorrigido]


def _modelo_correcao(caso: CasoRevisao, manifesto: dict[str, Any]) -> dict[str, Any]:
    resposta = manifesto.get("resposta") or {}
    documento = resposta.get("documento") or {}
    subtipo = Subtipo(documento.get("subtipo") or caso.subtipo or Subtipo.NAO_IDENTIFICADO.value)
    return {
        "id_caso": caso.id,
        "tipo_documento": caso.tipo_documento,
        "subtipo": subtipo.value,
        "uf": documento.get("uf") or caso.uf or UF.NAO_IDENTIFICADO.value,
        "lado": documento.get("lado") or Lado.NAO_IDENTIFICADO.value,
        "qualidade": None,
        "campos": {d.nome: {"status": None, "valor": None} for d in campos_do_subtipo(subtipo)},
    }


def _leituras(manifesto: dict[str, Any]) -> dict[str, Any]:
    texto = manifesto.get("leitura_texto") or {}
    return {
        "versao_pipeline": manifesto.get("versao_pipeline"),
        "classificacao": manifesto.get("classificacao"),
        "resposta": manifesto.get("resposta"),
        "decisao": manifesto.get("decisao"),
        "ancoragens": manifesto.get("ancoragens"),
        "releituras": manifesto.get("releituras"),
        "linhas_ocr": (texto.get("resposta_bruta") or {}).get("linhas"),
    }


def exportar(
    repositorio: Repositorio,
    objetos: Objetos,
    destino: Path,
    agora: datetime | None = None,
    limite: int = 100,
) -> list[str]:
    """Exporta os casos pendentes; pula os que já estão no destino. Devolve os ids."""
    agora = agora or datetime.now(UTC)
    exportados: list[str] = []
    for caso in repositorio.casos_pendentes(agora, limite):
        pasta = destino / caso.id
        if (pasta / ARQUIVO_CORRECAO).exists():
            continue
        bruto = objetos.ler(f"{caso.prefixo_pacote}/{auditoria.ARQUIVO_AUDITORIA}")
        if bruto is None:
            continue
        manifesto = json.loads(bruto)
        pasta.mkdir(parents=True, exist_ok=True)
        prefixo = caso.prefixo_pacote.rstrip("/") + "/"
        for chave in objetos.listar(prefixo):
            relativo = chave[len(prefixo) :]
            if relativo.startswith("imagem_recebida.") or relativo.startswith("recortes/"):
                conteudo = objetos.ler(chave)
                if conteudo is not None:
                    alvo = pasta / relativo
                    alvo.parent.mkdir(parents=True, exist_ok=True)
                    alvo.write_bytes(conteudo)
        _gravar_json(pasta / ARQUIVO_LEITURAS, _leituras(manifesto))
        _gravar_json(
            pasta / ARQUIVO_CASO,
            {
                "id_caso": caso.id,
                "processamento_id": caso.processamento_id,
                "sha256_imagem": caso.sha256_imagem,
                "criado_em": caso.criado_em.isoformat(),
                "expira_em": caso.expira_em.isoformat() if caso.expira_em else None,
            },
        )
        _gravar_json(pasta / ARQUIVO_CORRECAO, _modelo_correcao(caso, manifesto))
        exportados.append(caso.id)
    return exportados


def _gravar_json(caminho: Path, dados: Any) -> None:
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def _validar(correcao: Correcao) -> dict[str, ValorGabarito]:
    if correcao.subtipo is Subtipo.NAO_IDENTIFICADO:
        raise ErroRevisao("subtipo NAO_IDENTIFICADO não entra no gabarito")
    if correcao.qualidade is None:
        raise ErroRevisao("preencha `qualidade` (foto, xerox ou print)")
    esperados = {d.nome for d in campos_do_subtipo(correcao.subtipo)}
    fora = sorted(set(correcao.campos) - esperados)
    if fora:
        raise ErroRevisao(f"campos fora do subtipo {correcao.subtipo.value}: {fora}")
    faltando = sorted(
        n for n in esperados if (correcao.campos.get(n) or CampoCorrigido()).status is None
    )
    if faltando:
        raise ErroRevisao(f"campos sem status: {faltando}")
    campos: dict[str, ValorGabarito] = {}
    for nome, campo in correcao.campos.items():
        try:
            campos[nome] = ValorGabarito(status=campo.status, valor=campo.valor)
        except ValidationError as erro:
            # Mensagem sem o valor (dado pessoal): só o campo e o tipo do problema.
            tipos = ", ".join(sorted({e["type"] for e in erro.errors()}))
            raise ErroRevisao(f"campo {nome}: correção inválida ({tipos})") from None
    return campos


def importar(
    pasta_caso: Path,
    dir_gabarito: Path,
    repositorio: Repositorio | None,
    particao: Particao = "ajuste",
    manifesto: Path | None = None,
    agora: datetime | None = None,
) -> ItemGabarito:
    agora = agora or datetime.now(UTC)
    try:
        correcao = Correcao.model_validate_json(
            (pasta_caso / ARQUIVO_CORRECAO).read_text(encoding="utf-8")
        )
    except ValidationError as erro:
        locais = sorted({".".join(str(p) for p in e["loc"]) for e in erro.errors()})
        raise ErroRevisao(f"correcao.json inválido em: {locais}") from None
    caso = json.loads((pasta_caso / ARQUIVO_CASO).read_text(encoding="utf-8"))
    if caso["id_caso"] != correcao.id_caso:
        raise ErroRevisao("correcao.json é de outro caso")
    campos = _validar(correcao)

    imagens = sorted(pasta_caso.glob("imagem_recebida.*"))
    if not imagens:
        raise ErroRevisao("imagem recebida ausente na pasta do caso")
    conteudo = imagens[0].read_bytes()
    sha = hashlib.sha256(conteudo).hexdigest()
    if sha != caso["sha256_imagem"]:
        raise ErroRevisao("a imagem da pasta não é a do caso (sha256 diferente)")

    manifesto = manifesto or dir_gabarito / "manifesto.jsonl"
    itens = ler_manifesto(manifesto) if manifesto.exists() else []
    if any(i.sha256 == sha for i in itens):
        raise ErroRevisao("imagem já está no gabarito")
    relativo = f"imagens/revisao/{correcao.id_caso}{imagens[0].suffix.lower()}"
    destino = dir_gabarito / relativo
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(imagens[0], destino)
    item = ItemGabarito(
        arquivo=relativo,
        sha256=sha,
        tipo_documento=correcao.tipo_documento,
        subtipo=correcao.subtipo,
        uf=correcao.uf,
        lado=correcao.lado,
        qualidade=correcao.qualidade,
        particao=particao,
        campos=campos,
        origem=f"revisao:{correcao.id_caso}",
    )
    escrever_manifesto(manifesto, [*itens, item])
    if repositorio is not None:
        repositorio.marcar_conferido(correcao.id_caso, agora)
    return item


def main(argv: list[str] | None = None) -> int:
    from app.armazenamento.db import criar_engine, criar_sessoes
    from app.config import obter_settings

    parser = argparse.ArgumentParser(prog="revisao", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="comando", required=True)
    exp = sub.add_parser("exportar", help="exporta os casos pendentes para conferência")
    exp.add_argument("--destino", type=Path, default=Path("saidas/revisao"))
    exp.add_argument("--limite", type=int, default=100)
    imp = sub.add_parser("importar", help="importa a correção de um caso no gabarito")
    imp.add_argument("--caso", type=Path, required=True)
    imp.add_argument("--gabarito", type=Path, required=True)
    imp.add_argument("--manifesto", type=Path)
    imp.add_argument("--particao", choices=["ajuste", "holdout"], default="ajuste")
    imp.add_argument("--sem-banco", action="store_true", help="não marca o caso como conferido")
    args = parser.parse_args(argv)

    settings = obter_settings()
    repositorio = Repositorio(criar_sessoes(criar_engine(settings)))
    if args.comando == "exportar":
        ids = exportar(repositorio, Objetos(settings), args.destino, limite=args.limite)
        print(f"{len(ids)} caso(s) exportado(s) para {args.destino}")
        return 0
    try:
        item = importar(
            args.caso,
            args.gabarito,
            None if args.sem_banco else repositorio,
            args.particao,
            args.manifesto,
        )
    except ErroRevisao as erro:
        print(f"erro: {erro}")
        return 1
    print(f"caso importado: {item.arquivo} (partição {item.particao})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
