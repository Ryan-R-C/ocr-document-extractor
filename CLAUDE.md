# Extração e validação de documentos admissionais

Serviço HTTP que recebe a imagem (base64) de um documento admissional com o tipo declarado, identifica o subtipo, extrai os campos e valida cada valor dentro do próprio documento, devolvendo campos e status.

- Plano completo, fonte da verdade dos requisitos: `docs/plano.pdf`
- Prompt de cada fase: `docs/prompts-por-fase.md`
- Decisões tomadas durante o desenvolvimento: `docs/decisoes.md`
- Relatórios de fase e de portão: `docs/relatorios/`

**O que guia todas as decisões:** nos campos críticos, quase zerar o erro que escapa, mesmo que isso mande mais casos para revisão. Um valor inventado e aprovado é o pior resultado possível; um caso a mais em revisão é aceitável.

**Fora do escopo** (não implemente, nem "de brinde"): captura da imagem, biometria e comparação de face, cruzamento entre documentos (é da etapa de matching), consultas a bases externas como a situação do CPF na Receita.

## Invariantes — nunca violar

1. Nenhum campo crítico é aprovado sem três condições: leitura do VLM com `LEGIVEL`, ancoragem no segundo leitor (OCR clássico) e validação sem falha no motor de regras. Campo manuscrito crítico exige duas leituras de modelos diferentes concordando; sem isso, revisão.
2. O VLM transcreve, o OCR confirma e o código decide. Normalização, validação e status são código determinístico, nunca saída de modelo.
3. Nunca devolver ao modelo o erro do validador pedindo correção: isso fabrica um número que passa no dígito verificador.
4. Nunca corrigir dígitos a partir do dígito verificador.
5. A releitura dirigida nunca informa ao modelo o valor esperado nem as leituras anteriores. No máximo uma releitura por campo.
6. Schemas enviados ao VLM: sem `pattern`, `format` ou `enum` nos campos de valor; toda lista fechada tem a saída `NAO_IDENTIFICADO`; nenhum campo de raciocínio dentro do JSON.
7. Prompts sem exemplos com dados realistas.
8. O núcleo não conhece providers: só `app/providers/<provider>/` importa SDK de provider. Regras, schemas e decisão não dependem de provider.
9. Tudo roda em CPU. Nenhuma dependência que exija GPU.
10. Logs, mensagens de erro e relatórios sem dados pessoais: CPF, nomes e datas mascarados; só ids.
11. Testes nunca chamam provider real nem usam documentos ou dados pessoais reais. Fixtures com dados sintéticos (CPF gerado, imagens sintéticas) e respostas de provider gravadas. Chamadas reais só em testes marcados (`@pytest.mark.provider_real`) e no avaliador.
12. Nenhuma troca de provider, modelo, prompt ou regra entra se piorar o erro escapado no gabarito. A partição holdout do gabarito nunca é usada para ajustar nada.

## Arquitetura

```
POST /v1/extracoes
  → pré-processamento (CPU) ───── reprovada ─────→ 422 com motivo
  → leitura visual (VLM)  ∥  leitura de texto (OCR clássico)
  → ancoragem + dígitos verificadores ── falhou ──→ releitura dirigida (recorte + modelo de escalada)
  → motor de regras
  → decisão: APROVADO | REVISAO | 422
  → resposta 200 + pacote de auditoria (com TTL) + caso em revisão (quando houver)
```

O mesmo fluxo roda em produção e no avaliador sobre o gabarito. A esteira é uma função independente do FastAPI. Em produção ela roda no modo assíncrono: `POST /v1/extracoes/assincronas` responde 202 e enfileira (Postgres), o `trabalhador` processa com concorrência igual às instâncias do OCR, e `GET /v1/extracoes/{id}` devolve o resultado. O `POST /v1/extracoes` síncrono existe para uso local e fica desligado por padrão (`EXTRACAO_SINCRONA`).

Cada provider é um adaptador trocado por configuração, atrás de três interfaces (`Protocol`) em `app/providers/interfaces.py`:

