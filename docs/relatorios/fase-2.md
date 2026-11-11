# Fase 2 · Segundo leitor · relatório

Data: 2026-11-11 · branch `claude/charming-ride-4iq73l`

**Situação:** a implementação está pronta e testada. Como na Fase 1, as métricas saíram do
gabarito sintético, porque o gabarito real ainda não está disponível. O portão é avaliado
junto com a Fase 3: até lá, as regras e a checagem de dígitos são stubs e nenhum documento
chega a `APROVADO`.

As quatro dúvidas do plano não foram respondidas e seguiram a proposta (decisões 59 a 62,
marcadas **[confirmar]**):
- `PARCIAL` definido como no plano;
- trabalho nesta branch, sem tocar em `app/regras/`;
- medição só sintética;
- pesos do OCR dentro da imagem Docker.

## O que foi entregue

| Item | Onde |
|---|---|
| Adaptador PaddleOCR 3.7 em CPU (extra `ocr`): modelo carregado uma vez por instância, aquecimento no startup, inferência num pool de threads, palavras com caixa, confiança e id estável | `app/providers/paddleocr/` |
| Teste de contrato comum a todo `LeitorTexto` (fake e PaddleOCR; a Fase 4 só acrescenta o seu) | `tests/test_leitor_texto.py` |
| Imagem Docker com os pesos (sobe sem rede) e `medir-ocr` para tempo e memória por imagem | `Dockerfile`, `avaliacao/medir_ocr.py` |
| Leituras em paralelo (VLM ∥ OCR). Documento girado no desenho único: o OCR lê de novo a imagem de pé | `app/core/pipeline.py` |
| Ancoragem real: rótulo, região, tolerâncias, confusões por tipo, coringa `?`, evidência na auditoria | `app/core/ancoragem.py` |
| Localizador por OCR (evidência da ancoragem ou região do rótulo) | `app/core/localizacao.py` |
| Releitura dirigida: recorte ampliado, modelo de escalada, schema de um campo, votação 2 de 3 com o OCR obrigatório para aprovar | `app/core/releitura.py`, `app/prompts/releitura.md` |
| Interface com a Fase 3: `ChecadorDigitos` (pendente até o portão 2+3) | `app/core/digitos.py` |
| Decisão: valor da concordância, manuscrito por dupla leitura, `LEITURAS_DIVERGENTES`, regra ausente nunca aprova | `app/core/decisao.py` |
| Casos em revisão: exportar para conferência e importar a correção no gabarito | `avaliacao/revisao.py`, `docs/gabarito.md` |
| Avaliação: taxa de ancoragem por campo, subtipo e qualidade, taxa de releitura, custo da escalada, modo segundo leitor e campo apagado com as caixas do OCR | `avaliacao/metricas.py`, `relatorio.py`, `campo_apagado.py` |
| Configurações OpenAI + PaddleOCR (medium e mobile) | `avaliacao/configs/openai-terra-unico-paddleocr-*.toml` |

**Verificação:**
- `make lint` limpo (ruff e mypy strict).
- **305 testes** passando sem chamada real. Ficam 3 fora por padrão: integração, API real
  e PaddleOCR real. O do PaddleOCR (`make test-ocr`) passa aqui.
- No Docker Compose, a API sobe com o PaddleOCR (aquecimento de 3,5 s), fica saudável e
  processa um cartão sintético ponta a ponta: o OCR real ancorou os 15 campos lidos.

## Métricas

### Gabarito sintético (VLM fake, partição de ajuste, 38 documentos)

| Métrica | OCR fake | PaddleOCR medium | PaddleOCR mobile |
|---|---|---|---|
| Taxa de ancoragem (antes da releitura) | 97,3% | 97,3% | 93,3% |
| Taxa de ancoragem dos críticos | 99,4% | 99,4% | 99,4% |
| Ancoragem efetiva (depois da releitura) | 97,6% | 97,6% | 93,6% |
| Ancorados com valor errado | 0 | 0 | 0 |
| Valor certo sem ancoragem | 4 | 4 | 16 |
| Taxa de releitura (críticos lidos) | 2,5% (4/160) | 2,5% | 2,5% |
| Corrigidos pela releitura (OCR + escalada) | 4 | 4 | 4 |
| Erro escapado no modo segundo leitor (sem regras) | 0/160 | 0/160 | 0/160 |
| Erro escapado no modo medição (só o VLM) | 1/157 (0,64%) | — | — |
| Custo da escalada por documento | R$ 0,0013 (3,5% do total) | igual | igual |
| Latência do OCR p50 / p95 (host, 4 CPUs) | — | 11,3 s / 16,0 s | 4,8 s / 7,6 s |

Ancoragem por qualidade, PaddleOCR medium × mobile:

| Qualidade | Medium | Mobile |
|---|---|---|
| foto | 97,7% | 97,0% |
| print | 96,5% | 91,2% |
| xerox | 97,2% | 89,7% |

