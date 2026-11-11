# syntax=docker/dockerfile:1
# Imagem CPU, enxuta, usuário sem root. Nenhuma dependência exige GPU.

FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /src
COPY pyproject.toml uv.lock ./
# Só dependências: a camada fica em cache enquanto o lockfile não mudar.
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS final
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=root:root pyproject.toml alembic.ini ./
COPY --chown=root:root app ./app
COPY --chown=root:root avaliacao ./avaliacao
COPY --chown=root:root migrations ./migrations
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app_padrao", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