| Interface | Entrega | Adaptador inicial |
|---|---|---|
| `LeitorVisual` | subtipo, UF, modelo, lado e transcrição dos campos no schema do subtipo | OpenAI |
| `LeitorTexto` | palavras com caixa, confiança e id | PaddleOCR em CPU |
| `Localizador` | caixa de cada campo, para recortar e reler | caixas do OCR clássico |

Layout proposto:

```
app/
  api/            rotas FastAPI, modelos do contrato, mapeamento de erros para HTTP
  core/           pipeline.py, ancoragem.py, releitura.py, decisao.py
  preprocess/     decodificação, PDF/HEIC, orientação, recorte, métricas de qualidade
  providers/      interfaces.py, openai/, paddleocr/, fake/ (determinístico, para testes)
  schemas/        definição declarativa dos campos por subtipo → schemas JSON
  prompts/        prompts versionados (o hash entra em versao_pipeline)
  regras/         motor de regras; tabelas/ com IBGE, órgãos emissores e CBO
  armazenamento/  Postgres (metadados), S3 (artefatos), TTL e limpeza
  config.py       pydantic-settings lendo .env
  logs.py         logs estruturados com mascaramento de dados pessoais
  servico.py      cache → esteira → resposta → auditoria (fora do FastAPI)
  montagem.py     raiz de composição (configuração → providers → esteira)
avaliacao/        CLI do avaliador, gabarito, partição, estratificação, campo apagado, métricas e relatórios
tests/
docs/
```

## Stack

Do plano: Python, FastAPI, Pydantic, OpenCV, Pillow, PyMuPDF, PaddleOCR, validate-docbr, PostgreSQL, armazenamento compatível com S3, contêineres Docker escalando horizontalmente.

Adotado (ver `docs/decisoes.md`): Python 3.12, uv, pydantic-settings, SQLAlchemy 2 + Alembic, structlog, pypdfium2 (no lugar do PyMuPDF, AGPL), pillow-heif (HEIC), RustFS como S3 local (a imagem do MinIO saiu do Docker Hub), pytest, moto, ruff, mypy; na Fase 1, OpenCV (pré-processamento) e o SDK `openai` 3.x (Responses API); na Fase 2, PaddleOCR 3.x + PaddlePaddle 3.x em CPU (extra `ocr`), o que troca o `opencv-python-headless` pelo `opencv-contrib-python` exigido pelo PaddleX; na Fase 3, validate-docbr 2.0.1 (dígitos verificadores).

## Convenções

- Nomes do contrato e do domínio em português, idênticos aos do plano (`tipo_documento`, `status_leitura`, `versao_pipeline`…).
- Toda configuração passa por `app/config.py`. Limiares, preços por modelo, câmbio, nomes de modelo e limites de concorrência são configuração, nunca constantes no código.
- Os campos de cada subtipo vêm de uma única definição declarativa (nome, crítico ou informativo, rótulos esperados, impresso ou manuscrito, tipo do valor, normalizador). Schema, decisão e avaliação leem dela.
- Toda regra tem id, versão, vigência, fonte e testes nas fronteiras.
- `versao_pipeline` identifica provider, modelo, hash do prompt, versão dos schemas e versão das regras: qualquer resultado precisa ser reproduzível a partir dele.

## Como trabalhar

- O projeto anda em fases com portões (`docs/prompts-por-fase.md`). Não comece a fase seguinte sem eu aprovar o portão.
- Antes de codar: leia o trecho do plano indicado na fase, proponha um plano curto (arquivos, decisões, riscos, dúvidas) e espere aprovação.
- APIs e bibliotecas externas mudam. Antes de usar OpenAI, PaddleOCR, validate-docbr ou qualquer SDK, confira a documentação atual e a versão instalada; não confie em memória. Sem acesso para conferir, pergunte.
- Requisito ambíguo ou ausente no plano: pergunte. Toda decisão tomada vai para `docs/decisoes.md` (data, decisão, motivo, alternativa descartada).
- Commits pequenos, uma entrega por commit, com testes, lint e tipos passando.
- Ao fim de cada fase: `docs/relatorios/fase-N.md` com o que foi entregue, métricas do portão, desvios do plano e pendências. Depois pare.

