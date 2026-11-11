# Decisões

Formato: data · decisão · motivo · alternativa descartada. **[confirmar]** = tomada para
não travar a fase, mas precisa do seu aval.

## 2026-11-02 · Fase 0

### Projeto e stack

1. **Python 3.12 + uv**, com versões com limite superior no `pyproject.toml` e lockfile
   (`uv.lock`). Segue a sugestão do CLAUDE.md. *Descartado:* Python 3.13 (disponível no
   ambiente, mas fora da sugestão).
2. **pypdfium2 no lugar do PyMuPDF.** O PyMuPDF é AGPL; o pypdfium2 é Apache-2.0/BSD-3.
   *Descartado:* PyMuPDF.
3. **RustFS como S3 local no lugar do MinIO.** A imagem `minio/minio` não existe mais no
   Docker Hub (a API devolve 404, conferido em 2026-11-02) e o quay.io recusou o acesso. O
   RustFS 1.0.4 (Apache-2.0) foi testado aqui com SSE-S3, criptografia padrão do bucket,
   lifecycle, cópia e remoção. Em produção, qualquer S3 com SSE ou KMS serve.
   *Descartado:* SeaweedFS (sem teste de SSE neste ambiente) e Garage (AGPL).
4. **HEIC via pillow-heif** (BSD-3; usa libheif, LGPL, como biblioteca dinâmica).
5. **Logs com structlog em JSON.** O mascaramento se aplica também ao `logging` padrão
   (uvicorn, alembic) e tem duas camadas: chaves sensíveis e padrões no texto livre (CPF,
   CNPJ, datas, RG, sequências longas de dígitos). O timestamp entra depois do
   mascaramento. Nome em texto livre não tem padrão confiável: a regra é nunca pôr valor
   de campo em mensagem.

### Contrato

6. **Enums propostos e aprovados:** `subtipo` (com `NAO_IDENTIFICADO`), `lado`
   (`FRENTE`, `VERSO`, `FRENTE_E_VERSO`, `PAGINA`, `NAO_IDENTIFICADO`), `erro`,
   `acao_sugerida` e motivos de campo. O enum `tipo_documento` ficou só com
   `IDENTITY_DOCUMENT`, `CTPS` e `CPF`, até a lista oficial chegar.
7. **[confirmar] Itens fora do plano no contrato:**
   - motivo `REQUISICAO_MALFORMADA` (400) para JSON inválido, campo obrigatório ausente
     ou campo extra (`extra="forbid"`);
   - `500 ERRO_INTERNO` com `id_processamento`, para que todo erro traga o id.
8. **`null` explícito** em `valor` e `valor_normalizado` na resposta 200, como no exemplo
   do plano. No erro, `campo` e `detalhe` nulos são omitidos, também como no exemplo. O
   FastAPI remove `null` dos exemplos do OpenAPI, então os exemplos do plano são
   reinseridos na geração.
9. **Erros de validação não ecoam o conteúdo enviado**, porque ele pode ter dados pessoais.
   O 400 traz só o campo e o tipo do erro.
10. **Base64:** aceita prefixo `data:...;base64,` e quebras de linha. O limite de tamanho
    vale para o arquivo decodificado (`ARQUIVO_MAX_BYTES`, padrão 10 MB) e é conferido antes
    de decodificar, pelo `Content-Length` e pelo tamanho do texto. Resolução acima de
    `IMAGEM_PIXELS_MAX` também dá 413, com `detalhe: "resolução"`.
11. **[confirmar] PDF:** só a primeira página nesta fase. O número de páginas vai para o
    pacote de auditoria.

### Decisão do documento

12. **[confirmar] Campo crítico AUSENTE:** dá 422 só quando nenhum campo crítico foi lido.
    Se todos estão AUSENTE, o motivo é `LADO_SEM_DADOS`; havendo ILEGIVEL, é
    `CAMPO_CRITICO_ILEGIVEL`, um por campo. Com algum crítico lido, o documento vai para
    `REVISAO`.
13. **[confirmar] `NAO_VERIFICAVEL` em campo crítico bloqueia `APROVADO` por padrão.**
    Configurável em `APROVAR_CRITICO_NAO_VERIFICAVEL`. Com o bloqueio, todo RG fora de SP
    vai para revisão.
14. **Releitura "2 de 3" sem o OCR não aprova:** mantém o padrão do CLAUDE.md. A ancoragem
    é exigida de todo campo crítico; a dupla leitura de modelos diferentes é exigência a
    mais para os manuscritos.
