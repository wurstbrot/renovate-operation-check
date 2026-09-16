

FROM docker.io/python:3.14.7-slim AS builder

COPY pip.conf /etc/pip.conf
WORKDIR /app

COPY pyproject.toml setup.py MANIFEST.in README.md  ./
COPY scripts ./scripts/

RUN mkdir /app/python_packages && pip install --no-cache-dir --target=/app/python_packages "."

FROM docker.io/python:3.14.7-slim


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/app/config/config.yaml \
    PYTHONPATH=/app/python_packages

WORKDIR /app

COPY --from=builder /app/python_packages /app/python_packages

WORKDIR /app

USER 65535

HEALTHCHECK --interval=60s --timeout=10s --retries=3 CMD ["python", "-c", "import scripts"]

ENTRYPOINT ["python", "-m", "scripts.main", "--config", "/app/config/config.yaml"]

LABEL org.opencontainers.image.title="Renovate Operation Check" \
      org.opencontainers.image.description="Validate PRs and delete PRs" \
      org.opencontainers.image.base.name="docker.io/python:3.11-slim" \
      org.opencontainers.image.authors="Timo Pagel" \
