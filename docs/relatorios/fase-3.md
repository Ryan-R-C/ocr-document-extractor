# Fase 3 · Motor de regras · relatório

Data: 2026-11-12 · branch `claude/charming-ride-4iq73l`

**Situação:** o motor de regras está pronto, testado e integrado à decisão, junto com a
checagem de dígitos verificadores que a releitura da Fase 2 esperava. Pela primeira vez a
esteira chega a `APROVADO`. As métricas continuam vindo do gabarito sintético, porque o
gabarito real ainda não está disponível. O relatório do portão 2+3
(`docs/relatorios/portao-2-3.md`) depende dele e da rodada com a OpenAI. Ele não foi gerado.

**Bloqueio de rede:** este ambiente não alcança as fontes das referências nem as das
tabelas:
- os sites de lei e notícia da lista do plano são recusados;
- o IBGE fecha a conexão;
- o download da CBO exige captcha, e ele não foi contornado.

As regras seguem a tabela do plano e as suas respostas de 2026-11-12. Toda fonte não
conferida está marcada `[confirmar]` (decisão 98). As tabelas do IBGE e da CBO estão
pendentes; veja "Pendências".

## O que foi entregue

| Item | Onde |
|---|---|
| Estrutura das regras: id, versão, vigência (início e fim, inclusive), fonte, subtipos, campos, data de referência (documento ou processamento) e função pura | `app/regras/base.py` |
| Motor: vigência, agregação `INVALIDO` > `SUSPEITO` > `NAO_VERIFICAVEL` > `VALIDO`, "sem regra" = `VALIDO`, regras aplicadas na auditoria, versão do conjunto (regras + tabelas) em `versao_pipeline` | `app/regras/motor.py` |
| Dígitos verificadores (validate-docbr 2.0.1 + RG de SP) e a função `checar_digitos` da releitura | `app/regras/digitos.py` |
| Regras comuns: DVs de CPF, CNPJ, CNH, PIS, título e CNS; datas válidas, não futuras e depois do nascimento; idade plausível | `app/regras/conjuntos/comuns.py` |
| RG: número por UF (DV só em SP), órgão SSP/UF, matrícula de certidão, naturalidade no IBGE, validade do RG antigo | `app/regras/conjuntos/rg.py` |
| CIN: validade pela idade na expedição, vencimento, MRZ (checksums e conferência com o impresso) | `app/regras/conjuntos/cin.py` |
| CNH: categoria, teto de validade por idade e época, prorrogação do Contran, 1ª habilitação | `app/regras/conjuntos/cnh.py` |
| CTPS: número e série da física, CNPJ alfanumérico, datas do contrato, moeda do salário, CBO | `app/regras/conjuntos/ctps.py` |
| Tabelas versionadas com fonte e data (órgãos SSP/UF prontos; IBGE e CBO com script de atualização) | `app/regras/tabelas/`, `uv run tabelas` |
| Decisão: crítico `ILEGIVEL` → 422; `AUSENTE` → revisão (`CRITICO_AUSENTE`); número e série da CTPS digital derivados do CPF | `app/core/decisao.py`, `app/schemas/campos.py` |
| Esteira: DVs antes da releitura (com a UF do documento), regras depois dela, data do processamento no fuso configurado | `app/core/pipeline.py`, `app/config.py` |
| Contrato: motivos `VALIDACAO_INDISPONIVEL`, `VALOR_INCOERENTE`, `FORA_DA_TABELA` e erro `CAMPO_CRITICO_AUSENTE` | `app/api/contrato.py`, `docs/openapi.json` |
| Gabarito sintético coerente com as regras (DVs válidos, prazos, órgão) | `avaliacao/sintetico.py` |

**Mudanças de schema** (`VERSAO_SCHEMAS` 0.3):
- a `validade` da CIN passa ao tipo `VALIDADE`, que aceita "INDETERMINADA";
- a CTPS digital ganha `contrato_cbo`, informativo, e os campos derivados `numero_ctps` e
  `serie_ctps`, que não vão ao VLM;
