"""O exemplo de gabarito em docs/exemplos/gabarito continua importável e igual ao manifesto."""

from __future__ import annotations

from pathlib import Path

from app.schemas.campos import definicao
from avaliacao import sintetico
from avaliacao.gabarito import escrever_manifesto, ler_manifesto, verificar_arquivos
from avaliacao.importador import importar_csv
from avaliacao.particao import atribuir_particoes

EXEMPLO = Path(__file__).resolve().parents[1] / "docs" / "exemplos" / "gabarito"


def test_exemplo_de_gabarito_importa_e_bate_com_o_manifesto(tmp_path: Path) -> None:
    # Mesmos parâmetros do `make gabarito-sintetico`: as imagens são determinísticas.
    sintetico.gerar(tmp_path)
    itens, problemas = importar_csv(EXEMPLO / "gabarito-exemplo.csv", tmp_path)
    assert problemas == []
    assert len(itens) == 7

    manifesto = tmp_path / "manifesto-exemplo.jsonl"
    escrever_manifesto(manifesto, atribuir_particoes(itens))
    assert verificar_arquivos(tmp_path, ler_manifesto(manifesto)) == []
    # O sha256 é conferido contra a imagem gerada (acima); os bytes do PNG podem variar
    # entre ambientes, então a comparação com o exemplo ignora só esse campo.
    gerado = [i.model_dump(exclude={"sha256"}) for i in ler_manifesto(manifesto)]
    esperado = [
        i.model_dump(exclude={"sha256"}) for i in ler_manifesto(EXEMPLO / "manifesto-exemplo.jsonl")
    ]
    assert gerado == esperado

    # Todo campo preenchido pertence ao subtipo da linha.
    for item in itens:
        for nome in item.campos:
            definicao(item.subtipo, nome)
