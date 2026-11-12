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

## 2026-11-11 · Fase 2

O plano da Fase 2 foi apresentado com quatro dúvidas. A ordem para seguir veio sem
resposta a elas; as quatro foram decididas pela proposta do plano e estão marcadas
**[confirmar]**.

### Dúvidas do plano

59. **[confirmar] Ancoragem PARCIAL:** o valor aparece no OCR fora da região do rótulo, ou
    sem rótulo encontrado, ou com diferença de um único caractere (edição) dentro da
    região num valor com pelo menos `ANCORAGEM_PARCIAL_TAMANHO_MIN` (8) caracteres. Um
    caractere de diferença fora da região é `NAO_ANCORADO`. *Descartado:* tratar qualquer
    aparição do valor como ANCORADO (o valor de outro campo, em outro lugar, confirmaria o
    errado).
60. **[confirmar] Branch:** a Fase 2 foi feita em `claude/charming-ride-4iq73l`, sem tocar
    em `app/regras/`. A Fase 3 vai para outra sessão e outra branch; a interface comum está
    na decisão 70.
61. **[confirmar] Medição só sintética:** sem gabarito real, a taxa de ancoragem medida é
    do gabarito sintético (com o OCR fake e com o PaddleOCR real). Tempo e memória do OCR
    foram medidos no container.
62. **[confirmar] Pesos do PaddleOCR dentro da imagem Docker** (baixados no build, em
    `/opt/paddlex`, só leitura para o usuário da aplicação): o container sobe sem rede e
    sem esperar download. A imagem passou de 438 MB para 2,73 GB. *Descartado:* volume
    baixado na primeira execução (a primeira requisição pagaria o download e o container
    precisaria de acesso ao Hugging Face).

### PaddleOCR

63. **PaddleOCR 3.7.0 + PaddlePaddle 3.3.1 (CPU) como extra `ocr`** (`uv sync --extra
    ocr`). Os testes não precisam dele: o adaptador importa o pacote só ao criar o motor.
    Conferido na versão instalada: `PaddleOCR(lang="pt")` escolhe PP-OCRv6 medium
    (detecção e reconhecimento latino) e baixa os pesos do Hugging Face.
64. **`opencv-contrib-python` no lugar do `opencv-python-headless`.** O PaddleX confere o
    nome desse pacote (`importlib.metadata`) ao importar os módulos de OCR, então o headless
    não serve, e os dois juntos brigam pelo mesmo módulo `cv2`. Versão travada pelo
    PaddleX (4.10.0.84). A imagem Docker ganhou `libgl1` e `libglib2.0-0`.
    *Descartado:* excluir o pacote com override do uv (o PaddleX recusa o OCR) e enganar a
    checagem de dependências (frágil, API privada).
65. **oneDNN (MKLDNN) desligado** (`PADDLEOCR_MKLDNN=false`): no Paddle 3.3.1 em CPU a
    inferência falha com `ConvertPirAttribute2RuntimeAttribute`, inclusive com
    `FLAGS_enable_pir_api=0`. Reavaliar a cada versão do Paddle: é o que mais pesa na
    latência.
66. **Palavras a partir de `return_word_box=True`:** o PaddleOCR devolve fragmentos por
    linha (palavras, espaços e pontuação) com caixa; fragmentos sem espaço entre si viram
    uma palavra (`01`, `/`, `02` → `01/02`). Id = ordem de leitura. Sem caixas por
    palavra, a linha é dividida proporcionalmente ao número de caracteres. *Descartado* (do
    plano): sempre dividir proporcionalmente.
67. **Uma instância do modelo por thread** num pool próprio (`PADDLEOCR_INSTANCIAS`,
    padrão 1, ~1,3 GB cada), com aquecimento no startup (`LEITOR_TEXTO_AQUECER`). Escala
    horizontal por container. Orientação do documento, retificação e orientação de linha
    do PaddleOCR ficam desligadas: a esteira já recorta e gira.
68. **Modelo padrão PP-OCRv6 medium; mobile como alternativa em configuração.** Medido no
    container (4 CPUs, 12 cartões sintéticos de 1100 × 720): medium p50 11,3 s, p95 16,1 s,
    pico 1,46 GB; PP-OCRv5 mobile (latino) p50 5,2 s, p95 8,8 s, pico 0,82 GB. A escolha
    sai da taxa de ancoragem no gabarito real.
69. **Desenho único com documento girado: o OCR roda de novo na imagem de pé.** O OCR de
    um documento deitado não serve (texto mal lido e caixas em outra orientação). Custa uma
    segunda leitura de OCR só nesses casos; a primeira fica na auditoria. *Descartado* (do
    plano): girar as caixas das palavras.

### Interface com a Fase 3

70. **Dígitos verificadores:** `app/core/digitos.py` define o `Protocol`
    `ChecadorDigitos(tipo_valor, valor_normalizado) -> VALIDO | INVALIDO | NAO_APLICAVEL`.
    A Fase 3 entrega a função em `app/regras/digitos.py`, sem nunca corrigir o valor; a
    esteira a recebe no construtor (`Esteira(..., checar_digitos=...)`). Até a integração
    no portão 2+3, `checar_pendente` devolve `NAO_APLICAVEL` e aparece em `stubs_ativos`.
