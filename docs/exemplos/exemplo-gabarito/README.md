# Exemplo de gabarito

Exemplo do gabarito que o avaliador espera, feito **só com dados fictícios**: as imagens
são as do gabarito sintético (`make gabarito-sintetico`, determinístico) e os nomes são
sílabas sorteadas. O formato e as regras valem igual para o gabarito real, que **nunca
entra no git** (ver `docs/gabarito.md`).

| Arquivo | O que é |
|---|---|
| `gabarito-exemplo.csv` | O que a equipe preenche: uma linha por imagem, uma coluna por campo |
| `manifesto-exemplo.jsonl` | O que o importador gera a partir do CSV, já particionado |

## Como reproduzir

```bash
make gabarito-sintetico
```
```bash
uv run gabarito importar-csv --csv docs/exemplos/exemplo-gabarito/gabarito-exemplo.csv --imagens dados/sintetico --saida dados/sintetico/manifesto-exemplo.jsonl
```
```bash
uv run gabarito particionar --manifesto dados/sintetico/manifesto-exemplo.jsonl
```
```bash
uv run gabarito validar --manifesto dados/sintetico/manifesto-exemplo.jsonl
```

O manifesto fica ao lado das imagens porque `arquivo` é relativo ao diretório dele. O
resultado é igual a `manifesto-exemplo.jsonl`, salvo o `sha256`, que acompanha os bytes da
imagem gerada na sua máquina (o teste `tests/test_exemplo_gabarito.py` garante o resto).

## Colunas do CSV

UTF-8, separador `,` ou `;`. Uma linha por **imagem** (frente e verso separados são duas
linhas; frente e verso na mesma imagem é uma linha com `lado = FRENTE_E_VERSO`).

| Coluna | Obrigatória | Valores |
|---|---|---|
| `arquivo` | sim | caminho da imagem relativo ao diretório passado em `--imagens` |
| `tipo_documento` | sim | o tipo **declarado** na requisição: `IDENTITY_DOCUMENT`, `CPF`, `CTPS` |
| `subtipo` | sim | o subtipo **real** da imagem: `RG_ANTIGO`, `RG_2018_2022`, `CIN`, `CNH`, `CPF_CARTAO`, `CPF_COMPROVANTE`, `CPF_DIGITAL`, `CTPS_FISICA`, `CTPS_DIGITAL` |
| `uf` | sim | UF emissora (sigla) ou `NAO_IDENTIFICADO` |
| `lado` | sim | `FRENTE`, `VERSO`, `FRENTE_E_VERSO`, `PAGINA` |
| `qualidade` | sim | `foto`, `xerox` ou `print` |
| `particao` | não | deixe vazio: `uv run gabarito particionar` sorteia `ajuste`/`holdout` uma única vez |
| `campo:<nome>` | — | valor conferido, `AUSENTE` ou `ILEGIVEL` (regras abaixo) |
| `caixa:<nome>` | não | `x,y,largura,altura` do valor na imagem, em pixels; ajuda o teste do campo apagado |

## Regras de preenchimento dos campos

1. **Transcreva como está impresso**, com a pontuação do documento (`123.456.789-09`,
   `19.105.432-1`, `16/01/2011`, `SSP/ES`). O avaliador normaliza antes de comparar, então
   máscara e espaços não contam como erro; dígito, letra e data errados contam.
2. **Nunca corrija o documento.** Se o impresso tem um dígito verificador inválido, o
   gabarito registra o impresso. O gabarito é o que está na imagem, não o que deveria
   estar.
3. **`AUSENTE`**: o campo não existe neste lado/modelo do documento (por exemplo, todos os
   campos na frente do RG antigo, linha 1 do exemplo; `cpf` em RG antigo sem CPF).
4. **`ILEGIVEL`**: o campo existe, mas nem uma pessoa consegue ler com certeza (borrão,
   reflexo, rasura, corte). Na dúvida entre um valor e `ILEGIVEL`, use `ILEGIVEL`: qualquer
   valor devolvido pelo sistema nesse campo conta como alucinação.