15. **Regras de leitura no código:** `LEGIVEL` com `?` vira `PARCIAL`; `LEGIVEL` sem texto
    vira `ILEGIVEL`; `AUSENTE`/`ILEGIVEL` nunca devolvem valor, mesmo que o modelo tenha
    escrito algo; campo não devolvido pelo VLM vira `ILEGIVEL`; campo fora do subtipo é
    descartado. A normalização só roda em `LEGIVEL` e nunca troca caractere (O→0).
16. **Subtipo `NAO_IDENTIFICADO` → 422 `NENHUM_DOCUMENTO`.** Tipo detectado diferente do
    declarado, ou subtipo de outro tipo → 422 `TIPO_DIVERGENTE`, com ação
    `CORRIGIR_REQUISICAO`.

### Esteira e providers

17. **Schema da leitura visual:** um por tipo declarado, com a classificação (tipo
    detectado, UF, lado, qualidade da captura) e `documento` como `anyOf` por subtipo,
    cada variante com seus campos. A Fase 1 precisa conferir se a saída estruturada da
    OpenAI aceita esse `anyOf`; se não aceitar, a alternativa é classificar e transcrever
    em chamadas separadas.
18. **`versao_pipeline`:**
    `<serviço>+<provider>:<modelo>+escalada:<modelo>+ocr:<provider>+prompt:<hash8>+schemas:<v>+ancoragem:<v>+regras:<v>`.
    Quando o fallback responde, aparece o provider que de fato rodou.
19. **Fakes:** a chave é o sha256 do arquivo recebido. Imagem sem gravação devolve
    `NAO_IDENTIFICADO` sem campos; o fake nunca inventa valor.
20. **Modelo sem preço em `PRECOS_MODELOS` é erro**, porque um custo escondido é pior que
    uma falha. Chamada com zero tokens não consulta preço.
21. **Tempo limite e fallback** ficam numa composição comum a todos os providers. As novas
    tentativas com espera crescente ficam dentro de cada adaptador (Fase 1). Falha do OCR
    também gera 502/504.
22. **Localizador `ocr` é stub** até a Fase 2: não devolve caixas, então não há recortes.

### Armazenamento, auditoria e retenção

23. **Prefixos:** `ttl-8h/`, `ttl-16h/`, `sem-expiracao/` e `revisao/`. O lifecycle de 1 dia
    nos prefixos de TTL, e de TTL+1 dia em `revisao/`, é só rede de segurança. Quem garante
    o TTL é o job `limpeza`, que apaga primeiro os objetos e depois o metadado; se apagar os
    objetos falhar, ele tenta de novo na rodada seguinte.
24. **Criptografia em repouso:** todo `PutObject` e `CopyObject` sai com SSE (`AES256` ou
    `aws:kms`), o bucket tem criptografia padrão e o acesso público é bloqueado onde o S3
    permite. *Descartado:* criptografia no cliente, que complica o acesso de auditoria
    sem ganho claro sobre SSE com KMS.
25. **Caso em revisão:** cópia do pacote em `revisao/<id_caso>/`, com registro próprio e
    `CASOS_REVISAO_TTL` próprio. Recusas 422 não viram caso de revisão.
26. **`chamadas_provider` sem chave estrangeira:** não têm dado pessoal e ficam depois da
    expiração, para o histórico de custo.
27. **Cache:** a chave é sha256 + tipo + `versao_pipeline` configurada. Reaproveita 200 e
    422, nunca 5xx nem resultado vindo do fallback. A resposta do cache ganha
    `id_processamento` novo, registra `origem_cache_id`, custo zero, e expira junto com o
    pacote original.
28. **[confirmar] Falha ao gravar o pacote ou o metadado não bloqueia a resposta:** o erro
    vai para o log, o resultado não entra no cache e o processamento fica sem pacote.
    *Alternativa:* responder 500 quando a auditoria falhar.
29. **Requisições recusadas na validação do schema** (400 antes do serviço) não são
    registradas no banco. As que passam pela esteira são todas registradas, inclusive
    400 e 413 de decodificação, sem pacote.

### Gabarito e avaliador

30. **Formato do gabarito** em `docs/gabarito.md`, aprovado na proposta.
31. **Partição:** 20% de holdout, por estrato, com as vagas restantes distribuídas pelos
    maiores restos. Sem isso, estratos com 1 item nunca iriam para o holdout.