Como ler os números:
- **O que não ancora com o medium:** 4 campos MRZ, que não têm rótulo impresso e ficam
  `PARCIAL` por definição (decisão 77). Os demais são o valor inventado pelo VLM em campo
  ausente e o dígito trocado, que é o que a ancoragem deve barrar.
- **O que o mobile perde a mais:** as linhas de filiação, na xerox e no print.
- **Nenhum valor errado foi confirmado pelo OCR.** O dígito trocado gravado no VLM ficou
  `PARCIAL`, foi relido e corrigido pela concordância entre OCR e releitura.
- **Limites do sintético:** os cartões são limpos, têm fonte uniforme e não têm layout real.
  As taxas reais vão ser menores, sobretudo em RG plastificado, xerox e datilografado (risco
  do plano). Estes números medem a mecânica e a ordem de grandeza do OCR, não a
  qualidade em produção.

### Tempo e memória do OCR no container

Medido com 4 CPUs, 12 cartões de 1100 × 720 e uma instância:

| Modelo | Carga + aquecimento | p50 | p95 | Pico de memória |
|---|---|---|---|---|
| PP-OCRv6 medium (padrão, `lang=pt`) | 3,0 s | 11,3 s | 16,1 s | 1,46 GB |
| PP-OCRv5 mobile (latino) | 3,6 s | 5,2 s | 8,8 s | 0,82 GB |

A imagem Docker passou de 438 MB para 2,73 GB (PaddlePaddle e pesos).

## Desvios do plano

1. **`opencv-contrib-python` no lugar do headless** (decisão 64): o PaddleX exige esse
   pacote pelo nome. Ele precisa de `libgl1` na imagem.
2. **Documento girado: o OCR roda de novo, em vez de girar as caixas** (decisão 69). O OCR
   de texto deitado não serve, mesmo com as caixas giradas.
3. **Caixas por palavra vindas do próprio PaddleOCR** (`return_word_box`), não da divisão
   proporcional da linha (decisão 66).
4. **Releitura também para crítico com leitura `PARCIAL`** (caractere duvidoso). O prompt
   da fase diz "NAO_ANCORADO ou PARCIAL"; o código aplica as duas leituras (decisão 79).
5. **Modo medição redefinido sobre a leitura do VLM principal** (decisão 85), para continuar
   comparável à Fase 1 agora que a releitura muda o valor final.
6. **Achado de segurança corrigido:** com `APROVAR_CRITICO_NAO_VERIFICAVEL=true`, o stub de
   regras deixaria a ancoragem real aprovar documentos. Agora `REGRA_NAO_IMPLEMENTADA`
   bloqueia sempre (decisão 71).

## Riscos e pendências

1. **Latência do OCR.** De 5 a 16 s por imagem em CPU, sem MKLDNN, que quebra no Paddle
   3.3.1 (decisão 65). Isso pesa mais que o VLM e pode exigir o modo assíncrono (decisão em
   aberto sobre o tempo máximo aceito pelo chamador). Opções:
   - modelo mobile;
   - limitar o lado da detecção;
   - testar o MKLDNN nas próximas versões do Paddle;
   - um leitor de texto em nuvem (Fase 4).
2. **Fila do OCR conta para o tempo limite** (decisão 87). Com uma instância por processo,
   requisições simultâneas podem estourar `PROVIDER_TIMEOUT_S`. A concorrência por container
   precisa casar com `PADDLEOCR_INSTANCIAS`.
3. **Calibração no gabarito real:**
   - região da ancoragem por subtipo, similaridade do rótulo e lista de confusões;
   - escolha medium × mobile;
   - taxa de ancoragem por subtipo, UF e qualidade.
4. **Integração com a Fase 3 no portão 2+3:**
   - trocar `checar_pendente` pela função de `app/regras/digitos.py`;
   - ligar o motor de regras real;
   - rodar o gabarito com as duas.
5. **Decisões em aberto que esta fase tocou:**
   - "VLM + releitura sem o OCR" fica em revisão;
   - contratos manuscritos da CTPS continuam informativos;
   - `NAO_VERIFICAVEL` em campo crítico.
6. **Rede deste ambiente:** liberei `us.aws.cdn.hf.co` (pesos do Hugging Face) e
   `deb.debian.org` (apt no build da imagem).

## Como rodar com dados reais

1. Importe e particione o gabarito real (`docs/gabarito.md`).
2. Rode as configurações da Fase 1 (baseline) e da Fase 2 na partição de ajuste:
   ```bash
   make avaliar CONFIG=avaliacao/configs/openai-terra-unico-paddleocr-medium.toml MODO=batch
   ```
   ```bash
   make avaliar CONFIG=avaliacao/configs/openai-terra-unico-paddleocr-mobile.toml MODO=batch
   ```
3. Compare com a rodada da Fase 1:
   ```bash
   make comparar RELATORIOS="saidas/avaliacoes/*/relatorio.json"
   ```
4. Exporte os casos em revisão de produção ou de homologação, confira e importe:
   ```bash
   uv run revisao exportar --destino saidas/revisao
   ```