5. **Célula vazia** = campo não avaliado. Use só para campos de outros subtipos (a coluna
   `campo:categoria` fica vazia numa linha de RG). Nos campos do subtipo da linha, preencha
   sempre valor, `AUSENTE` ou `ILEGIVEL`, principalmente nos **críticos**.
6. **Dupla conferência**: cada linha preenchida por uma pessoa e conferida por outra. Valor
   copiado de saída de sistema (OCR, VLM, fornecedor) sem conferência não serve.

## Campos por subtipo

Críticos em **negrito** (são eles que entram na meta de erro escapado).

| Subtipo | Campos |
|---|---|
| `RG_ANTIGO` | **numero_rg, orgao_emissor, uf_emissao, data_expedicao, nome, data_nascimento**, filiacao_linha_1, filiacao_linha_2, naturalidade, documento_origem, cpf |
| `RG_2018_2022` | **numero_rg, orgao_emissor, uf_emissao, data_expedicao, nome, data_nascimento, cpf**, nis, titulo_eleitor, numero_cnh, numero_ctps, cns, filiacao_linha_1, filiacao_linha_2, naturalidade, documento_origem |
| `CIN` | **cpf, nome, data_nascimento, data_expedicao, validade, orgao_emissor, uf_emissao**, filiacao_linha_1, filiacao_linha_2, naturalidade, mrz, nis, titulo_eleitor |
| `CNH` | **nome, cpf, data_nascimento, numero_registro, validade, categoria**, documento_identidade, orgao_emissor_identidade, filiacao_linha_1, filiacao_linha_2, primeira_habilitacao, data_emissao, local_emissao, observacoes |
| `CPF_CARTAO`, `CPF_COMPROVANTE`, `CPF_DIGITAL` | **cpf**, nome, data_nascimento |
| `CTPS_FISICA` | **numero_ctps, serie_ctps, uf_ctps**, nome, data_nascimento, data_emissao, contrato_empregador, contrato_cnpj, contrato_cargo, contrato_admissao, contrato_saida, contrato_salario |
| `CTPS_DIGITAL` | **cpf**, nome, contrato_empregador, contrato_cnpj, contrato_cargo, contrato_admissao, contrato_saida, contrato_cbo |

Fonte única: `app/schemas/campos.py`. `numero_ctps` e `serie_ctps` da CTPS digital são
derivados do CPF pelo código e não entram no gabarito.

## O que o exemplo cobre

| Linha | Subtipo · lado | O que mostra |
|---|---|---|
| 1 | RG antigo · frente | todos os campos `AUSENTE` (a frente não tem dados) |
| 2 | RG antigo · verso | `cpf` `AUSENTE`, demais com valor; `caixa:nome` preenchida |
| 3 | RG 2018–2022 · verso | campos extras (NIS, título, CNS…) com valor ou `AUSENTE` |
| 4 | CIN · verso | validade, MRZ |
| 5 | CNH · verso | categoria e datas da habilitação |
| 6 | CPF cartão · verso | só três campos |
| 7 | CTPS digital · página | contrato; `caixa:cpf` preenchida |

Faltam no exemplo, porque o gerador sintético ainda não os produz: `CPF_COMPROVANTE`,
`CPF_DIGITAL`, `CTPS_FISICA` e um caso `ILEGIVEL`. O formato é o mesmo.

## Tamanho necessário

A meta (erro escapado < 0,5%) só fica **demonstrada** com pelo menos **598 campos críticos
aprovados sem erro no holdout** (decisão 119). No sintético, a esteira aprovou 2,2 campos
críticos por documento (82 em 38). Com essa taxa:

- holdout: cerca de 275 documentos;
- gabarito total (holdout = 20%): cerca de **1.400 documentos**.

É uma estimativa a refazer com a taxa de aprovação do gabarito real. Com menos, o portão
sai "não demonstrada por amostra pequena", mesmo sem nenhum erro. A distribuição deve
seguir a produção (subtipo × UF × qualidade); `uv run gabarito estratificar` aponta os
estratos com poucos exemplos.

## Dados pessoais

O gabarito real tem dados pessoais: fica fora do git (em `gabarito/`, que está no
`.gitignore`, ou fora do repositório), e os relatórios do avaliador só mostram ids.
