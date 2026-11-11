# Prompts por fase — implementação no Claude Code

## Como usar

1. Num repositório novo, coloque o `CLAUDE.md` na raiz, o plano em `docs/plano.pdf` e este arquivo em `docs/prompts-por-fase.md`.
2. Preencha os `[A PREENCHER]` do `CLAUDE.md`. O que já estiver decidido entre as "Decisões em aberto", escreva lá mesmo.
3. Abra uma sessão nova por fase e escreva só: `Execute a Fase 0 de docs/prompts-por-fase.md.` (ou cole a seção da fase). O Claude Code propõe um plano; aprove ou corrija antes de ele codar.
4. As fases 2 e 3 correm em paralelo: uma sessão para cada, em branches ou worktrees separados (um por desenvolvedor). Depois rode a seção "Portão 2+3".
5. Cada fase termina com um relatório em `docs/relatorios/`. Você aprova o portão; só então segue para a próxima.

---

## Fase 0 · Fundação (semanas 1–2)

Leia o `CLAUDE.md` e, em `docs/plano.pdf`, as seções "Contexto e escopo", "Arquitetura", "Contrato da API", "Armazenamento, auditoria e retenção" e "Avaliação e métricas". Antes de escrever código, apresente um plano curto e as dúvidas, e espere minha aprovação.

**Objetivo:** o serviço rodando de ponta a ponta com providers falsos, o contrato fechado, o armazenamento com expiração e o avaliador rodando a esteira inteira sobre o gabarito.

### 1. Projeto
- `pyproject.toml`, `Dockerfile` (CPU, imagem enxuta, usuário sem root), `docker-compose.yml` com api, postgres e minio, `Makefile` com `setup`, `up`, `test`, `lint`, `avaliar`.
- `app/config.py` (pydantic-settings) com as variáveis do plano: `LEITOR_VISUAL_PROVIDER`, `LEITOR_VISUAL_MODELO`, `LEITOR_VISUAL_MODELO_ESCALADA`, `LEITOR_VISUAL_DETALHE`, `LEITOR_TEXTO_PROVIDER`, `PROVIDER_FALLBACK`, `PROVIDER_TIMEOUT_S`, `ARTEFATOS_TTL` (`8h` | `16h` | `null`), `CASOS_REVISAO_TTL`, `HTTP_STATUS_FALHA_EXTRACAO`. Acrescente as que faltam: tamanho máximo do arquivo, conexões de banco e S3, preços por modelo (entrada e saída por milhão de tokens), câmbio USD→BRL, limites de concorrência e de tokens por minuto por provider. `.env.example` comentado.

### 2. Contrato
- `POST /v1/extracoes` (síncrono, uma imagem por chamada) e `GET /health`.
- Modelos Pydantic exatamente como no plano.

Requisição:
```json
{
  "tipo_documento": "IDENTITY_DOCUMENT",
  "imagem_base64": "<JPEG, PNG, WebP, HEIC ou PDF>",
  "id_externo": "opcional, para rastreio"
}
```

Resposta 200:
```json
{
  "id_processamento": "9b1c...",
  "status": "REVISAO",
  "documento": { "subtipo": "RG_ANTIGO", "uf": "SP", "lado": "VERSO" },
  "campos": {
    "cpf": {
      "valor": "123.456.789-09",
      "valor_normalizado": "12345678909",
      "status_leitura": "LEGIVEL",
      "status_validacao": "VALIDO",
      "motivos": []
    },
    "data_expedicao": {
      "valor": "12/03/2O15",
      "valor_normalizado": null,
      "status_leitura": "PARCIAL",
      "status_validacao": "NAO_VERIFICAVEL",
      "motivos": ["LEITURAS_DIVERGENTES"]
    }
  },
  "sinalizacoes": ["COPIA_REPROGRAFICA"],
  "versao_pipeline": "1.0.0+openai:<modelo>+regras:1.0"
}
```

Erro:
```json
{
  "id_processamento": "9b1c...",
  "erro": "DOCUMENTO_NAO_PROCESSAVEL",
  "motivos": [
    { "codigo": "CAMPO_CRITICO_ILEGIVEL", "campo": "cpf" },
    { "codigo": "REFLEXO_EXCESSIVO", "detalhe": "reflexo sobre o número do registro" }
  ],
  "acao_sugerida": "REENVIAR_IMAGEM"
}
```