32. **Campo apagado:** por padrão, 1 campo crítico por item (escolhido pelo hash) e os dois
    métodos (borrar → `ILEGIVEL`; apagar → `AUSENTE`). PDF fica de fora nesta fase. As
    variantes ficam fora das métricas principais; a alucinação é reportada com e sem elas.
33. **Erro escapado** só existe em documento `APROVADO`. Com os stubs, o valor é `0/0`
    (sem significado até a Fase 1); o relatório avisa quando há stub ativo.
34. **Batch/Flex:** o modo existe na interface (`ModoChamada`, `--modo`, preço com
    `fator_batch`/`fator_flex`); rodar fora do padrão falha com mensagem da Fase 1.

## 2026-11-03 · Portão da Fase 0

35. **Portão da Fase 0 aprovado** pelo responsável, com todas as decisões marcadas
    **[confirmar]** acima aceitas como estão (itens 7, 11, 12, 13 e 28). Continuam em aberto:
    a localização e o formato do gabarito real, o mínimo de exemplos por estrato, os nomes
    dos campos e do enum `tipo_documento` no sistema de destino e o modelo dos contratos da
    CTPS física. A Fase 1 começa quando chegar o prompt dela (`docs/prompts-por-fase.md`
    não está no repositório).

## 2026-11-03 · Fase 1

### Aprovadas no plano da fase

36. **Rotação de 90°/180°** é pedida ao VLM na classificação (`rotacao`: 0, 90, 180, 270 ou
    `NAO_IDENTIFICADO`); a imagem é girada antes dos recortes e, no desenho em duas
    etapas, antes da extração. *Descartado:* Tesseract OSD (nova dependência de sistema).
    Efeito para a Fase 2: no desenho único, o OCR roda em paralelo sobre a imagem ainda não
    girada; no desenho em duas etapas, o OCR já recebe a imagem de pé.
37. **"Modelo" do documento** (`modelo_documento`, texto livre curto) fica só no pacote de
    auditoria. O contrato não muda.
38. **Nomes de modelo** seguem o plano: gpt-6.1-terra, gpt-6-luna, gpt-6.1-sol e
    gpt-6-astra. Todos aparecem nos tipos do SDK `openai` 3.44.0 instalado.

### Pré-processamento

39. **OpenCV headless 4.x** (`>=4.12,<5`). A versão 5.0 saiu há pouco; a troca fica para
    quando houver motivo.
40. **Localização do documento:** filtro de mediana, Canny e fechamento morfológico, e
    depois o maior quadrilátero convexo com área entre `DOCUMENTO_AREA_MIN` e
    `DOCUMENTO_AREA_MAX`, com correção de perspectiva. Sem quadrilátero plausível, a
    imagem inteira segue: não localizar nunca reprova.
41. **Métricas de qualidade** (limiares provisórios e conservadores, todos em
    configuração, a calibrar na partição de ajuste):
    - conteúdo: desvio-padrão do cinza; abaixo do limiar, `NENHUM_DOCUMENTO`;
    - nitidez: variância do Laplaciano nas bordas, em escala fixa de 1.000 px; abaixo do
      limiar, `IMAGEM_DESFOCADA`;
    - reflexo: maior mancha estourada (≥ 250) que seja compacta, sem texto dentro, menor
      que metade da imagem e cercada por pixels mais escuros; acima do limiar,
      `REFLEXO_EXCESSIVO`. Fundo branco e prints não contam;
    - corte: o documento que sai do quadro não fecha contorno. A moldura da imagem fecha o
      contorno, e só contam regiões que tocam um ou dois lados do quadro (três ou quatro
      lados é fundo ou barra de status de print). Acima do limiar, `DOCUMENTO_CORTADO`.
42. **Limite de resolução:** o documento recortado cabe em 1600 × 1000 px (respeitando a
    orientação) e nunca é ampliado. São 1.600 patches de 32 px, a estimativa do plano.
43. **Heurísticas de captura** (só sinalizam):
    - print: muitos pixels idênticos ao vizinho e quase nenhum grão;
    - cópia: saturação baixa sem ser print.

    `SINALIZACAO_CAPTURA_FONTE` combina com o VLM (`vlm`, `heuristica`, `qualquer` ou
    `ambas`). O padrão provisório é `qualquer`; o avaliador mede a precisão de cada fonte.
44. **PNG com `compress_level=1`** em todas as imagens internas: sem perda,
    determinístico e várias vezes mais rápido.

### Leitor visual e esteira

