# Stage 1: Build
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip wheel . --no-deps --wheel-dir /wheels && \
    pip wheel psycopg2-binary --wheel-dir /wheels

# Stage 2: Runtime
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --system schemint && \
    useradd --system --gid schemint --create-home schemint

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

COPY alembic.ini ./
COPY alembic/ alembic/

USER schemint

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "schemint.main:app", "--host", "0.0.0.0", "--port", "8000"]
