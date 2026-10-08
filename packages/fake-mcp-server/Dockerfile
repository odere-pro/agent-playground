# The fake MCP server image for the offline Compose stack. Build from the repo root:
#   docker build -f packages/fake-mcp-server/Dockerfile -t fake-mcp-server .
# Same base and the same locked resolve as packages/chassis/Dockerfile.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.23@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/chassis/pyproject.toml packages/chassis/
COPY packages/contract-suites/pyproject.toml packages/contract-suites/
COPY packages/workloads/echo-python/pyproject.toml packages/workloads/echo-python/
COPY packages/workload-a2a/pyproject.toml packages/workload-a2a/
COPY packages/workloads/echo-pydanticai/pyproject.toml packages/workloads/echo-pydanticai/
COPY packages/workloads/echo-langgraph/pyproject.toml packages/workloads/echo-langgraph/
COPY packages/code-runner/pyproject.toml packages/code-runner/
COPY packages/fake-model-server/pyproject.toml packages/fake-model-server/
COPY packages/fake-mcp-server/pyproject.toml packages/fake-mcp-server/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package fake-mcp-server --no-install-workspace
COPY packages/fake-mcp-server/src packages/fake-mcp-server/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package fake-mcp-server

FROM ${PYTHON_IMAGE} AS runtime
# uid 10005: the one uid table in deploy/README.md. No home directory: HOME points at /tmp.
RUN groupadd --gid 10005 fake && useradd --uid 10005 --gid fake --no-create-home \
    --home-dir /tmp --shell /usr/sbin/nologin fake
WORKDIR /app
# Owned by root and read-only to the app user: the process cannot change its own code.
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
USER 10005:10005
EXPOSE 8082
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8082/health', timeout=2).status == 200 else 1)"]
# No secret and no credential: the server has no auth of its own; a NetworkPolicy limits who reaches it.
ENTRYPOINT ["fake-mcp-server", "--host", "0.0.0.0", "--port", "8082"]