- Enums: `status` do documento `APROVADO` | `REVISAO`; `status_leitura` `LEGIVEL` | `PARCIAL` | `ILEGIVEL` | `AUSENTE`; `status_validacao` `VALIDO` | `INVALIDO` | `SUSPEITO` | `NAO_VERIFICAVEL`; motivos e sinalizações do plano também como enums.
- Códigos HTTP:

| HTTP | Quando | Motivos |
|---|---|---|
| 400 | requisição malformada | `BASE64_INVALIDO`, `TIPO_DESCONHECIDO`, `FORMATO_NAO_SUPORTADO` |
| 413 | arquivo acima do limite | `ARQUIVO_GRANDE_DEMAIS` |
| 422 (configurável) | imagem válida, sem como extrair os campos críticos | `NENHUM_DOCUMENTO`, `TIPO_DIVERGENTE`, `LADO_SEM_DADOS`, `IMAGEM_DESFOCADA`, `DOCUMENTO_CORTADO`, `REFLEXO_EXCESSIVO`, `CAMPO_CRITICO_ILEGIVEL` |
| 502 / 504 | provider falhou ou estourou o tempo, mesmo após nova tentativa e fallback | `PROVIDER_INDISPONIVEL`, `TEMPO_ESGOTADO` |

- Todo erro traz `id_processamento`. Exporte o OpenAPI para `docs/openapi.json` e mantenha um teste que falha se o contrato mudar sem atualizar esse arquivo.

### 3. Interfaces, fakes e esteira
- `LeitorVisual`, `LeitorTexto` e `Localizador` como `Protocol`, com tipos de entrada e saída que incluam tokens, custo, modelo e latência de cada chamada.
- Fakes determinísticos em `app/providers/fake/` que devolvem respostas gravadas por hash de imagem, para os testes e para o avaliador rodar sem custo.
- `app/core/pipeline.py` orquestrando todas as etapas (pré-processamento, leituras em paralelo, ancoragem, releitura, regras, decisão), com stubs onde ainda não há implementação. Nenhum stub pode resultar em `APROVADO`.

### 4. Armazenamento, auditoria e retenção
- Postgres (SQLAlchemy 2 + Alembic): processamentos (id, id_externo, hash da imagem, versao_pipeline, status, horários, `expira_em`), casos em revisão (registro próprio, com seu TTL) e custo por chamada.
- S3: pacote de auditoria por processamento com imagem recebida (e hash), imagem normalizada, recortes de cada campo, respostas brutas dos providers com tokens e custo, ancoragens, releituras, regras aplicadas (id e versão), decisão final, versao_pipeline, horários e id_externo.
- Expiração por `ARTEFATOS_TTL` e `CASOS_REVISAO_TTL`. Atenção: o lifecycle do S3 trabalha em dias e não garante 8 h ou 16 h. Quem garante é o job de limpeza que confere `expira_em` no banco e apaga objeto e metadado; use prefixos por TTL com lifecycle de 1 dia só como rede de segurança.
- Cache: mesmo hash de imagem com a mesma versao_pipeline devolve o resultado guardado enquanto o pacote não expirou, sem nova chamada ao provider.
- Criptografia em repouso nos objetos e logs estruturados com mascaramento de dados pessoais desde o primeiro commit, com teste que garante que CPF e nomes não aparecem em log.

### 5. Gabarito e avaliador
- Proponha o formato do gabarito antes de implementar: um manifesto (JSONL) com arquivo, tipo_documento, subtipo, uf, lado, qualidade (`foto` | `xerox` | `print`), partição (`ajuste` | `holdout`) e, por campo, o valor conferido ou `AUSENTE`/`ILEGIVEL`. Se o gabarito existente estiver em outro formato, escreva um importador sem alterar os originais.
- Partição holdout fixa e determinística (por hash do arquivo), estratificada por subtipo, UF e qualidade. Nada é ajustado no holdout; ele só fornece o número final de cada relatório.
- Relatório de estratificação: contagem por subtipo × UF × qualidade, apontando células vazias ou com poucos exemplos.
- CLI `avaliar --config <arquivo> --particao <ajuste|holdout>` que roda a mesma esteira de produção sobre o gabarito e gera relatório (Markdown + JSON) por campo, subtipo, UF e qualidade de imagem:

