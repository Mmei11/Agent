FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    CONSULTATION_DB_PATH=/data/consultations.sqlite

WORKDIR /app

RUN pip install --no-cache-dir uv==0.8.22 \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data \
    && chown app:app /data

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY backend ./backend
COPY skills ./skills

USER app
EXPOSE 8000

CMD ["uvicorn", "backend.app.api.routes:app", "--host", "0.0.0.0", "--port", "8000"]
