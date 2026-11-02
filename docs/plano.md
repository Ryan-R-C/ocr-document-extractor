# Plano de desenvolvimento — Extração e validação de documentos admissionais

Nov 2, 2026 · @Vagner

## Contexto e escopo

O serviço recebe uma imagem em base64 com o tipo de documento, extrai os campos e valida cada valor dentro do próprio documento, devolvendo campos e status. A primeira versão usa GPT Vision, com a arquitetura aberta para trocar ou comparar providers.

**Dentro do escopo**

- Entrada: imagem em base64 + tipo de documento declarado (`IDENTITY_DOCUMENT`, `CTPS`, `CPF` etc.).
- Classificação fina dentro do tipo declarado: RG antigo, RG 2018–2022, CIN ou CNH; CTPS física ou digital; UF, modelo e lado.
- Extração dos campos com controle de alucinação e validação de cada valor dentro do documento.
- Resposta com campos e status; erro com motivo quando o documento não pode ser processado.
- Guarda de artefatos para auditoria, com expiração configurável (8 h, 16 h ou sem expiração), e guarda dos casos que precisam de revisão.

**Fora do escopo**

- Captura da imagem (app, câmera, orientação ao candidato).
- Biometria e comparação de face.
- Cruzamento entre documentos, que fica com a etapa de matching.
- Consultas a bases externas, como a situação do CPF na Receita.

**Premissas**

- Volume variável, sem previsão de picos.
- Sem GPU, nem planos de ter: tudo que roda localmente precisa rodar em CPU.
- Custo de referência: abaixo de R$ 0,15 por documento é ótimo; acima de R$ 1,00 é problema.
- Já existe um gabarito (documentos com valores conferidos) para avaliação.
- Sem restrição de local de processamento dos dados.
- Nos campos críticos, preferência por mandar mais casos para revisão e quase zerar o erro que escapa.
- Todo documento é aceito e sinalizado (xerox, print de tela, RG antigo).
- Distribuição por tipo e por UF desconhecida: todas as UFs são tratadas igualmente até haver dados.

Sem GPU, a abordagem C entra pelas ideias, não pela infraestrutura: OCR clássico em CPU como segundo leitor, extração ancorada no texto lido e modelos abertos consumidos como mais um provider via API hospedada.

## Arquitetura

&#91;embedded content: fluxo de uma requisição · da imagem à resposta\]

A leitura visual e o OCR rodam em paralelo; só o que falha na ancoragem gasta uma segunda chamada ao provider, e o mesmo fluxo roda sobre o gabarito na avaliação.

Stack sugerida, tudo em CPU: Python com FastAPI e Pydantic, OpenCV, Pillow e PyMuPDF no pré-processamento, PaddleOCR, validate-docbr, PostgreSQL para metadados, armazenamento de objetos compatível com S3 e contêineres Docker escalando horizontalmente.

## Contrato da API

Uma chamada síncrona por imagem: 200 quando há campos para devolver (aprovados ou para revisão), 422 com motivos quando a imagem não permite extrair os campos críticos. Se a latência ou os picos apertarem, a mesma esteira ganha um modo assíncrono (202 + consulta por id) sem mudar o núcleo.

**Requisição** — `POST /v1/extracoes`

```json
{
  "tipo_documento": "IDENTITY_DOCUMENT",
  "imagem_base64": "<JPEG, PNG, WebP, HEIC ou PDF>",
  "id_externo": "opcional, para rastreio"
}
```

**Resposta 200**

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

- `status` do documento: `APROVADO` quando todos os campos críticos estão legíveis, ancorados no segundo leitor e válidos; `REVISAO` nos demais casos com campos críticos lidos.
- `status_leitura`: `LEGIVEL`, `PARCIAL`, `ILEGIVEL`, `AUSENTE`.
- `status_validacao`: `VALIDO`, `INVALIDO`, `SUSPEITO`, `NAO_VERIFICAVEL`.
- `versao_pipeline` registra provider, modelo, prompt e regras usados, para reproduzir qualquer resultado.

**Erros**

