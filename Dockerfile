FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_PREFERENCE=only-system

# Third-party dependencies first, so source edits do not invalidate this layer.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/lineage/pyproject.toml packages/lineage/pyproject.toml
COPY packages/knowledge/pyproject.toml packages/knowledge/pyproject.toml
COPY packages/gateway/pyproject.toml packages/gateway/pyproject.toml
COPY packages/mcp/pyproject.toml packages/mcp/pyproject.toml
COPY packages/orchestrator/pyproject.toml packages/orchestrator/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace

# Workspace packages and runtime assets.
COPY packages/ packages/
COPY templates/ templates/
COPY examples/ examples/
RUN uv sync --frozen --no-dev

ENV MODELFORGE_ARTIFACT_DIR=/data/artifacts \
    MODELFORGE_DATA_DIR=/data/datasets \
    MODELFORGE_TEMPLATES_DIR=/app/templates

ENTRYPOINT ["uv", "run", "--no-sync", "modelforge"]
CMD ["worker"]