| Métrica | Definição | Meta |
|---|---|---|
| Erro escapado | campo crítico errado ou inventado em documento `APROVADO` ÷ campos críticos aprovados | < 0,5% |
| Alucinação | valor devolvido onde o gabarito diz `AUSENTE` ou `ILEGIVEL`, incluindo o teste do campo apagado | zero em críticos aprovados |
| Acurácia por campo | valor normalizado igual ao do gabarito | definir após o baseline |
| Taxa de revisão | documentos em `REVISAO` | definir após o baseline |
| Taxa de 422 | documentos recusados, por motivo | acompanhar |
| Custo por documento | média e p95, em R$ | < R$ 0,15 na média |
| Latência | p50 e p95, total e por etapa | definir após o baseline |

- Teste do campo apagado: gerador de variantes das imagens com um campo crítico borrado ou apagado digitalmente; a resposta certa é `AUSENTE` ou `ILEGIVEL`, qualquer valor conta como alucinação. Se o gabarito não tiver as caixas dos campos, deixe o gerador pronto para usar as caixas do OCR (Fase 2), localizando o valor conferido.
- Deixe previsto na interface do avaliador o modo de rodada mais barata (Batch/Flex); a implementação vem na Fase 1.

### Testes
Contrato (exemplos validam contra o OpenAPI), código HTTP de cada erro, TTL e limpeza, cache por hash, mascaramento de log e avaliador ponta a ponta com os fakes.

### Portão
Contrato aprovado por mim e gabarito estratificado. Gere `docs/relatorios/fase-0.md` com o que foi entregue, o relatório de estratificação, desvios e dúvidas. Pare e aguarde.

---

## Fase 1 · Baseline com GPT (semanas 3–5)

Leia o `CLAUDE.md`, `docs/relatorios/fase-0.md` e, no plano, as seções "Providers", "Extração sem alucinação", "Campos por tipo de documento" e "Custos estimados". Apresente um plano e as dúvidas antes de codar.

**Objetivo:** primeira medição real com o adaptador OpenAI (acurácia, alucinação, custo e latência), ainda sem segundo leitor.

### 1. Pré-processamento (`app/preprocess/`, só CPU)
- Decodificar o base64 (inválido → 400 `BASE64_INVALIDO`) e identificar o formato pelos bytes, não pela extensão: JPEG, PNG, WebP, HEIC ou PDF (outro → 400 `FORMATO_NAO_SUPORTADO`); acima do limite → 413.
- PDF → imagem com DPI configurável; HEIC → imagem. Antes de adotar o PyMuPDF, veja a questão de licença nas decisões em aberto.
- Orientação (EXIF e rotações de 90°/180°), localização do documento e recorte do fundo (contorno + correção de perspectiva).
- Métricas de nitidez, reflexo e corte, com limiares em configuração calibrados na partição de ajuste. Reprovação → 422 com `IMAGEM_DESFOCADA`, `REFLEXO_EXCESSIVO`, `DOCUMENTO_CORTADO` ou `NENHUM_DOCUMENTO`.
- Sinalizações `COPIA_REPROGRAFICA` e `PRINT_DE_TELA` por heurística em CPU e/ou pela classificação do VLM; meça a precisão contra o campo qualidade do gabarito. Sinalizações nunca bloqueiam.
- Limitar a resolução antes de enviar: alvo de cerca de 1600 × 1000 px no documento recortado. Uma foto de 4000 × 3000 px sem recorte passa de 11 mil patches; o adaptador recusa localmente qualquer imagem acima do limite do provider, em vez de deixar o provider recusar.

### 2. Definições de campo e schemas (`app/schemas/`)
- Uma definição declarativa por subtipo: nome do campo, crítico ou informativo, rótulos esperados, impresso ou manuscrito, tipo do valor (numérico, data, texto) e normalizador. Os schemas JSON, a lista de campos críticos e a decisão saem dessa definição.
- Campos por subtipo (proposta do plano; os nomes finais dependem da decisão em aberto sobre o sistema de destino):

