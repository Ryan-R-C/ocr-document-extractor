# Comandos do projeto. Requer uv (https://docs.astral.sh/uv/) e Docker.
.PHONY: setup up down logs test test-integracao test-ocr lint formatar openapi avaliar comparar \
        gabarito-sintetico estratificar limpeza

CONFIG ?= avaliacao/configs/sintetico.toml
PARTICAO ?= ajuste
MODO ?= padrao
MANIFESTO ?= dados/sintetico/manifesto.jsonl

setup:  ## dependências (com o PaddleOCR), .env local e gabarito sintético de demonstração
	uv sync --extra ocr
	@test -f .env || cp .env.example .env
	@test -f $(MANIFESTO) || $(MAKE) gabarito-sintetico

up: setup  ## sobe api, postgres, s3 (RustFS) e limpeza (migrações e bucket no serviço preparar)
	docker compose up -d --build --wait api

down:
	docker compose down

logs:
	docker compose logs -f api limpeza

test:  ## testes sem dependências externas (fakes, SQLite, S3 simulado)
	uv run pytest

test-integracao:  ## testes contra o postgres e o s3 do docker compose
	uv run pytest -m integracao

test-ocr:  ## o teste que roda o PaddleOCR de verdade (precisa do extra ocr e dos pesos)
	uv run pytest -m ocr_real

lint:  ## ruff, formatação e tipos
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy app avaliacao tests

formatar:
	uv run ruff check --fix .
	uv run ruff format .

openapi:  ## regenera docs/openapi.json (revise o diff antes de commitar)
	uv run python -m app.api.openapi

avaliar:  ## make avaliar CONFIG=<arquivo.toml> PARTICAO=ajuste|holdout MODO=padrao|flex|batch
	uv run avaliar --config $(CONFIG) --particao $(PARTICAO) --modo $(MODO)

comparar:  ## make comparar RELATORIOS="saidas/avaliacoes/a/relatorio.json saidas/avaliacoes/b/relatorio.json"
	uv run comparar $(RELATORIOS) --saida saidas/comparacao.md

gabarito-sintetico:
	uv run gabarito sintetico --saida dados/sintetico

estratificar:  ## make estratificar MANIFESTO=<manifesto.jsonl>
	uv run gabarito estratificar --manifesto $(MANIFESTO) --saida saidas/estratificacao

limpeza:  ## uma rodada do job de limpeza com a configuração do .env
	uv run limpeza