| Código | Quando | Exemplos de motivo |
| --- | --- | --- |
| 400 | Requisição malformada | `BASE64_INVALIDO`, `TIPO_DESCONHECIDO`, `FORMATO_NAO_SUPORTADO` |
| 413 | Arquivo acima do limite | `ARQUIVO_GRANDE_DEMAIS` |
| 422 | Imagem válida, mas sem como extrair os campos críticos | `NENHUM_DOCUMENTO`, `TIPO_DIVERGENTE`, `LADO_SEM_DADOS`, `IMAGEM_DESFOCADA`, `DOCUMENTO_CORTADO`, `REFLEXO_EXCESSIVO`, `CAMPO_CRITICO_ILEGIVEL` |
| 502 / 504 | Provider falhou ou estourou o tempo, mesmo após nova tentativa e fallback | `PROVIDER_INDISPONIVEL`, `TEMPO_ESGOTADO` |

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

Sugiro 422 em vez de 400 para falha de extração, porque separa "requisição errada" de "documento não legível". Se o sistema que chama já espera 400, o código fica configurável.

## Providers

O núcleo conversa com três interfaces; cada provider é um adaptador trocado por configuração, e o primeiro é a OpenAI. Nenhuma regra, schema ou decisão depende de um provider específico.

| Interface | O que entrega | Adaptador inicial | Candidatos para testar |
| --- | --- | --- | --- |
| Leitor visual (VLM) | Subtipo, UF, lado e transcrição dos campos no schema | OpenAI | Claude, Gemini, modelos abertos via API hospedada |
| Leitor de texto (OCR clássico) | Palavras com caixa e confiança, para ancoragem e recorte | PaddleOCR em CPU | Tesseract (CPU), Google Cloud Vision, Azure |
| Localizador | Caixa de cada campo, para recortar e reler | Caixas do OCR clássico | Caixas do próprio VLM, quando o provider oferecer |

**O que cada adaptador resolve**

- Converte o schema interno para o formato de saída estruturada do provider.
- Aplica as regras de imagem do provider. Na OpenAI, o parâmetro `detail` define o redimensionamento, e a documentação indica `original` para OCR; a imagem é recusada se passar de 30.000 patches, então o serviço recorta e redimensiona antes de enviar.
- Faz nova tentativa com espera crescente em erro 429 ou 5xx, respeita o tempo limite e mapeia falhas para os códigos da API.
- Contabiliza tokens e custo por chamada e registra provider e modelo em `versao_pipeline`.
- Controla concorrência por provider: tokens de imagem contam no limite de tokens por minuto, o gargalo provável nos picos.

**Escalada por dificuldade**

A leitura principal usa um modelo barato. Só os campos que falham na ancoragem ou nas regras são relidos, recortados, por um modelo maior. Isso mantém o custo médio perto do modelo barato (ver Custos).

**Configuração (`.env`)**

```bash
LEITOR_VISUAL_PROVIDER=openai
LEITOR_VISUAL_MODELO=gpt-6.1-terra
LEITOR_VISUAL_MODELO_ESCALADA=gpt-6.1-sol
LEITOR_VISUAL_DETALHE=original
LEITOR_TEXTO_PROVIDER=paddleocr
PROVIDER_FALLBACK=
PROVIDER_TIMEOUT_S=30
ARTEFATOS_TTL=8h           # 8h | 16h | null
CASOS_REVISAO_TTL=null
HTTP_STATUS_FALHA_EXTRACAO=422
```

Dois pontos da OpenAI que afetam o plano: a plataforma de fine-tuning está sendo encerrada e não aceita novos usuários, então a melhoria vem de schema, prompt e pipeline, não de treino; e os preços de Batch e Flex são metade do padrão, o que barateia as rodadas de avaliação no gabarito.

## Extração sem alucinação

O VLM transcreve, o OCR clássico confirma e o código decide: nenhum valor é aprovado sem aparecer nas duas leituras. Esta é a parte da abordagem C que não depende de GPU.

1. **Pré-processamento (CPU).** Decodifica o base64, corrige a orientação, converte PDF e HEIC em imagem, localiza o documento e recorta o fundo. Mede nitidez, reflexo e corte; se reprovar, devolve 422 com o motivo.
2. **Leitura visual (VLM).** Uma chamada por imagem, com schema específico do subtipo. Cada campo traz `status`, `rotulo_visto` e `texto_literal`; caractere duvidoso vira `?`.
3. **Leitura de texto (OCR clássico, CPU).** Palavras com caixa e confiança.
4. **Ancoragem.** O `texto_literal` precisa aparecer no OCR, perto do rótulo visto. A comparação tolera espaços, pontuação e confusões comuns (O/0, I/1, S/5). Resultado: ancorado, parcial ou não ancorado.
5. **Releitura dirigida.** Campo não ancorado ou com dígito verificador falho é recortado com margem, ampliado e relido pelo modelo de escalada, sem dizer qual valor se espera. Se duas de três leituras concordam, o campo conta como lido; senão, vai para revisão.
6. **Regras e decisão.** O código normaliza, valida e decide o status (ver Motor de regras).