| Tipo declarado | Subtipo | Campos críticos | Campos informativos |
|---|---|---|---|
| `IDENTITY_DOCUMENT` | RG antigo | número do RG, órgão emissor, UF, data de expedição, nome, data de nascimento | filiação (linhas), naturalidade, documento de origem, CPF |
| `IDENTITY_DOCUMENT` | RG 2018–2022 | os do RG antigo + CPF | números extras (NIS, título, CNH, CTPS e outros), filiação, naturalidade |
| `IDENTITY_DOCUMENT` | CIN | CPF (número do documento), nome, data de nascimento, data de expedição, validade, órgão e UF | filiação, naturalidade, MRZ e QR (quando houver), números extras |
| `IDENTITY_DOCUMENT` | CNH | nome, CPF, data de nascimento, número de registro, validade, categoria | documento de identidade e órgão, filiação, 1ª habilitação, emissão, local, observações |
| `CPF` | cartão, comprovante ou CPF digital | número do CPF | nome, data de nascimento |
| `CTPS` | física | número, série, UF | nome, data de nascimento, emissão, contratos (empregador, CNPJ, cargo, admissão, saída, salário) |
| `CTPS` | digital (print ou PDF) | CPF (de onde saem número e série) | nome, contratos (empregador, CNPJ, cargo, admissão, saída) |

