FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project \
    --extra postgres --extra cloud --extra fetch --extra tracing --extra s3
COPY backend ./backend
RUN cd backend && uv sync --frozen --no-dev \
    --extra postgres --extra cloud --extra fetch --extra tracing --extra s3
COPY corpus ./corpus
COPY presets ./presets
COPY scenarios ./scenarios

ENV PATH="/app/backend/.venv/bin:$PATH" DREAMPET_CONFIG=/config/dreampet.yaml PYTHONUNBUFFERED=1
WORKDIR /config
EXPOSE 8000
CMD ["dreampet", "up", "--host", "0.0.0.0", "--no-worker"]
