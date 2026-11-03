# Fase 1 · Baseline com GPT · relatório

Data: 2026-11-03 · branch `claude/charming-ride-4iq73l`

**Situação do portão: não atingido.** Toda a implementação da fase está pronta e testada,
mas o baseline real ainda não foi medido. Duas coisas bloqueiam:

1. **Rede.** O ambiente recusa `developers.openai.com` e `api.openai.com`, e não há
   `OPENAI_API_KEY` configurada. Sem isso não há chamada real nem conferência da
   documentação web.
2. **Gabarito real.** Não está no repositório nem neste ambiente.

As métricas por campo, subtipo, UF e qualidade, o custo em R$ contra o plano, a latência e
a decisão sobre o desenho do schema dependem dessa rodada. Os números abaixo vêm do gabarito
sintético com providers fake: medem a mecânica, não a qualidade.

## O que foi entregue

| Item | Onde |
|---|---|
| Pré-processamento em CPU: localização do documento e correção de perspectiva, métricas de nitidez, reflexo, corte e conteúdo com limiares em configuração (422 com o motivo), limite de ~1600 × 1000 px | `app/preprocess/documento.py`, `qualidade.py`, `preparacao.py` |
| Heurísticas de cópia e print, combinadas com a classificação do VLM (fonte configurável) | `app/preprocess/captura.py`, `app/core/decisao.py` |
| Rotação de 90°/180°/270° informada pelo VLM; imagem girada antes dos recortes e da extração | `app/core/pipeline.py` |
| Schemas: desenho (a) único com `anyOf` por subtipo e desenho (b) classificação + extração do subtipo; campos com o schema exato do plano | `app/schemas/campos.py` |
| Interpretação do JSON comum a todos os providers; JSON fora do schema dá 502 | `app/schemas/respostas.py` |
| Prompts versionados: leitura (único), classificação, extração e releitura, sem dados realistas | `app/prompts/` |
| Adaptador OpenAI (Responses API, saída estruturada estrita, `detail` configurável, esforço de raciocínio configurável com padrão `none`, novas tentativas com jitter em 429/5xx, custo com tokens de raciocínio, limite de patches local, `store: false`) | `app/providers/openai/` |
| Batch (envio, acompanhamento, retomada) e Flex | `app/providers/openai/batch.py`, `avaliacao/executor.py` |
| Avaliador: modo medição (baseline), acurácia de subtipo, UF e lado, precisão das sinalizações por fonte, tokens medidos × plano, custo × tabela do plano, piores casos por id | `avaliacao/metricas.py`, `relatorio.py` |
| `comparar`: rodadas lado a lado | `avaliacao/comparacao.py` |
| Configurações prontas: terra e luna × desenho único e em duas etapas | `avaliacao/configs/openai-*.toml` |
| Pacote de auditoria com a imagem enviada, o contorno, as métricas de qualidade e captura e a classificação | `app/armazenamento/auditoria.py` |

**Verificação:** `make lint` limpo (ruff e mypy strict). **262 testes** passando, sem
chamada real; 2 ficam de fora por padrão (integração e API real, que pede chave). Os testes
novos cobrem:
- pré-processamento: perspectiva, corte, desfoque, reflexo × fundo branco, prints, limite
  de resolução e rotação;
- esteira nos dois desenhos, incluindo a recusa cedo de tipo divergente;
- adaptador OpenAI com transporte HTTP simulado do SDK: corpo da requisição, custo com
  raciocínio, Flex, 429/5xx/4xx, timeout, resposta incompleta, recusa, JSON inválido e
  limite de patches;
- Batch com API simulada: lote único, retomada com respostas prontas, retomada de lote em
  andamento, falha parcial reenviada e lote expirado;
- avaliador: modo medição, Flex pela metade do preço e comparação dos desenhos.

O que foi conferido na OpenAI e o que ficou pendente está em `docs/decisoes.md`, itens 49 e 50.

## Demonstração no gabarito sintético (fakes, partição ajuste, 38 documentos)