- Schema de cada campo, exatamente assim:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["status", "rotulo_visto", "texto_literal"],
  "properties": {
    "status": { "type": "string", "enum": ["LEGIVEL", "PARCIAL", "ILEGIVEL", "AUSENTE"] },
    "rotulo_visto": { "type": "string" },
    "texto_literal": { "type": "string" }
  }
}
```

  Todos os campos obrigatórios, status antes do valor, texto vazio quando `ILEGIVEL` ou `AUSENTE`. Filiação sem rótulo vira `filiacao_linha_1` e `filiacao_linha_2`.
- Classificação dentro do tipo declarado (subtipo, UF, modelo, lado e qualidade), com listas fechadas que incluem `NAO_IDENTIFICADO`. Tipo incompatível → 422 `TIPO_DIVERGENTE`; RG enviado só pela frente (foto e assinatura) → 422 `LADO_SEM_DADOS`.
- **Decisão de desenho a resolver nesta fase:** o schema é por subtipo, mas o subtipo só se conhece depois da leitura. Compare na partição de ajuste (a) uma chamada com classificação mais os schemas de todos os subtipos do tipo declarado como alternativas dentro de um objeto raiz, se a saída estruturada estrita do provider aceitar, e (b) classificação numa chamada barata seguida da extração com o schema do subtipo. Traga custo, latência e acurácia de classificação das duas e recomende uma.

### 3. Prompt (`app/prompts/`, versionado, hash em versao_pipeline)
Transcrever exatamente o que está impresso; não corrigir, completar, normalizar nem deduzir de outros campos; `?` para caractere duvidoso; `AUSENTE` e `ILEGIVEL` são respostas corretas. Nenhum exemplo com dados realistas.

### 4. Adaptador OpenAI (`app/providers/openai/`)
- Antes de implementar, consulte a documentação atual da OpenAI sobre saída estruturada, imagens (parâmetro `detail`, cálculo de patches e multiplicador por modelo, limite de patches), preços, Batch/Flex e esforço de raciocínio. Registre o que verificou em `docs/decisoes.md`.
- Converte o schema interno para a saída estruturada estrita do provider; `detail` vem de `LEITOR_VISUAL_DETALHE` (o plano indica `original` para OCR).
- Nova tentativa com espera exponencial e jitter em 429 e 5xx, respeitando `PROVIDER_TIMEOUT_S`; falha final → 502 `PROVIDER_INDISPONIVEL` ou 504 `TEMPO_ESGOTADO`.
- Contabiliza tokens (incluindo os de imagem e os de raciocínio, cobrados como saída) e custo por chamada a partir da tabela de preços da configuração; registra provider e modelo em versao_pipeline.
- Controle de concorrência por provider, contando os tokens de imagem no orçamento de tokens por minuto.
- Menor esforço de raciocínio disponível, configurável e medido no gabarito.
- Testes com respostas gravadas; testes com a API real só com o marcador e a chave presente.
- Não proponha fine-tuning: a plataforma da OpenAI está sendo encerrada e não aceita novos usuários. A melhoria vem de schema, prompt e pipeline.

### 5. Decisão nesta fase
- Sem segundo leitor, nenhum campo crítico pode ser aprovado (invariante 1): a esteira devolve `REVISAO` ou 422.
- O avaliador calcula também um "modo medição": o erro escapado e a alucinação que teríamos se todo campo `LEGIVEL` fosse aprovado direto. Esse é o baseline contra o qual as fases 2 e 3 serão comparadas.

### 6. Avaliador
- Rodadas pelo preço de Batch ou Flex quando o provider oferecer. Batch é assíncrono: o avaliador precisa enviar, acompanhar e retomar.
- Rode a partição de ajuste com gpt-6.1-terra e gpt-6-luna como leitura principal. Compare os tokens de imagem medidos com a estimativa do plano (1.920 por imagem; o multiplicador 1,2 só está documentado para o gpt-6-astra). Holdout só para o número final.

### Portão
Baseline medido e aceito. Gere `docs/relatorios/fase-1.md` com métricas por campo, subtipo, UF e qualidade; custo médio e p95 em R$ comparados à tabela de custos do plano; latência; a decisão sobre o desenho do schema; e os piores casos (ids, sem dados pessoais). Pare e aguarde.

---

## Fase 2 · Segundo leitor (semanas 6–8)

Leia o `CLAUDE.md`, `docs/relatorios/fase-1.md` e a seção "Extração sem alucinação" do plano. Esta fase roda em paralelo com a Fase 3, em outro branch: não mexa em `app/regras/`. A interface entre as duas fases fica em `app/core/decisao.py` e numa função de checagem de dígitos verificadores que a Fase 3 entrega; combine o formato no plano. Apresente o plano antes de codar.

**Objetivo:** OCR clássico como segundo leitor, ancoragem, releitura dirigida e casos em revisão.

### 1. Adaptador PaddleOCR em CPU (`app/providers/paddleocr/`)
- Confira a versão atual e a API do PaddleOCR (mudou bastante entre versões principais) e use o build de CPU do PaddlePaddle, com modelo adequado a português.
- Saída: palavras com caixa, confiança e id estável (o id serve depois para a extração por referência).
- Modelo carregado uma vez por processo, com aquecimento no startup; inferência fora do event loop (pool de processos ou threads). Meça tempo e memória por imagem dentro do container.
- Um teste de contrato comum a todo `LeitorTexto`, para os adaptadores da Fase 4 passarem nele também.

### 2. Leituras em paralelo
VLM e OCR rodam juntos (asyncio) sobre a mesma imagem normalizada.

### 3. Ancoragem (`app/core/ancoragem.py`)
- O `texto_literal` do VLM precisa aparecer no OCR perto do `rotulo_visto`: localize o rótulo nas palavras do OCR e procure o valor na região esperada (mesma linha à direita ou linhas logo abaixo, configurável por subtipo). Um valor pode ocupar várias palavras.
- A comparação tolera espaços e pontuação; `?` no texto do VLM casa com qualquer caractere.
- As confusões comuns (O/0, I/1, S/5, lista configurável) se aplicam só ao texto do OCR e conforme o tipo do campo (em campo numérico, um O lido pelo OCR conta como 0). O valor do VLM nunca é alterado: se o próprio VLM devolver caractere incompatível com o tipo, o campo fica `PARCIAL`, com `LEITURAS_DIVERGENTES` e `valor_normalizado` nulo.
- Resultado por campo: `ANCORADO`, `PARCIAL` ou `NAO_ANCORADO`, com a evidência (ids das palavras e caixas) no pacote de auditoria.
- Campos manuscritos não dependem de ancoragem no OCR (que quase nunca ancora manuscrito): exigem duas leituras de modelos diferentes concordando.

### 4. Releitura dirigida (`app/core/releitura.py`)
- Disparo: campo crítico `NAO_ANCORADO` ou `PARCIAL`, ou com dígito verificador falho.
- Recorte do campo com margem (caixas do `Localizador`; de início, as do OCR em torno do rótulo), ampliado e enviado ao `LEITOR_VISUAL_MODELO_ESCALADA` com schema de um único campo. O prompt não informa valor esperado nem leituras anteriores.
- Duas de três leituras (VLM principal, OCR, releitura) concordando após normalização → campo lido, com o valor da concordância; senão → revisão. Para o campo poder ser aprovado, o OCR precisa estar entre as leituras que concordam (invariante 1); se só o VLM principal e a releitura concordarem, o campo fica em revisão até a decisão em aberto sobre isso. No máximo uma releitura por campo; depois dela as regras rodam de novo, sem novo ciclo.
- Cada releitura registra custo e entra no pacote de auditoria.

### 5. Casos em revisão
- Registro próprio com `CASOS_REVISAO_TTL`, guardando imagem, recortes e leituras.
- CLI para exportar casos e importar a correção feita por uma pessoa; o caso corrigido entra no gabarito (partição de ajuste por padrão), com a origem registrada. Sem interface gráfica nesta fase.

### 6. Avaliação
- Teste do campo apagado agora com as caixas do OCR.
- Novas métricas: taxa de ancoragem por campo, subtipo e qualidade; taxa de releitura; custo extra da escalada.

Gere `docs/relatorios/fase-2.md` e pare. O portão é avaliado junto com a Fase 3.

---

## Fase 3 · Motor de regras (semanas 6–9)

Leia o `CLAUDE.md`, `docs/relatorios/fase-1.md` e a seção "Motor de regras" do plano. Esta fase roda em paralelo com a Fase 2, em outro branch: mexa só em `app/regras/` e na interface combinada com `app/core/decisao.py`. Apresente o plano antes de codar.

**Objetivo:** validações determinísticas e versionadas, só com o próprio documento, e a decisão final do status.

### 1. Estrutura
- Cada regra: id, versão, vigência (início e fim), fonte (link do plano), a que tipo, subtipo e campo se aplica, qual data de referência usa (data do documento, como expedição ou emissão, ou data do processamento) e uma função pura que devolve `VALIDO`, `INVALIDO`, `SUSPEITO` ou `NAO_VERIFICAVEL` com motivo.
- Normalizadores por tipo de valor (CPF, datas, números por UF), sem jamais consertar dígito.
- Agregação por campo: `INVALIDO` > `SUSPEITO` > `NAO_VERIFICAVEL` > `VALIDO`.
- A função de checagem de dígitos verificadores usada pela releitura da Fase 2 sai daqui.
- Regras aplicadas (id e versão) vão para o pacote de auditoria; a versão do conjunto de regras vai para versao_pipeline.
- QR e MRZ são bônus: presentes e legíveis reforçam (`QR_CONFERIDO`, `MRZ_CONFERIDA`); ausentes não pesam contra.

### 2. Dígitos verificadores
Use o validate-docbr (CPF, CNPJ numérico e alfanumérico, CNH, CNS, PIS, título, certidões). Confira na versão instalada se cada um existe, em especial o CNPJ alfanumérico; o que faltar, implemente pela especificação oficial, com testes.

### 3. Regras
Confirme datas e prazos na fonte de cada uma (lista de referências do plano) antes de codificar; se divergirem do que está aqui, pergunte.

| Documento | Regra | Se falhar |
|---|---|---|
| Todos | CPF: dígitos verificadores e rejeição de 11 dígitos iguais | releitura; persistindo, `INVALIDO` |
| Todos | Datas válidas; idade plausível; expedição depois do nascimento e não futura | `INVALIDO` ou `SUSPEITO` |
| RG | Formato do número por UF; dígito verificador só em SP (8 dígitos + DV, módulo 11 com X) | fora de SP: `NAO_VERIFICAVEL` |
| RG 2018–2022 | Dígitos verificadores de NIS/PIS, título, CNH e CNS quando impressos | `INVALIDO` no campo |
| RG | Matrícula de certidão (32 dígitos): DVs, código 55 nas posições 9–10, ano do registro ≥ ano de nascimento | `SUSPEITO` |
| RG | Naturalidade (município × UF) na tabela do IBGE, tolerando nomes históricos | `NAO_VERIFICAVEL` |
| RG | Órgão emissor coerente com a UF | `SUSPEITO` |
| RG antigo | Validade até 28/02/2032; indeterminada para quem tinha 60 anos em 01/03/2022 | sinalização |
| CIN | Número = CPF; validade coerente com a idade na expedição (5 anos até 11, 10 anos de 12 a 59, indeterminada a partir de 60) | `INVALIDO` ou `SUSPEITO` |
| CIN | MRZ legível: checksums e conferência com os campos impressos | bônus ou `SUSPEITO` |
| CNH | Dígitos verificadores do registro; categoria em lista fechada | `INVALIDO` |
| CNH | Validade como teto por idade e época: desde 12/04/2021, 10 anos (< 50), 5 (50–69), 3 (70+); antes, 5 (até 65) e 3 (acima); prazo menor é permitido | `SUSPEITO` |
| CNH | Prorrogação do Contran: vencidas entre 05/06 e 08/12/2026 valem até 09/12/2026 | sinalização |
| CNH | 1ª habilitação com 18 anos ou mais e não posterior à emissão | `SUSPEITO` |
| CTPS física | Número, série e UF no formato (em geral 7 + 4 + UF, tolerando modelos antigos) | `SUSPEITO` |
| CTPS (contratos) | CNPJ ou CGC: dígitos verificadores, aceitando CNPJ alfanumérico (emitido desde 31/07/2026) | `INVALIDO` no contrato |
| CTPS (contratos) | Admissão antes da saída; idade mínima plausível na admissão; moeda do salário coerente com a data (Cr$, Cz$, NCz$, CR$, R$) | `SUSPEITO` |
| CTPS digital | Número = 7 primeiros dígitos do CPF; série = 4 últimos; CBO na tabela oficial | `INVALIDO` |

A prorrogação do Contran depende da data do processamento e deixa de ter efeito depois de 09/12/2026, provavelmente antes da produção: implemente com vigência, sem ir além disso.

### 4. Sinalizações de política (nunca bloqueiam)
`COPIA_REPROGRAFICA`, `PRINT_DE_TELA`, `RG_ANTIGO`, `DOCUMENTO_VENCIDO`, `QR_CONFERIDO`, `MRZ_CONFERIDA`.

### 5. Tabelas versionadas (`app/regras/tabelas/`)
Municípios do IBGE por UF com nomes históricos, órgãos emissores por UF e CBO, cada uma com fonte, data de extração e script de atualização. Se não houver acesso à rede para baixar das fontes oficiais, pare e me peça os arquivos.

### 6. Decisão (`app/core/decisao.py`)
- `APROVADO`: todos os campos críticos `LEGIVEL`, ancorados no OCR (direto ou via releitura com o OCR entre as leituras que concordam; nos manuscritos, dupla leitura de modelos diferentes) e com validação sem falha.
- `REVISAO`: demais casos com campos críticos lidos.
- 422 `CAMPO_CRITICO_ILEGIVEL`: algum campo crítico ilegível. O tratamento de campo crítico `AUSENTE` e de `NAO_VERIFICAVEL` em campo crítico seguem as decisões em aberto do `CLAUDE.md`; implemente os dois como configuração e não escolha o padrão sozinho.
- Sinalizações nunca alteram o status.

### 7. Testes
Cada regra com casos nas fronteiras, sempre com dados sintéticos: idades 11/12, 49/50, 59/60, 65/66, 69/70; 18 anos na 1ª habilitação; 12/04/2021 para a CNH; 01/03/2022 e 28/02/2032 para o RG antigo; 31/07/2026 para o CNPJ alfanumérico; a janela de prorrogação do Contran; os quatro estados de vigência (antes, no início, no fim, depois).

Gere `docs/relatorios/fase-3.md` e pare.

---

## Portão 2+3 · Integração

As fases 2 e 3 estão concluídas em branches separados. Integre-as (os conflitos devem ficar em `app/core/`), rode a suíte completa e o avaliador na partição de ajuste e, por fim, no holdout, com a configuração indicada pelo baseline (ponto de partida: gpt-6.1-terra na leitura principal e gpt-6.1-sol na escalada).

Gere `docs/relatorios/portao-2-3.md` com:
- erro escapado e alucinação em campos críticos aprovados, por campo, subtipo, UF e qualidade, comparados ao baseline em modo medição;
- taxa de revisão, taxa de 422 por motivo, taxa de ancoragem e de releitura;
- custo médio e p95, latência p50 e p95;
- para cada erro escapado ou alucinação: id do caso, campo, etapa que deixou passar e proposta de correção;
- o efeito, na taxa de revisão, de cada opção das decisões em aberto sobre `NAO_VERIFICAVEL` e campo `AUSENTE`.

Meta do portão: zero alucinação em campos críticos aprovados e erro escapado abaixo de 0,5%. Se não passar, não ajuste nada olhando o holdout: proponha mudanças, valide na partição de ajuste e me mostre antes de aplicar. Pare e aguarde.

---

## Fase 4 · Comparação de providers (semanas 10–11)

Leia o `CLAUDE.md` e `docs/relatorios/portao-2-3.md`. Apresente o plano antes de codar.

**Objetivo:** escolher, por subtipo, a configuração mais barata que mantém as metas de erro.

### 1. Adaptadores novos
Cada um em `app/providers/<nome>/`, passando nos mesmos testes de contrato da interface:
- Leitor visual: Anthropic Claude, Google Gemini e um adaptador genérico para modelos abertos via API hospedada.
- Leitor de texto: Tesseract (CPU), Google Cloud Vision e Azure.
- Localizador: caixas do próprio VLM, quando o provider oferecer.

Para cada um, consulte a documentação atual (saída estruturada, regras de imagem, preço, limites, Batch) e registre em `docs/decisoes.md`. As diferenças ficam no adaptador; nada muda no núcleo, nos schemas ou nas regras. As chaves ficam no `.env`; se faltar alguma, pergunte.

### 2. Configuração por subtipo
Modelo principal e de escalada definidos por subtipo, e combinações entre providers (por exemplo, a dupla leitura de manuscrito feita por modelos de providers diferentes).

### 3. Matriz de avaliação
Na partição de ajuste, cada combinação (leitor visual principal × escalada × leitor de texto) com erro escapado, alucinação, taxa de revisão, taxa de ancoragem, custo médio e p95 e latência, por subtipo. Use Batch/Flex quando houver. Holdout só para as duas ou três finalistas.

### 4. Recomendação
Por subtipo, a configuração mais barata que mantém o erro escapado na meta e zero alucinação em críticos aprovados, e o provider de fallback (só um que tenha passado no portão).

### Portão
Custo médio abaixo de R$ 0,15 e erro dentro das metas. Gere `docs/relatorios/fase-4.md`, pare e aguarde.

---

## Fase 5 · Produção (semanas 12–13)

Leia o `CLAUDE.md` e `docs/relatorios/fase-4.md`. Apresente o plano antes de codar.

1. **Resiliência:** concorrência e orçamento de tokens por minuto por provider (tokens de imagem incluídos); fila interna com limite e resposta rápida quando estourar; fallback (`PROVIDER_FALLBACK`) registrado em versao_pipeline; timeouts coerentes com o tempo máximo de resposta do chamador.
2. **Modo assíncrono** (202 + `GET /v1/extracoes/{id}`): implemente só se a decisão sobre tempo de resposta pedir; caso contrário, documente como ativar.
3. **Observabilidade:** logs estruturados sem dados pessoais; métricas de status, 422 por motivo, ancoragem, releitura, custo, latência por etapa, erros e 429 por provider; rastreio por `id_processamento`; alerta quando custo médio ou taxa de revisão saírem da faixa.
4. **Teste de carga:** com providers falsos de latência realista e uma rodada curta com o provider real; descubra a capacidade por container e o ponto em que o limite de tokens por minuto vira o gargalo.
5. **Segurança:** criptografia em repouso (banco e objetos), acesso restrito aos pacotes de auditoria, autenticação da API conforme a decisão em aberto, revisão de que nenhum log ou erro vaza dado pessoal, imagem Docker sem root e com dependências fixadas. Se `ARTEFATOS_TTL=null` em produção, registre a finalidade em `docs/decisoes.md` para revisão com quem cuida de LGPD.
6. **Operação:** `docs/runbook.md` com deploy, escala, troca de provider ou modelo (sempre com o avaliador antes), e incidentes: provider fora, 429, custo subindo, fila cheia.

### Portão
Teste de carga e runbook prontos. Gere `docs/relatorios/fase-5.md`, pare e aguarde.

---

## Depois · Melhoria contínua

**Rotina semanal**

Importe para o gabarito (partição de ajuste) os casos revisados e corrigidos na semana, rode o avaliador com a configuração de produção e compare com a semana anterior. Relatório curto em `docs/relatorios/semana-<data>.md` com a variação das métricas e os novos padrões de erro. Não mude prompt, modelo ou regra sem me mostrar o resultado no gabarito.

**Extração por referência**

Nos campos impressos, em vez de transcrever, o VLM devolve só os ids das palavras do OCR que compõem cada campo, e o código monta o valor; assim o modelo não tem como inventar caracteres. Implemente como modo alternativo configurável por subtipo e compare com a extração atual no gabarito (erro escapado, alucinação, taxa de revisão, custo).

**Modelos abertos**

Teste modelos abertos via API hospedada como mais um provider do leitor visual, com a mesma matriz da Fase 4.
