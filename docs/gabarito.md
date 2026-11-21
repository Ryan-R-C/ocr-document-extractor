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
uv run gabarito apagar-campos --manifesto <dir>/manifesto.jsonl --saida saidas/variantes [--ocr]
uv run avaliar --config <rodada.toml> --particao ajuste
uv run avaliar --config <rodada.toml> --particao ajuste --estimar [--base <relatorio.json>]
               [--taxa-releitura 0.6] [--orcamento-brl 50] [--gabarito <manifesto.jsonl>]
uv run portao --ajuste <ajuste>/relatorio.json [--holdout <holdout>/relatorio.json]
              [--baseline <fase1>/relatorio.json ...] --saida <rascunho.md>
```

### Rascunho do relatório do Portão 2+3

`uv run portao` (`avaliacao/portao.py`) só lê os `relatorio.json` (não roda a esteira nem
recalcula métricas) e escreve o rascunho de `docs/relatorios/portao-2-3.md`: rodadas e
`versao_pipeline` (com aviso em destaque se ajuste e holdout divergirem), veredito
estatístico (decisões 119 a 121; provisório, sobre o ajuste, enquanto não houver holdout),
erro escapado e alucinação por campo, subtipo, UF e qualidade contra o modo medição da
rodada e dos baselines da mesma partição, comparação lado a lado com os baselines da Fase 1
(sem segundo leitor), revisão, 422 por motivo, ancoragem e releitura, custo e latência,
casos do diagnóstico com a coluna "Proposta de correção" e o efeito das opções de
`NAO_VERIFICAVEL` e `AUSENTE`. Análise, próximos passos e as propostas ficam marcados
`_a escrever_`. Um `relatorio.json` antigo sem alguma chave tem a seção omitida, com aviso.

### Estimativa de custo e orçamento

`--estimar` não chama nenhum provider (nem monta a esteira): lê o manifesto e o tamanho das
imagens e imprime as imagens da partição, as variantes do campo apagado, as chamadas
previstas por etapa (leitura única, ou classificação + extração; releitura), os tokens por
chamada e o custo em US$ e R$, médio por documento e total, em faixa baixa / central / alta.
A lógica está em `avaliacao/estimativa.py`.

- **Tokens.** Com `--base` (ou `[estimativa] base` na rodada, relativo ao `.toml`), as
  médias medidas no `relatorio.json` de uma rodada anterior, por etapa, quando o modelo é
  o mesmo. Sem base: o plano (1.500 de instruções e schema + imagem; 600 de saída), com a
  imagem contada pela regra do modelo (`MODELOS_IMAGEM`) no tamanho que vai ao provider e
  o texto nunca abaixo do prompt + schema reais (~4 caracteres por token).
- **Releitura.** `taxa_releitura` releituras por documento: o valor configurado, senão o
  medido na base, senão 0,6 (plano: 30% dos documentos com dois campos relidos). Faixa da
  metade ao dobro. Sem leitor de texto (`nenhum`) não há releitura.
- **Faixa.** Tokens ± `folga_tokens` (25%; a alta usa o p95 medido se for maior) e, sem
  caixa no gabarito com `usar_ocr_sem_caixa`, a baixa só conta as variantes certas.
- **Preço.** `PRECOS_MODELOS`, fator do modo (`--modo batch|flex`) e `CAMBIO_USD_BRL`.

`orcamento_brl` na rodada (ou `--orcamento-brl`) é um teto em R$:

- antes de começar, se a estimativa **alta** passar do teto, a rodada não começa (código 3);
- durante a rodada, o custo medido de cada chamada é somado; quando passa do teto, nenhuma
  chamada nova sai, os documentos restantes não são processados e o relatório é gravado com
  o que foi feito e a marca `execucao.orcamento.interrompida` (código 3). As chamadas que já
  estavam em voo terminam e são cobradas: o excedente aceito é o delas (até `concorrencia`
  documentos, cada um com a leitura ou as releituras em paralelo). No modo Batch todo o lote
  está em voo; ali vale o bloqueio pela estimativa;
- com provider `fake` o custo é zero e nada é bloqueado.

```toml
orcamento_brl = 50.0

[estimativa]
base = "../../saidas/avaliacoes/<rodada anterior>/relatorio.json"
taxa_releitura = 0.6
folga_tokens = 0.25
```

Um exemplo completo, com dados fictícios, está em `docs/exemplos/gabarito/` (CSV de
entrada, manifesto gerado e regras de preenchimento).

O importador lê um CSV com as colunas `arquivo, tipo_documento, subtipo, uf, lado,
qualidade[, particao]`, mais `campo:<nome>` (valor, `AUSENTE` ou `ILEGIVEL`) e, opcional,
`caixa:<nome>` (`x,y,largura,altura`). Ele nunca altera os originais. Se o gabarito
existente estiver em outro formato, basta escrever outro leitor para o mesmo manifesto.

## Teste do campo apagado

Para cada item, um campo crítico com valor (escolhido pelo hash; configurável) é borrado
(`ILEGIVEL`) ou apagado com a cor do fundo (`AUSENTE`). Qualquer valor devolvido nesse
campo é alucinação. Sem `caixa` no gabarito, o `LocalizadorValorPorOCR` procura o valor
conferido nas palavras do OCR configurado (PaddleOCR): `--ocr` no comando, ou
`usar_ocr_sem_caixa = true` na seção `[campo_apagado]` da rodada.

## Casos em revisão viram gabarito

Os casos que a esteira mandou para revisão são conferidos por uma pessoa e entram no
gabarito, na partição de ajuste por padrão, com `origem = "revisao:<id_caso>"`.

```bash
uv run revisao exportar --destino saidas/revisao
uv run revisao importar --caso saidas/revisao/<id_caso> --gabarito <dir_gabarito>
```

`exportar` grava, por caso pendente, a imagem recebida, os recortes, `leituras.json` (o que
a esteira leu) e `correcao.json`, um modelo em branco. Quem confere preenche `qualidade` e,
em cada campo, `status` (`LEGIVEL`, `AUSENTE` ou `ILEGIVEL`) e `valor` (só em `LEGIVEL`),
corrigindo `subtipo`, `uf` e `lado` se preciso. `importar` valida, confere o sha256 da
imagem, copia para `imagens/revisao/`, acrescenta ao manifesto e marca o caso como
`CONFERIDO` (`--sem-banco` pula essa marcação; `--particao holdout` só com motivo). A pasta
exportada tem dados pessoais: fica fora do git e é apagada depois da conferência.