**Schema de cada campo**

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

- Todos os campos obrigatórios, status antes do valor e texto vazio quando ilegível ou ausente: o schema já nasce compatível com saída estruturada estrita.
- Sem `pattern`, `format` ou `enum` no valor: formato forçado na geração transforma ilegível em valor plausível e falso.
- Toda lista fechada tem uma saída como `NAO_IDENTIFICADO`.
- Um schema por subtipo, só com os campos que aquele modelo de documento tem; filiação sem rótulo vira `filiacao_linha_1` e `filiacao_linha_2`.

**Prompt, no essencial:** transcrever exatamente o que está impresso; não corrigir, completar, normalizar nem deduzir de outros campos; `?` para caractere duvidoso; AUSENTE e ILEGIVEL são respostas corretas. Nenhum exemplo com dados realistas.

**Nunca fazer**

- Devolver ao modelo o erro do validador pedindo correção: isso fabrica um número que passa no dígito verificador.
- Corrigir dígitos automaticamente a partir do dígito verificador.
- Pedir raciocínio dentro do JSON.
- Aprovar campo manuscrito crítico (contratos da CTPS física) só com a leitura do VLM: o OCR clássico quase nunca ancora manuscrito, então esses campos exigem duas leituras de modelos diferentes concordando, ou revisão.

**Evolução opcional: extração por referência.** Para campos impressos, o modelo devolve só os identificadores das palavras do OCR que compõem o campo, e o código monta o valor. Assim o modelo não tem como inventar caracteres.

## Campos por tipo de documento

Proposta para confirmar: os campos críticos decidem entre `APROVADO`, `REVISAO` e 422; os informativos são devolvidos com status, mas nunca bloqueiam.

| Tipo declarado | Subtipo | Campos críticos | Campos informativos |
| --- | --- | --- | --- |
| `IDENTITY_DOCUMENT` | RG antigo | Número do RG, órgão emissor, UF, data de expedição, nome, data de nascimento | Filiação (linhas), naturalidade, documento de origem, CPF |
| `IDENTITY_DOCUMENT` | RG 2018–2022 | Os do RG antigo + CPF | Números extras (NIS, título, CNH, CTPS e outros), filiação, naturalidade |
| `IDENTITY_DOCUMENT` | CIN | CPF (número do documento), nome, data de nascimento, data de expedição, validade, órgão e UF | Filiação, naturalidade, MRZ e QR (quando houver), números extras |
| `IDENTITY_DOCUMENT` | CNH | Nome, CPF, data de nascimento, número de registro, validade, categoria | Documento de identidade e órgão, filiação, 1ª habilitação, emissão, local, observações |
| `CPF` | Cartão, comprovante ou CPF digital | Número do CPF | Nome, data de nascimento |
| `CTPS` | Física | Número, série, UF | Nome, data de nascimento, emissão, contratos (empregador, CNPJ, cargo, admissão, saída, salário) |
| `CTPS` | Digital (print ou PDF) | CPF (de onde saem número e série) | Nome, contratos (empregador, CNPJ, cargo, admissão, saída) |

- Confirmar os nomes exatos do enum de entrada (por exemplo, `CTPS`) e os nomes de campo que o sistema de destino usa.
- Um RG enviado só pela frente (foto e assinatura) não tem os campos críticos: a resposta é 422 com `LADO_SEM_DADOS`.

## Motor de regras

Todas as validações usam só o próprio documento, são determinísticas e versionadas por data de vigência. QR e MRZ entram como bônus: quando presentes e legíveis, reforçam; quando ausentes, não pesam contra.

