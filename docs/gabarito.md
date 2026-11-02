# Gabarito: formato, partição e ferramentas

O gabarito real contém dados pessoais e **nunca entra no git**. Ele fica fora do repositório
(ou em `gabarito/`, que está no `.gitignore`), e o avaliador o lê pelo caminho do manifesto
no arquivo de configuração da rodada.

## Manifesto (JSONL)

Uma linha por imagem; `arquivo` é relativo ao diretório do manifesto.

```json
{"arquivo": "rg/0001_verso.jpg", "sha256": "…", "tipo_documento": "IDENTITY_DOCUMENT",
 "subtipo": "RG_ANTIGO", "uf": "SP", "lado": "VERSO", "qualidade": "foto",
 "particao": "ajuste",
 "campos": {
   "numero_rg": {"valor": "12.345.678-9", "caixa": [410, 220, 260, 38]},
   "cpf":       {"status": "AUSENTE"},
   "data_expedicao": {"status": "ILEGIVEL"}
 },
 "origem": "importado:gabarito.csv"}
```

| Chave | Valores |
|---|---|
| `tipo_documento`, `subtipo`, `uf`, `lado` | mesmos enums do contrato |
| `qualidade` | `foto` · `xerox` · `print` |
| `particao` | `ajuste` · `holdout` (atribuída uma vez, nunca muda) |
| `campos.<nome>.valor` | valor conferido; comparado depois de passar pelo normalizador do campo |
| `campos.<nome>.status` | `AUSENTE` ou `ILEGIVEL`: qualquer valor devolvido conta como alucinação |
| `campos.<nome>.caixa` | opcional, `[x, y, largura, altura]` com orientação EXIF aplicada; serve ao teste do campo apagado |
| `origem` | de onde veio o item (importação, revisão conferida, sintético) |
| `variante_de`, `campo_apagado` | só nas variantes geradas pelo teste do campo apagado |

Os nomes de campo são os de `app/schemas/campos.py`; campo fora do subtipo é recusado.
Campo que não aparece no manifesto não é avaliado.

## Partição holdout

- Estrato: subtipo × UF × qualidade.
- Cada estrato recebe `⌊20% × total⌋` vagas no holdout; as vagas que faltam para 20% do
  gabarito vão aos estratos com maior resto, com desempate pelo hash do estrato.
- Dentro do estrato, os itens são ordenados pelo sha256 do arquivo: a escolha é
  determinística e não depende da ordem do manifesto.
- Itens que já têm partição não mudam; casos novos (semanais) são particionados entre si.
- **Nada é ajustado olhando o holdout.** Ele só fornece o número final de cada relatório.

## Comandos

```bash
uv run gabarito importar-csv --csv gabarito.csv --imagens <dir> --saida <dir>/manifesto.jsonl
uv run gabarito particionar  --manifesto <dir>/manifesto.jsonl
uv run gabarito validar      --manifesto <dir>/manifesto.jsonl
uv run gabarito estratificar --manifesto <dir>/manifesto.jsonl --saida saidas/estratificacao
uv run gabarito apagar-campos --manifesto <dir>/manifesto.jsonl --saida saidas/variantes
uv run avaliar --config <rodada.toml> --particao ajuste
```

O importador lê um CSV com as colunas `arquivo, tipo_documento, subtipo, uf, lado,
qualidade[, particao]`, mais `campo:<nome>` (valor, `AUSENTE` ou `ILEGIVEL`) e, opcional,
`caixa:<nome>` (`x,y,largura,altura`). Ele nunca altera os originais. Se o gabarito
existente estiver em outro formato, basta escrever outro leitor para o mesmo manifesto.

## Teste do campo apagado

Para cada item, um campo crítico com valor (escolhido pelo hash; configurável) é borrado
(`ILEGIVEL`) ou apagado com a cor do fundo (`AUSENTE`). Qualquer valor devolvido nesse
campo é alucinação. Sem `caixa` no gabarito, o `LocalizadorValorPorOCR` procura o valor
conferido nas palavras do OCR configurado (pronto para o PaddleOCR da Fase 2).