71. **Regra não implementada nunca aprova.** Com `APROVAR_CRITICO_NAO_VERIFICAVEL=true`, o
    `NAO_VERIFICAVEL` do stub de regras contaria como validado e, com a ancoragem real,
    aprovaria documento sem regra nenhuma. A decisão agora bloqueia todo campo crítico com
    `REGRA_NAO_IMPLEMENTADA`, qualquer que seja a configuração.

### Ancoragem

72. **Comparação por chave:** maiúsculas, sem acento, só letras e dígitos (espaço e
    pontuação ignorados). Acento e cedilha também são ignorados, porque o OCR erra os dois
    (`EXPEDIÇÃO` lido como `EXPEDIO`). `?` do VLM casa com qualquer caractere.
73. **Confusões do OCR por tipo do campo** (pares letra-dígito em `ANCORAGEM_CONFUSOES`):
    em campo numérico (CPF, data, dígitos), a letra do OCR conta como o dígito; em texto,
    UF e categoria, o dígito do OCR conta como a letra; em alfanumérico (RG, série da CTPS,
    CNPJ), os dois sentidos. Só o texto do OCR é tolerado; o do VLM nunca muda.
74. **VLM com caractere incompatível com o tipo** (letra num CPF): ancoragem `PARCIAL` com
    `vlm_incompativel`, motivo `LEITURAS_DIVERGENTES` e `valor_normalizado` nulo. A busca
    no OCR continua, só para localizar o campo para a releitura.
75. **Rótulo reconhecido por semelhança** (`ANCORAGEM_ROTULO_SIMILARIDADE_MIN` = 0,8 sobre a
    chave), entre o `rotulo_visto` do VLM e os rótulos da definição do campo. Rótulo
    colado no valor na mesma palavra (`CPF:123…`) conta como dentro da região.
76. **Região em alturas do rótulo, configurável por subtipo** (`ANCORAGEM_REGIOES`): à
    direita na mesma linha até 25 alturas; abaixo até 3 alturas, com folga horizontal de 8.
    Valores provisórios, a calibrar no gabarito real.
77. **Campo sem rótulo impresso** (MRZ) nunca fica `ANCORADO`: no máximo `PARCIAL` (sem
    rótulo). Hoje todos são informativos.
78. **Manuscrito não passa pela ancoragem** (`NAO_APLICAVEL`): precisa de VLM principal e
    releitura, de modelos diferentes, concordando. A releitura de manuscrito crítico é
    sempre disparada. Hoje nenhum manuscrito é crítico.

### Releitura

79. **Disparo:** campo crítico lido (`LEGIVEL` ou `PARCIAL`) com ancoragem `NAO_ANCORADO`
    ou `PARCIAL`, com leitura `PARCIAL` (caractere duvidoso), com dígito verificador
    inválido ou manuscrito. Campo crítico `ILEGIVEL` ou `AUSENTE` não é relido (não há o
    que localizar). O prompt diz o nome do campo e os rótulos usuais, nunca valor.
80. **Votação** depois da normalização (código):
    - VLM = releitura e o OCR confirma um dos dois → `3_DE_3`, aprovável;
    - OCR confirma a releitura, VLM diferente → `2_DE_3`, vale o valor da releitura;
    - OCR confirma o VLM, releitura diferente → `2_DE_3`, vale o VLM;
    - VLM = releitura sem o OCR → `2_DE_3` sem OCR: fica em revisão (decisão em aberto do
      CLAUDE.md);
    - nada concorda → `LEITURAS_DIVERGENTES`.

    "O OCR confirma" usa o mesmo critério da ancoragem (exato, na região do rótulo). As
    regras rodam depois da releitura, sobre o valor final, sem novo ciclo.
81. **Recorte da releitura:** caixa do localizador com margem (`RELEITURA_MARGEM`, mínimo em
    px), ampliado até `RELEITURA_ALTURA_MIN_PX` (até 4×). O mesmo recorte vai para a
    auditoria.
82. **Falha na releitura não derruba o documento:** erro do provider, tempo esgotado ou
    resposta fora do schema deixam o campo sem releitura (e em revisão), com o motivo na
    auditoria.
83. **Localizador por OCR:** caixa da evidência da ancoragem; sem ela, a região do rótulo
    (à direita e abaixo); sem rótulo, sem caixa (o campo vai para revisão sem releitura).

### Casos em revisão e avaliação

84. **Casos em revisão por linha de comando** (`uv run revisao exportar|importar`). O modelo
    de correção vem com os valores em branco, para não induzir quem confere; as leituras
    ficam ao lado, em `leituras.json`. A importação confere o sha256 da imagem, recusa
    imagem repetida, grava com origem `revisao:<id_caso>` na partição de ajuste (padrão) e
    marca o caso como `CONFERIDO`. Erros de validação não ecoam valores.