| Documento | Regra | Se falhar |
| --- | --- | --- |
| Todos | CPF: dígitos verificadores e rejeição de 11 dígitos iguais | Releitura; persistindo, `INVALIDO` |
| Todos | Datas válidas; idade plausível; expedição depois do nascimento e não futura | `INVALIDO` ou `SUSPEITO` |
| RG | Formato do número por UF; dígito verificador só em SP (8 dígitos + DV, módulo 11 com X) | Fora de SP: `NAO_VERIFICAVEL` |
| RG 2018–2022 | Dígitos verificadores de NIS/PIS, título, CNH e CNS quando impressos | `INVALIDO` no campo |
| RG | Matrícula de certidão (32 dígitos): DVs, código 55 nas posições 9–10, ano do registro ≥ ano de nascimento | `SUSPEITO` |
| RG | Naturalidade (município × UF) na tabela do IBGE, tolerando nomes históricos | `NAO_VERIFICAVEL` |
| RG | Órgão emissor coerente com a UF | `SUSPEITO` |
| RG antigo | Validade até 28/02/2032; indeterminada para quem tinha 60 anos em 01/03/2022 | Sinalização |
| CIN | Número = CPF; validade coerente com a idade na expedição (5 anos até 11, 10 anos de 12 a 59, indeterminada a partir de 60) | `INVALIDO` ou `SUSPEITO` |
| CIN | MRZ legível: checksums e conferência com os campos impressos | Bônus ou `SUSPEITO` |
| CNH | Dígitos verificadores do registro; categoria em lista fechada | `INVALIDO` |
| CNH | Validade como teto por idade e época: desde 12/04/2021, 10 anos (< 50), 5 (50–69), 3 (70+); antes, 5 (até 65) e 3 (acima); prazo menor é permitido | `SUSPEITO` |
| CNH | Vencimento considera a prorrogação do Contran: vencidas entre 05/06 e 08/12/2026 valem até 09/12/2026 | Sinalização |
| CNH | 1ª habilitação com 18 anos ou mais e não posterior à emissão | `SUSPEITO` |
| CTPS física | Número, série e UF no formato (em geral 7 + 4 + UF, tolerando modelos antigos) | `SUSPEITO` |
| CTPS (contratos) | CNPJ ou CGC: dígitos verificadores, aceitando CNPJ alfanumérico (emitido desde 31/07/2026) | `INVALIDO` no contrato |
| CTPS (contratos) | Admissão antes da saída; idade mínima plausível na admissão; moeda do salário coerente com a data (`Cr$`, `Cz$`, `NCz$`, `CR$`, `R$`) | `SUSPEITO` |
| CTPS digital | Número = 7 primeiros dígitos do CPF; série = 4 últimos; CBO na tabela oficial | `INVALIDO` |

**Sinalizações de política (nunca bloqueiam):** `COPIA_REPROGRAFICA`, `PRINT_DE_TELA`, `RG_ANTIGO`, `DOCUMENTO_VENCIDO`, `QR_CONFERIDO`, `MRZ_CONFERIDA`.

**Implementação:** cada regra tem id, versão, data de vigência e testes. Os dígitos verificadores vêm da biblioteca validate-docbr (CPF, CNPJ numérico e alfanumérico, CNH, CNS, PIS, título e certidões); regras por UF, prazos por época e tabelas (IBGE, órgãos emissores, CBO) são código do projeto.

## Armazenamento, auditoria e retenção

Cada processamento gera um pacote de auditoria que expira conforme `ARTEFATOS_TTL` (8 h, 16 h ou sem expiração); os casos em revisão são guardados à parte, conforme `CASOS_REVISAO_TTL`, porque viram material de melhoria.

**Pacote de auditoria por processamento**

- Imagem recebida (com hash), imagem normalizada e recortes de cada campo.
- Respostas brutas dos providers (VLM e OCR), com tokens e custo.
- Ancoragens, releituras, regras aplicadas (id e versão) e decisão final.
- `versao_pipeline`, horários e `id_externo`.

**Retenção**

- Metadados em banco com `expira_em`; arquivos em armazenamento de objetos com regra de expiração por prefixo, mais um job de limpeza que confere o banco.
- Casos em revisão ganham um registro próprio. Quando alguém confere e corrige os valores, o caso entra no gabarito e passa a ser usado na avaliação.
- Mesma imagem (mesmo hash) com a mesma `versao_pipeline` pode devolver o resultado guardado, sem nova chamada ao provider, enquanto o pacote não expirar.

**Segurança**

- Logs sem dados pessoais: CPF e nomes mascarados, só ids.
- Criptografia em repouso e acesso restrito aos pacotes.
- Guardar imagens de documentos sem expiração (`null`) pede finalidade definida; vale registrar essa decisão com quem cuida de LGPD.

## Avaliação e métricas

O gabarito vira um conjunto de teste executável: toda troca de provider, modelo, prompt ou regra roda nele antes de ir para produção, e não entra se piorar o erro que escapa.

