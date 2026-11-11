# Fase 0 · Fundação · relatório

Data: 2026-11-02 · branch `claude/charming-ride-4iq73l`

**Situação do portão:** aprovado em 2026-11-03, com as decisões [confirmar] aceitas como estão (ver `docs/decisoes.md`, item 35). O gabarito real ainda não foi
estratificado porque ele não está no repositório nem neste ambiente. Toda a ferramenta
(importador, partição, estratificação, campo apagado e avaliador) está pronta e testada
sobre um gabarito sintético.

## O que foi entregue

| Item | Onde | Situação |
|---|---|---|
| Projeto: `pyproject.toml` (uv, Python 3.12), `Dockerfile` (CPU, multi-stage, usuário 10001), `docker-compose.yml` (api, postgres, s3, preparar, limpeza), `Makefile` | raiz | ✅ `make up` validado aqui: imagem de 438 MB, api *healthy* |
| Configuração com as variáveis do plano + tamanho máximo, banco, S3, SSE, preços por modelo, câmbio, concorrência e tokens/min por provider | `app/config.py`, `.env.example` | ✅ teste garante que o `.env.example` cobre toda a configuração |
| Contrato `POST /v1/extracoes` e `GET /health`, enums do plano, códigos 400/413/422 (configurável)/502/504 | `app/api/` | ✅ |
| OpenAPI exportado e teste de divergência | `docs/openapi.json`, `tests/test_contrato.py` | ✅ exemplos do plano validam contra o OpenAPI |
| `LeitorVisual`, `LeitorTexto`, `Localizador` (`Protocol`) com tokens, custo, modelo, latência e modo (padrão/batch/flex) | `app/providers/interfaces.py` | ✅ |
| Fakes determinísticos por hash da imagem | `app/providers/fake/` | ✅ sem gravação → `NAO_IDENTIFICADO`, nunca valor |
| Tempo limite, fallback e limites de concorrência/tokens por minuto | `app/providers/composicao.py`, `limites.py` | ✅ |
| Esteira completa | `app/core/pipeline.py` | ✅ decodificação e decisão reais; qualidade, ancoragem, releitura e regras em stub |
| Decisão real (invariante 1) | `app/core/decisao.py` | ✅ testada com entradas reais; com os stubs, nunca chega a APROVADO |
| Definição declarativa dos campos por subtipo e schemas do VLM (invariante 6) | `app/schemas/` | ✅ testes de schema estrito, sem `pattern/format/enum` no valor, com `NAO_IDENTIFICADO` |
| Postgres (SQLAlchemy 2 + Alembic): processamentos, casos em revisão, custo por chamada | `app/armazenamento/`, `migrations/` | ✅ teste falha se modelo e migração divergirem |
| Pacote de auditoria no S3 com SSE, prefixos por TTL, lifecycle de segurança, job de limpeza | `app/armazenamento/` | ✅ testado com moto e com RustFS + Postgres reais |
| Cache por hash + tipo + `versao_pipeline` | `app/servico.py` | ✅ |
| Logs estruturados com mascaramento | `app/logs.py` | ✅ teste ponta a ponta: CPF e nome sintéticos não aparecem em log |
| Formato do gabarito, importador CSV, partição holdout, estratificação, campo apagado | `avaliacao/`, `docs/gabarito.md` | ✅ |
| `avaliar --config --particao` com relatório Markdown + JSON | `avaliacao/` | ✅ Batch/Flex previstos na interface; implementação na Fase 1 |
| Gabarito sintético com gravações para os fakes | `uv run gabarito sintetico` | ✅ 48 itens, nenhum dado real |

**Verificação:** `make lint` (ruff, formatação e mypy strict) limpo · `make test` com **205
testes** passando · 1 teste de integração passando contra o Postgres e o S3 do compose.
Também validado no compose: requisição real 200, cache na segunda chamada (sem chamada a
provider, custo zero), 400 com `id_processamento`, TTL de 8 h no banco, caso em revisão
criado e job de limpeza rodando, sem CPF nos logs do container.

### Cobertura dos testes pedidos

| Pedido | Testes |
|---|---|
| Contrato (exemplos validam contra o OpenAPI) | `test_contrato.py`: OpenAPI versionado = gerado; exemplos do plano e respostas reais validam |
| Código HTTP de cada erro | `test_api_erros.py`: 400 (base64, tipo, formato, malformado, campo extra), 413 (arquivo, corpo, resolução), 422 (cada motivo + configurável para 400), 502, 504, 500, todos com `id_processamento` |
| TTL e limpeza | `test_armazenamento.py`: `expira_em` por TTL, limpeza só do vencido, caso em revisão com TTL próprio, `null` nunca limpo, falha ao apagar mantém metadado |
| Cache por hash | `test_cache.py`: reaproveita sem chamar provider; não reaproveita com outro tipo, outra versão, pacote expirado ou apagado, nem com erro de provider |
| Mascaramento de log | `test_logs.py`: padrões, chaves sensíveis, logging padrão, exceções e requisição completa |
| Avaliador ponta a ponta com os fakes | `test_avaliador.py`: relatório completo, alucinação no campo apagado detectada, partição estável, estratificação, importador, CLI |

## Como rodar

```bash
make setup
make test
make up
make avaliar
make estratificar MANIFESTO=<caminho do manifesto real>
```