85. **O modo medição passa a usar a leitura do VLM principal** (antes do segundo leitor),
    para continuar sendo o baseline da Fase 1. Novo "modo segundo leitor": aprova todo
    crítico `LEGIVEL` com ancoragem efetiva, sem as regras.
86. **Gabarito sintético** com os rótulos nas palavras do OCR, um caso de confusão O/0 e
    releituras gravadas (modelo de escalada) nos casos de dígito trocado e caractere
    duvidoso. As configurações sintéticas usam o localizador por OCR.
87. **Fila do OCR conta para o tempo limite:** com uma instância por processo, requisições
    simultâneas esperam o modelo, e a espera entra no `PROVIDER_TIMEOUT_S`. Em produção,
    concorrência por container ≈ `PADDLEOCR_INSTANCIAS`, ou o modo assíncrono.

## 2026-11-12 · Modo assíncrono (fora da sequência de fases, na mesma branch)

Pedido para diminuir a latência percebida e controlar a concorrência no OCR. Plano
aprovado com as sugestões para as cinco dúvidas.

88. **Modo assíncrono entra já** (resolve a decisão em aberto sobre o tempo de resposta do
    chamador):
    - `POST /v1/extracoes/assincronas` responde 202 com `id_processamento`, `situacao`,
      `url_consulta` (também no header `Location`), `posicao_fila` e `Retry-After`;
    - `GET /v1/extracoes/{id}` responde 202 enquanto não conclui; concluída, devolve o
      mesmo corpo e o mesmo código HTTP do síncrono; id desconhecido ou expirado dá 404
      `NAO_ENCONTRADO`/`EXTRACAO_NAO_ENCONTRADA`.

    *Descartado:* `Prefer: respond-async` no mesmo endpoint (contrato menos explícito).
89. **O síncrono fica, desligado por padrão** (`EXTRACAO_SINCRONA=false`; 404
    `MODO_SINCRONO_DESLIGADO`). O `.env.example` liga para uso local. Com ele desligado, a
    API não carrega o OCR: sobe em segundos e usa ~110 MB.
90. **Só consulta (polling), sem webhook** por enquanto.
91. **Fila no próprio Postgres** (`tarefas_extracao`, `SELECT … FOR UPDATE SKIP LOCKED`).
    A imagem fica no S3 em `fila/<id>/`, criptografada, e é apagada ao concluir; o
    resultado fica no pacote de auditoria, como no síncrono. Nenhuma coluna guarda valor de
    campo; o corpo de erro gerado pela própria fila não tem dado pessoal. *Descartado:*
    Redis/SQS (infraestrutura nova).
92. **Erros de requisição continuam síncronos** (400/413 no POST). O cache vale nos dois
    modos: documento já processado responde 202 `CONCLUIDA` e a consulta devolve o
    resultado na hora.
93. **Trabalhador** (`uv run trabalhador`, serviço no compose):
    - concorrência = `TRABALHADOR_CONCORRENCIA` (padrão: `PADDLEOCR_INSTANCIAS`), para a
      fila do OCR nunca estourar o tempo limite (decisão 87);
    - escala horizontal com mais containers;
    - aquece o OCR antes da primeira tarefa e para de forma limpa em SIGTERM.
94. **Falhas na fila:**
    - erro transitório (502/504) volta para a fila com espera crescente
      (`FILA_ESPERA_INICIAL_S` × 2ⁿ, até `FILA_ESPERA_MAX_S`) até `FILA_TENTATIVAS_MAX`;
      depois disso, o 502/504 é o resultado;
    - trabalhador que morre: a reserva (`FILA_RESERVA_S`) vence e outro retoma; reservada
      mais vezes que o limite, a tarefa termina com 500 `ERRO_INTERNO` (protege contra uma
      imagem que derruba o processo);
    - falha ao gravar o pacote de auditoria também dá 500 na consulta (não há resultado
      gravado para devolver);
    - o custo das tentativas devolvidas vai só para o log (`tarefa_devolvida`).
95. **TTL:** a tarefa expira com o TTL dos artefatos contado da chegada; o processamento
    também conta da chegada, não do início no trabalhador. A limpeza apaga tarefas vencidas
    e a imagem da fila; o lifecycle de 2 dias em `fila/` é só rede de segurança.
96. **O 422 automático do FastAPI sai do OpenAPI:** a validação vira 400 pelo handler, e
    aquele 422 nunca acontece.
97. **OCR: medium continua o padrão.** O mobile perde de 3 a 5 pontos de ancoragem no
    sintético em todas as variações (condição combinada: só trocar sem perda). O limite da
    detecção (`PADDLEOCR_DET_LADO_MAX`) existe e fica desligado. Medido isolado no container,
    com cartões de 1100 px: em 960 px o p50 cai de 10,2 para 9,2 s e o pico de memória de
    1,46 para 1,24 GB, sem perda de ancoragem; em 640 px a ancoragem cai 1 ponto. Em foto
    real (~1600 px após o recorte) o ganho tende a ser maior; decide-se no gabarito real.