| Métrica | Definição | Meta proposta |
| --- | --- | --- |
| Erro escapado | Campo crítico errado ou inventado com status `APROVADO` | Abaixo de 0,5% dos campos críticos aprovados |
| Alucinação | Valor devolvido onde o gabarito diz ausente ou ilegível, incluindo o teste do campo apagado | Zero em campos críticos aprovados |
| Acurácia por campo | Valor normalizado igual ao do gabarito | Definir após o baseline |
| Taxa de revisão | Documentos em `REVISAO` | Definir após o baseline |
| Taxa de 422 | Documentos recusados por imagem | Acompanhar por motivo |
| Custo por documento | Média e percentil 95 | Abaixo de R$ 0,15 na média |
| Latência | Percentis 50 e 95 | Definir após o baseline |

**Como medir**

- Um comando roda a esteira inteira sobre o gabarito com uma configuração (provider, modelo, prompt, regras) e gera relatório por campo, subtipo, UF e qualidade de imagem (foto, xerox, print).
- Teste do campo apagado: versões das imagens do gabarito com um campo crítico borrado ou apagado digitalmente. A resposta certa é AUSENTE ou ILEGIVEL; qualquer valor conta como alucinação.
- Parte do gabarito fica separada e nunca é usada para ajustar prompt, para o resultado não ficar otimista.
- Rodadas de avaliação usam Batch ou Flex quando o provider oferece, pela metade do preço.
- Casos revisados e corrigidos entram no gabarito toda semana.

## Custos estimados

Com OpenAI e OCR em CPU, três das quatro configurações ficam abaixo de R$ 0,15 por documento; só usar o modelo mais caro em tudo passa da meta.

| Configuração (leitura principal + escalada) | US$ por documento | R$ por documento |
| --- | --- | --- |
| gpt-6-luna + gpt-6.1-sol | 0,0016 | 0,01 |
| gpt-6.1-terra + gpt-6.1-sol | 0,0074 | 0,04 |
| gpt-6.1-sol + gpt-6-astra | 0,018 | 0,10 |
| gpt-6-astra nas duas etapas | 0,069 | 0,37 |

**Premissas**

- Preços padrão por milhão de tokens (entrada/saída): gpt-6-luna US$ 0,10/0,50; gpt-6.1-terra US$ 1/5; gpt-6.1-sol US$ 2/10; gpt-6-astra US$ 10/50.
- Imagem do documento recortada em cerca de 1600 × 1000 px: 1.600 patches de 32 px × multiplicador 1,2 = 1.920 tokens. O multiplicador 1,2 é o documentado para o gpt-6-astra; confirmar os demais na calculadora oficial.
- Mais 1.500 tokens de instruções e schema na entrada e 600 tokens de saída por leitura principal.
- 30% dos documentos com dois campos relidos em recorte pelo modelo de escalada.
- OCR clássico em CPU, sem custo por chamada.
- Câmbio de R$ 5,40 por dólar (fechamento de 26/10/2026).

**O que pode mudar a conta**

- Imagem enviada sem recorte e em `original`: uma foto de 4000 × 3000 px passa de 11 mil patches. Recortar e limitar a resolução antes de enviar é obrigatório.
- Se o modelo usar raciocínio, esses tokens são cobrados como saída. Usar o menor esforço de raciocínio disponível e medir no gabarito.
- CTPS com várias páginas multiplica o custo pelo número de imagens.

A escolha entre as linhas sai da avaliação: o modelo mais barato que mantiver o erro escapado dentro da meta.

## Fases, entregáveis e critérios de aceite

&#91;embedded content: fases e portões · 13 semanas até produção\]

Estimativa para dois desenvolvedores: as fases 2 e 3 correm em paralelo, e nenhuma fase seguinte começa sem passar no portão da anterior. Os portões 3 e 4 usam as metas da seção Avaliação e métricas.

## Riscos e decisões em aberto

O maior risco é aprovar um valor inventado; o desenho ataca isso, mas só se o gabarito cobrir os RGs mais difíceis.

