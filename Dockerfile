FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.5 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true

RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry install --without dev --no-root

# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    APP__UPLOADS_DIR=/app/uploads

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .

EXPOSE 8000

CMD ["python", "-m", "app.entrypoint_api"]