## Comandos

Atualize esta seção à medida que os comandos existirem.

- `make setup`: dependências, `.env` a partir do `.env.example` e gabarito sintético de demonstração
- `make up` / `make down`: api, trabalhador, postgres, s3 (RustFS) e o job de limpeza via Docker Compose; o serviço `preparar` aplica as migrações e configura o bucket
- `uv run trabalhador`: processa a fila do modo assíncrono (escala com mais containers)
- `make setup` instala o extra `ocr` (PaddleOCR); os pesos vêm do Hugging Face no primeiro uso, ou já estão na imagem Docker
- `make test`: testes sem dependências externas (fakes, SQLite, S3 simulado com moto)
- `make test-ocr`: o teste que roda o PaddleOCR de verdade (marcador `ocr_real`)
- `make test-integracao`: testes contra o postgres e o s3 do compose
- `make lint`: ruff, formatação e mypy
- `make openapi`: regenera `docs/openapi.json`; o teste de contrato falha se ele divergir
- `make avaliar CONFIG=<arquivo> PARTICAO=ajuste MODO=padrao|flex|batch`: o padrão roda o gabarito sintético com os fakes; rodadas OpenAI em `avaliacao/configs/openai-*.toml`
- `make comparar RELATORIOS="<a>/relatorio.json <b>/relatorio.json"`: rodadas lado a lado (desenho, modelo, esforço)
- `uv run gabarito {sintetico,importar-csv,particionar,validar,estratificar,apagar-campos}`: ver `docs/gabarito.md`
- `uv run limpeza [--loop]`: job de limpeza do TTL
- `uv run medir-ocr --imagens <dir> [--limite N]`: tempo e memória do PaddleOCR por imagem (rodar no container)
- `uv run revisao exportar|importar`: casos em revisão para conferência humana e de volta ao gabarito (ver `docs/gabarito.md`)
- `uv run tabelas municipios [--arquivo]` / `uv run tabelas cbo --arquivo`: atualiza as tabelas das regras (`app/regras/tabelas/fontes.toml`)

## Decisões em aberto — não decidir sozinho

Do plano:
- Nomes exatos do enum `tipo_documento` e dos campos que o sistema de destino usa: [A PREENCHER]
- Código HTTP para falha de extração: 422 (sugerido) ou 400; fica configurável em `HTTP_STATUS_FALHA_EXTRACAO`.
- ~~Tempo máximo de resposta aceito pelo chamador, que define se o modo assíncrono entra já~~: resolvido em 2026-11-12, o modo assíncrono entra já (decisão 88).
- Modelo inicial: gpt-6.1-terra, a confirmar pelo baseline.
- Leitor de texto: PaddleOCR em CPU ou serviço em nuvem, decidido pela taxa de ancoragem no gabarito.
- TTL padrão dos artefatos e quem acessa os casos em revisão.
- Tamanho do gabarito e quantidade mínima de exemplos por subtipo e UF.

Levantadas na preparação do repositório:
- Onde está o gabarito e em que formato: [A PREENCHER]
- ~~`NAO_VERIFICAVEL` em campo crítico bloqueia o `APROVADO`?~~: resolvido em 2026-11-12, bloqueia (configurável); todo RG fora de SP vai para revisão (decisão 101).
- Contratos manuscritos da CTPS física: a tabela de campos os trata como informativos, mas a seção "Nunca fazer" os chama de campo manuscrito crítico.
- ~~Campo crítico `AUSENTE`: 422 ou revisão?~~: resolvido em 2026-11-12, revisão, configurável em `CRITICO_AUSENTE` (decisão 101).
- Na releitura, "duas de três leituras concordam" sem o OCR entre elas (VLM principal + escalada) basta para aprovar? O padrão adotado é não: o campo fica em revisão, para respeitar "nenhum valor aprovado sem aparecer nas duas leituras".
- PDF com várias páginas (CTPS): quais páginas processar e como juntar os campos.
- Autenticação da API: o plano não define.
- ~~PyMuPDF é AGPL~~: resolvido na Fase 0 com pypdfium2.