| Risco | Efeito | Mitigação |
| --- | --- | --- |
| Gabarito pouco representativo (poucas UFs, só fotos boas) | Métricas otimistas e surpresa em produção | Estratificar por UF, subtipo e qualidade; incluir casos ruins; somar casos revisados toda semana |
| OCR clássico fraco em RG plastificado, xerox ou datilografado | Pouca ancoragem e muita revisão | Ajustar pré-processamento; testar outro leitor de texto; medir a taxa de ancoragem por subtipo |
| Mudança de modelo ou preço no provider | Resultado ou custo muda sem aviso | Fixar a versão do modelo; rodar a avaliação a cada troca; provider plugável |
| Picos acima do limite de tokens por minuto | Erros 429 e latência alta | Controle de concorrência, novas tentativas, fallback de provider e, se preciso, modo assíncrono |
| Contratos manuscritos na CTPS física | Baixa automação nesses campos | Aceitar revisão; dupla leitura por modelos diferentes |
| Regras que mudam com o tempo (prazos da CNH, CNPJ alfanumérico) | Documentos válidos marcados como inválidos | Regras versionadas por vigência e revistas a cada trimestre |
| Retenção sem expiração | Exposição de dados pessoais | Finalidade definida, acesso restrito e TTL como padrão |

**Decisões em aberto**

- [ ] Confirmar os campos críticos por tipo e os nomes exatos do enum de entrada.
- [ ] Código HTTP para falha de extração: 422 (sugerido) ou 400.
- [ ] Tempo máximo de resposta aceito por quem chama, que define se o modo assíncrono entra já.
- [ ] Modelo inicial: gpt-6.1-terra como ponto de partida, a confirmar pelo baseline.
- [ ] Leitor de texto: PaddleOCR em CPU ou serviço em nuvem, decidido pela taxa de ancoragem no gabarito.
- [ ] TTL padrão dos artefatos e quem acessa os casos em revisão.
- [ ] Tamanho do gabarito e quantidade mínima de exemplos por subtipo e UF.

## Referências

- [OpenAI — Pricing](https://developers.openai.com/api/docs/pricing) (preços por modelo, Batch/Flex, fine-tuning)
- [OpenAI — Images and vision](https://developers.openai.com/api/docs/guides/images-vision) (detail, patches, multiplicadores, limitações)
- [PaddleOCR](https://www.paddleocr.ai/v3.6.0/en/index.html)
- [validate-docbr](https://github.com/alvarofpp/validate-docbr)
- [Decreto 10.977/2022 — anexo](https://www2.camara.gov.br/legin/fed/decret/2022/decreto-10977-23-fevereiro-2022-792318-anexo-pe.pdf) (validade do RG antigo)
- [Validade da CIN por idade (Arpen-SP)](https://arpensp.org.br/noticia/artigo-decreto-1097722-e-a-validade-da-carteira-de-identidade-%E2%80%93-por-fernanda-maria-alves-gomes)
- [Decreto 9.278/2018](https://www2.camara.leg.br/legin/fed/decret/2018/decreto-9278-5-fevereiro-2018-786143-normaatualizada-pe.pdf) (campos do RG 2018–2022)
- [Provimento CNJ 149, art. 473](https://www.legjur.com/legislacao/art/out_2026020900001492023-473) (matrícula de certidão)
- [Prazos de validade da CNH (Portal do Trânsito)](https://www.portaldotransito.com.br/noticias/nova-lei-de-transito-mitos-e-verdades-sobre-o-novo-prazo-de-renovacao-da-cnh/)
- [Lei 15.428/2026 e a escala de validade da CNH (A Tarde)](https://atarde.com.br/brasil/cnh-aos-50-anos-entenda-a-mudanca-na-validade-e-regras-de-2026-1401935)
- [Deliberação Contran 279/2026 (Portal do Trânsito)](https://www.portaldotransito.com.br/?p=58299)
- [Dígito verificador do RG de SP (Axon)](https://www.axonwireless.com/toolbox/brazil-rg-generator/)
- [CNPJ alfanumérico](https://www.barbieriadvogados.com/cnpj-alfanumerico-2026-mudancas-regulamentares-e-impactos-empresariais/)
- [CTPS Digital: número e série a partir do CPF (NetCPA)](https://netcpa.com.br/noticias/urgente-ctps-digital-atualizada-a-forma-de-declaracao-no-caged/3049)
- [Câmbio de 26/10/2026 (Canal Rural)](https://www.canalrural.com.br/economia/ibovespa-fecha-em-queda-de-1388-e-dolar-comercial-sobe-821/)
- [KIE-HVQA: alucinação de OCR em documentos degradados (arXiv)](https://arxiv.org/abs/2506.20168v2)