`make setup` também gera o gabarito sintético; `make avaliar` usa o padrão
`CONFIG=avaliacao/configs/sintetico.toml PARTICAO=ajuste`.

## Relatório de estratificação

**Gabarito real: pendente**, porque não tenho acesso a ele (ver dúvida 1). Quando chegar:

```bash
uv run gabarito importar-csv --csv <gabarito.csv> --imagens <dir> --saida <dir>/manifesto.jsonl
uv run gabarito particionar --manifesto <dir>/manifesto.jsonl
uv run gabarito estratificar --manifesto <dir>/manifesto.jsonl --saida saidas/estratificacao
```

Abaixo, o relatório sobre o **gabarito sintético** (48 itens), só para mostrar o formato. O
gerador usa poucas UFs de propósito, para o relatório apontar lacunas.

- Itens: **48** · ajuste 38 · holdout 10
- Células (6 subtipos presentes × 27 UFs × 3 qualidades): 486 · **vazias 444** · **com
  menos de 5 exemplos 42** · ok 0
- Subtipos sem nenhum exemplo: CPF_COMPROVANTE, CPF_DIGITAL, CTPS_FISICA
- UFs sem nenhum exemplo: GO, MA, MT, MS, MG, PA, PB, PR, PE, PI, RJ, RN, RS, RO, RR, SC, SP, SE, TO
- Por qualidade: foto 27 · xerox 7 · print 14
- Por subtipo: 8 de cada (RG_ANTIGO, RG_2018_2022, CIN, CNH, CPF_CARTAO, CTPS_DIGITAL)

O relatório completo tem uma tabela UF × qualidade por subtipo, com `⚠` para células abaixo
do mínimo e `· 0` para vazias, mais um JSON com cada célula por partição. Com 27 UFs, 3
qualidades e 9 subtipos, são 729 células: até com 5 exemplos por célula, o gabarito
precisaria de 3.645 imagens. Isso pede decidir o mínimo por estrato agregado (dúvida 3).

## Avaliação de demonstração (sintético, partição ajuste, fakes)

Mede a esteira, não a qualidade: com os stubs, nada é aprovado.

| Métrica | Valor |
|---|---|
| Documentos | 38 (+ 68 variantes de campo apagado) |
| Desfechos | REVISAO 33 · 422: 5 (LADO_SEM_DADOS 4, CAMPO_CRITICO_ILEGIVEL 1) |
| Erro escapado | 0/0 críticos aprovados (sem significado até haver ancoragem e regras) |
| Alucinação em críticos aprovados | 0 ✅ |
| Alucinações gravadas de propósito no sintético (campos AUSENTE) | 5, todas detectadas |
| Acurácia (críticos) | 96,05%: erros e `?` injetados no sintético |
| Custo médio por documento | R$ 0,0347 (3.420 tokens de entrada + 600 de saída no gpt-6.1-terra a R$ 5,40/US$; bate com o plano sem escalada) ✅ |
| Latência p50/p95 | 132 ms / 525 ms (fakes; o grosso é pré-processamento e recortes) |

## Desvios do plano

1. **RustFS no lugar do MinIO** como S3 local: a imagem do MinIO não existe mais no Docker
   Hub. Testado com SSE-S3 e lifecycle (decisão 3).
2. **pypdfium2 no lugar do PyMuPDF** (licença AGPL; decisão 2).
3. **Dois itens fora do contrato do plano**, para aprovar: motivo
   `REQUISICAO_MALFORMADA` (400) e resposta `500 ERRO_INTERNO` com `id_processamento`
   (decisão 7).
4. **`tipo_documento` só com IDENTITY_DOCUMENT, CTPS e CPF**, até chegar a lista oficial.
5. **PDF só na primeira página** nesta fase (decisão 11).
6. **Schema do VLM com `anyOf` por subtipo**: a compatibilidade com a saída estruturada da
   OpenAI ainda precisa ser conferida na Fase 1 (decisão 17).

## Dúvidas para o portão

1. **Gabarito real:** onde está e em que formato? Deixei um importador CSV. Se o formato
   for outro, escrevo o leitor sem tocar nos originais. Ele não pode vir para o git; o
   caminho fica no TOML da rodada.
2. **Aprovação do contrato:** confirme os itens 3 e 4 dos desvios e os itens marcados
   **[confirmar]** em `docs/decisoes.md`:
   - 422 só quando nenhum crítico foi lido;
   - `NAO_VERIFICAVEL` bloqueia `APROVADO` por padrão;
   - falha de auditoria não bloqueia a resposta;
   - PDF só na primeira página.
3. **Mínimo de exemplos por estrato:** subtipo × UF × qualidade dá 729 células. Proponho
   exigir o mínimo em subtipo × qualidade (27 células) e em UF agregada por subtipo,
   reportando o cruzamento completo só como diagnóstico.
4. **Nomes dos campos e enum `tipo_documento`** do sistema de destino. Hoje são propostos
   em `app/schemas/campos.py`.
5. **Contratos da CTPS física:** modelei um contrato por imagem (`contrato_*`) como
   informativo. Uma página com vários contratos precisa de lista; confirmar antes da Fase 1.
6. **Autenticação da API** continua fora do escopo do plano. Fica para quando houver
   ambiente compartilhado?