45. **Interface do leitor visual simplificada:** o provider recebe imagem, instruções e
    schema e devolve o JSON com as métricas. A interpretação (`app/schemas/respostas.py`)
    é comum a todos os providers. JSON fora do schema é tratado como falha do provider
    (502), nunca como valor.
46. **Dois desenhos atrás de `LEITOR_VISUAL_DESENHO`:**
    - (a) `unico`: classificação e `documento` como `anyOf` por subtipo;
    - (b) `duas_etapas`: classificação com `LEITOR_VISUAL_MODELO_CLASSIFICACAO`, recusa
      cedo de tipo divergente (sem pagar a extração) e extração com o schema do subtipo.

    O padrão fica `unico` até a comparação no gabarito real.
47. **`LEITOR_TEXTO_PROVIDER=nenhum`** para a Fase 1. Sem segundo leitor nada ancora,
    então nada é aprovado (invariante 1).
48. **`versao_pipeline`** passa a incluir o desenho (e o modelo de classificação) e o
    esforço de raciocínio. `VERSAO_SCHEMAS` sobe para 0.2.

### Adaptador OpenAI

49. **Conferido no SDK `openai` 3.44.0 instalado** (tipos e docstrings gerados da
    especificação oficial):
    - Responses API com `text.format = {type: json_schema, strict: true}`;
    - `detail` aceita `high`, `low`, `auto` e `original`;
    - `reasoning.effort` aceita `none`, `minimal`, `low`, `medium`, `high`, `xhigh` e `max`;
    - `service_tier="flex"`;
    - Batch em `/v1/responses` com janela de 24 h;
    - `usage.output_tokens_details.reasoning_tokens` vem dentro de `output_tokens`, então
      os tokens de raciocínio são cobrados como saída.
50. **[confirmar] Não verificado:** a rede do ambiente bloqueia `developers.openai.com` e
    `api.openai.com`. Ficam em configuração até a conferência:
    - tamanho do patch e multiplicador por modelo (`MODELOS_IMAGEM`; padrão 32 px e 1,2,
      conservador);
    - limite de 30.000 patches;
    - suporte a `anyOf` dentro do objeto raiz na saída estruturada estrita;
    - preços (`PRECOS_MODELOS`, os do plano) e desconto de Batch/Flex (50%);
    - se todos os modelos aceitam `effort="none"`.
51. **Política de novas tentativas:**
    - erros transitórios: 429, 5xx e falha de conexão ou timeout;
    - até `PROVIDER_TENTATIVAS_MAX` tentativas (4), com espera exponencial de 0,5 s a 8 s
      e jitter de 0,5 a 1,5×, tudo dentro de `PROVIDER_TIMEOUT_S`;
    - as novas tentativas automáticas do SDK ficam desligadas (`max_retries=0`);
    - 4xx (exceto 429) falha na hora;
    - resposta incompleta, recusa do modelo ou JSON inválido dão 502.
52. **`store: false`** em toda chamada: o registro de auditoria é nosso, não do provider.
53. **Imagem acima do limite de patches** é recusada localmente, antes de enviar, com 413
    `ARQUIVO_GRANDE_DEMAIS` (`detalhe: "limite de imagem do provider"`). Com o limite de
    resolução da decisão 42, isso não deve acontecer.
54. **O custo usa o preço do modelo pedido**, mesmo quando a resposta traz o nome com data
    (`gpt-6.1-terra-2026-...`).

### Avaliador

55. **Modo medição:** todo campo `LEGIVEL` é tratado como aprovado direto, sem segundo
    leitor nem regras. Dele saem o erro escapado e a alucinação usados como baseline das
    Fases 2 e 3.
56. **Batch:**
    - `ColetorBatch` implementa a mesma interface e junta num lote as chamadas feitas
      enquanto não há ociosidade; o desenho em duas etapas gera dois lotes;
    - o estado fica em `saidas/batch/<nome>-<partição>`, com as respostas guardadas pelo
      hash do corpo da requisição;
    - a retomada reaproveita respostas prontas e volta a acompanhar os lotes em andamento;
    - falhas e lotes expirados são reenviados na rodada seguinte;
    - Batch só existe para o provider OpenAI; Flex vai por `service_tier` em cada chamada.
57. **Relatórios usam id de caso** (os 12 primeiros caracteres do sha256 do arquivo), não o
    nome do arquivo, que pode conter dado pessoal.
58. **Comparação do custo com o plano:** a tabela do plano inclui escalada em 30% dos
    documentos, e a Fase 1 não relê. O relatório mostra os dois números lado a lado.
