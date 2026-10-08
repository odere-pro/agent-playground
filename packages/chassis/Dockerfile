# The chassis image: `chassis serve` from the locked workspace. Build from the repo root:
#   docker build -f packages/chassis/Dockerfile -t chassis .
# Base pinned by digest (deploy/compose/SECURITY.md, section 2). Bump on purpose, in its own commit:
#   docker buildx imagetools inspect python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${PYTHON_IMAGE} AS build
# uv from its official image, pinned by digest too. suggested: keep it at the version `make setup` uses.
COPY --from=ghcr.io/astral-sh/uv:0.11.23@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Only what the locked resolve needs, so a source edit does not invalidate the dependency layer.
# `--locked` checks the whole workspace against uv.lock, so every member's pyproject.toml is here.
COPY pyproject.toml uv.lock ./
COPY packages/chassis/pyproject.toml packages/chassis/
COPY packages/contract-suites/pyproject.toml packages/contract-suites/
COPY packages/fake-model-server/pyproject.toml packages/fake-model-server/
COPY packages/workloads/echo-python/pyproject.toml packages/workloads/echo-python/
COPY packages/workload-a2a/pyproject.toml packages/workload-a2a/
COPY packages/workloads/echo-pydanticai/pyproject.toml packages/workloads/echo-pydanticai/
COPY packages/workloads/echo-langgraph/pyproject.toml packages/workloads/echo-langgraph/
COPY packages/fake-mcp-server/pyproject.toml packages/fake-mcp-server/
COPY packages/code-runner/pyproject.toml packages/code-runner/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package chassis --package echo-python --no-install-workspace
# The echo-python workload rides in this image for the `inprocess` lane (PoC-1 only; PoC-2 moves
# it to its own container).
COPY packages/chassis/src packages/chassis/src
COPY packages/workloads/echo-python/src packages/workloads/echo-python/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package chassis --package echo-python

FROM ${PYTHON_IMAGE} AS runtime
# uid 10001: the one uid table in deploy/README.md. No home directory: HOME points at /tmp.
RUN groupadd --gid 10001 chassis && useradd --uid 10001 --gid chassis --no-create-home \
    --home-dir /tmp --shell /usr/sbin/nologin chassis
WORKDIR /app
# Owned by root and read-only to the app user: the process cannot change its own code.
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
USER 10001:10001
EXPOSE 8080
# Healthy means ready: `/ready` is 200 once the lifespan has built the ports and set up the engine
# (server/app.py); `/health` only says the process answers. uvicorn runs the lifespan before it
# binds, so a bad `spec.engine.handle` never reaches either: the process exits with
# "Application startup failed" and the import error in the log.
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["chassis", "serve"]
CMD ["--config", "/etc/chassis/config.yaml", "--host", "0.0.0.0", "--port", "8080"]