- cada campo com dígito verificador declara qual é, no atributo `verificador`.

**Verificação:**
- `make lint` limpo: ruff e mypy strict. Três erros de tipo que já existiam nos testes
  também foram corrigidos.
- **423 testes** passando sem chamada real, 99 deles novos em `tests/test_regras.py`.
  `make test-ocr` (PaddleOCR real) também passa.
- **Fronteiras pedidas no prompt, todas testadas:**
  - idades 11/12 e 59/60 (CIN), 49/50 e 69/70 (CNH desde 2021), 65/66 (CNH antes de 2021);
  - 18 anos na 1ª habilitação;
  - 11 e 12/04/2021, 01/03/2022 e 28/02/2032, 30 e 31/07/2026;
  - a janela do Contran: antes, no início, no fim, depois do fim e depois do fim da
    prorrogação;
  - os quatro estados de vigência, pela data do processamento e pela do documento.
- **Conferidos contra referência externa:**
  - o espécime TD1 do ICAO 9303 (os quatro dígitos de controle);
  - o exemplo oficial de CNPJ alfanumérico da Receita.

## Métricas

### Gabarito sintético (VLM fake, partição de ajuste, 38 documentos)

| Métrica | OCR fake | PaddleOCR medium |
|---|---|---|
| Aprovados / revisão / 422 | 21 / 11 / 6 | {PADDLE_DESFECHOS} |
| Campos críticos aprovados | 82 | {PADDLE_CRITICOS} |
| Erro escapado (críticos aprovados) | 0 (0%) | {PADDLE_ESCAPADO} |
| Alucinação em crítico aprovado, incluindo o campo apagado | 0 | {PADDLE_ALUCINACAO} |
| 422 por motivo | `CAMPO_CRITICO_ILEGIVEL` 10,5%, `LADO_SEM_DADOS` 5,3% | {PADDLE_422} |
| Taxa de ancoragem (todos / críticos) | 97,3% / 98,7% | {PADDLE_ANCORAGEM} |
| Releituras (críticos lidos) | 4/159 (2,5%), as 4 corrigidas por OCR + escalada | {PADDLE_RELEITURA} |
| Erro escapado no modo medição (só o VLM) | 2/157 | {PADDLE_MEDICAO} |
| Custo médio por documento | R$ 0,036 | igual |

Por subtipo (OCR fake):

| Subtipo | Docs | Aprovados | Revisão | 422 | O que manda para revisão |
|---|---|---|---|---|---|
| CIN | 6 | 6 | 0 | 0 | — |
| CTPS digital | 7 | 7 | 0 | 0 | — |
| CNH | 8 | 5 | 2 | 1 | validade sem a data de emissão lida (decisão 102) |
| Cartão CPF | 6 | 3 | 0 | 3 | — |
| RG 2018–2022 | 6 | 0 | 6 | 0 | número do RG fora de SP (`NAO_VERIFICAVEL`) |
| RG antigo | 5 | 0 | 3 | 2 | idem; um caso também com órgão fora da tabela |

O sintético não sorteia SP (só as 8 primeiras UFs), então nenhum RG chega a ser aprovado.
O caminho de SP (DV válido aprova, DV falho fica `INVALIDO`) está coberto nos testes.

### Efeito das decisões sobre `NAO_VERIFICAVEL` e `AUSENTE` (sintético, OCR fake)

| Configuração | Aprovados | Revisão | 422 | Críticos aprovados | Erro escapado |
|---|---|---|---|---|---|
| Padrão: `NAO_VERIFICAVEL` bloqueia, `AUSENTE` → revisão | 21 | 11 (28,9%) | 6 | 82 | 0 |
| `APROVAR_CRITICO_NAO_VERIFICAVEL=true` | 32 | 0 (0%) | 6 | 154 | 0 |
| `CRITICO_AUSENTE=422` | 21 | 11 | 6 | 82 | 0 |
| As duas | 32 | 0 | 6 | 154 | 0 |