| Métrica | Desenho único | Duas etapas |
|---|---|---|
| Subtipo, UF e lado corretos | 100% | 100% |
| Acurácia dos críticos | 95,12% | 95,12% |
| Erro escapado no modo medição | 0,64% | 0,64% |
| Alucinações em críticos no modo medição | 0 | 0 |
| Custo médio | R$ 0,0347 | R$ 0,0361 |
| Tokens de entrada por chamada | leitura 3.420 | classificação 2.300 + extração 3.420 |

Os tokens do sintético são valores gravados, não medidos. Servem para mostrar que o
relatório separa as chamadas e compara com o plano (3.420 de entrada, sendo 1.920 de imagem).

## Como produzir o baseline quando os bloqueios caírem

1. Libere `api.openai.com` e `developers.openai.com` na rede do ambiente e coloque a
   `OPENAI_API_KEY` nos segredos.
2. Antes de rodar, confira na documentação web e registre em `docs/decisoes.md`:
   patches e multiplicador por modelo, limite de patches, `anyOf` na saída estruturada
   estrita, preços e descontos de Batch/Flex, e `effort="none"` em cada modelo.
3. Rode o teste da API real com uma imagem sintética, para validar o schema:
   ```bash
   uv run pytest -m provider_real
   ```
4. Importe e particione o gabarito real:
   ```bash
   uv run gabarito importar-csv --csv <csv> --imagens <dir> --saida gabarito/manifesto.jsonl
   ```
   ```bash
   uv run gabarito particionar --manifesto gabarito/manifesto.jsonl
   ```
5. Calibre os limiares de qualidade e captura na partição de ajuste, olhando as métricas
   do relatório (`qualidade`, `captura`).
6. Rode as quatro configurações na partição de ajuste, de preferência em Batch:
   ```bash
   make avaliar CONFIG=avaliacao/configs/openai-terra-unico.toml MODO=batch
   ```
   ```bash
   make avaliar CONFIG=avaliacao/configs/openai-terra-duas-etapas.toml MODO=batch
   ```
   ```bash
   make avaliar CONFIG=avaliacao/configs/openai-luna-unico.toml MODO=batch
   ```
   ```bash
   make avaliar CONFIG=avaliacao/configs/openai-luna-duas-etapas.toml MODO=batch
   ```
7. Compare as quatro rodadas (custo, latência, classificação e erro no modo medição), e só
   então rode o holdout uma vez, com a configuração escolhida:
   ```bash
   make comparar RELATORIOS="saidas/avaliacoes/*/relatorio.json"
   ```

## Decisão sobre o desenho do schema

**Pendente da medição.** O que esperar de cada lado:
- (a) único: uma chamada por documento. O schema é maior (classificação + campos de todos
  os subtipos), e depende de a saída estruturada estrita aceitar `anyOf`.
- (b) duas etapas: mais uma chamada, de classificação, com um modelo barato. Recusa tipo
  divergente sem pagar a extração, a extração vê só os campos do subtipo e o OCR da Fase 2
  já recebe a imagem de pé.

A recomendação sai da comparação no ajuste. Se a saída estruturada não aceitar `anyOf`, (a)
fica descartado.

## Desvios do plano

1. **Interface do leitor visual:** o provider devolve JSON e a interpretação é do núcleo.
   Isso reforça a invariante 8 e deixa a releitura como mais uma chamada com o schema de
   um campo.
2. **Limiares de qualidade provisórios,** porque a calibração depende do gabarito real.

## Dúvidas e pendências

1. **Rede e chave:** libera os dois domínios da OpenAI e a `OPENAI_API_KEY`?
2. **Gabarito real:** continua pendente desde a Fase 0 (onde está e em que formato).
3. **Nomes dos campos e lista de `tipo_documento`** do sistema de destino, também
   pendente desde a Fase 0.
4. **Sinalizações de captura:** o padrão provisório (`qualquer`) favorece a revocação.
   Confirmo depois da medição de precisão por fonte?