Como ler:
- **Todos os 11 casos em revisão são `NAO_VERIFICAVEL`:** 9 RGs fora de SP e 2 CNHs sem a
  data de emissão lida. Liberar o `NAO_VERIFICAVEL` aprovaria todos sem erro escapado no
  sintético, mas aprovaria o número de RG de 26 UFs sem nenhuma validação além da
  ancoragem.
- **`CRITICO_AUSENTE` não muda nada aqui,** porque o sintético não tem crítico ausente
  isolado; o comportamento está nos testes da decisão.
- **O dígito trocado pelo VLM continua sem escapar:** o OCR não o ancora, a releitura
  concorda com o OCR e o valor certo passa nas regras. No modo medição (só o VLM) ele
  escapa duas vezes.

## Desvios do plano

1. **Fontes sem conferência:** as datas e os prazos não foram conferidos na fonte, ao
   contrário do que o prompt pede, porque a rede não alcança os sites. Ficaram as respostas
   do responsável e a tabela do plano, com `[confirmar]` (decisão 98).
2. **Teto da CNH:** desde 12/04/2021, 10 anos até os 69 anos, e não 5 entre 50 e 69, por
   causa da Lei 15.428/2026 (resposta do responsável; decisão 102).
3. **422 por crítico ilegível:** segue o prompt da Fase 3 (algum crítico `ILEGIVEL` → 422),
   e não a decisão 12 da Fase 0, que só recusava quando nenhum crítico era lido.
4. **Formato do RG por UF:** só SP tem formato e DV. As outras UFs ficam `NAO_VERIFICAVEL`;
   só uma letra fora da posição do DV vira `SUSPEITO`.
5. **QR da CIN:** fica pendente (resposta do responsável). A MRZ entra.
6. **Moeda do salário:** inclui o `NCr$` (1967 a 1970), que a lista do plano omite.
7. **Tabelas do IBGE e da CBO ausentes:** naturalidade e CBO saem `NAO_VERIFICAVEL`. Os dois
   campos são informativos e não mudam o status do documento.

## Riscos e pendências

- **Tabelas oficiais.** Preciso de dois arquivos, ou de rede para os hosts:
  - **IBGE:** o JSON de `https://servicodados.ibge.gov.br/api/v1/localidades/municipios`.
    Carregar com `uv run tabelas municipios --arquivo municipios.json`;
  - **CBO 2002:** o arquivo `CBO2002 - Ocupacao.csv`, da página de downloads do MTE.
    Carregar com `uv run tabelas cbo --arquivo …`;
  - **nomes históricos de municípios:** ainda sem fonte;
  - os hosts do IBGE, do MTE e do Planalto foram liberados no ambiente, mas mesmo assim
    recusam a conexão daqui.
- **Fontes `[confirmar]`:**
  - RG de SP (pesos 2 a 9 e X);
  - Decreto 10.977/2022 (validade do RG antigo e da CIN);
  - art. 473 do Provimento CNJ 149 (matrícula);
  - Lei 15.428/2026 (data de vigência e faixa de 70+);
  - Deliberação Contran 279/2026;
  - datas das moedas;
  - formatos antigos de número e série da CTPS.
- **CNH sem data de emissão:** a validade não pode ser verificada sem ela, e a CNH vai para
  revisão. No gabarito real, vale medir quantas CNHs caem aqui. Se forem muitas, a
  alternativa é tornar `data_emissao` crítica ou aceitar o teto mais folgado (10 anos)
  quando ela faltar, o que precisa da sua decisão.
- **RG fora de SP:** com o bloqueio, vai 100% para revisão. É o maior volume de revisão
  esperado.
- **Portão 2+3:** precisa do gabarito real (partição de ajuste e holdout) e da rodada
  OpenAI + PaddleOCR (`openai-terra-unico-paddleocr-medium.toml`, `MODO=batch`). O
  avaliador já mede tudo o que o prompt do portão pede.
- **Pendências anteriores:**
  - nomes dos campos e do enum `tipo_documento` no sistema de destino;
  - decisões 59 a 62 `[confirmar]`;
  - PDF com várias páginas;
  - autenticação da API.
