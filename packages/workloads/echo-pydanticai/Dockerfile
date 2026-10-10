# The echo-pydanticai workload: `handle` served over A2A by workload-a2a, from the locked
# workspace. Build from the repo root:
#   docker build -f packages/workloads/echo-pydanticai/Dockerfile -t echo-pydanticai .
# Base and uv pinned by digest, the same as packages/chassis/Dockerfile (deploy/compose/SECURITY.md,
# section 2). Bump both there and here on purpose, in their own commit.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.23@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# `--locked` checks the whole workspace against uv.lock, so every member's pyproject.toml is here;
# only the two packages this image runs are synced. The chassis is never installed.
COPY pyproject.toml uv.lock ./
COPY packages/chassis/pyproject.toml packages/chassis/
COPY packages/contract-suites/pyproject.toml packages/contract-suites/
COPY packages/fake-model-server/pyproject.toml packages/fake-model-server/
COPY packages/workload-a2a/pyproject.toml packages/workload-a2a/
COPY packages/workloads/echo-python/pyproject.toml packages/workloads/echo-python/
COPY packages/workloads/echo-pydanticai/pyproject.toml packages/workloads/echo-pydanticai/
COPY packages/workloads/echo-langgraph/pyproject.toml packages/workloads/echo-langgraph/
COPY packages/workloads/echo-openai-agents/pyproject.toml packages/workloads/echo-openai-agents/
COPY packages/workloads/echo-claude-agent/pyproject.toml packages/workloads/echo-claude-agent/
COPY packages/workloads/echo-smolagents/pyproject.toml packages/workloads/echo-smolagents/
COPY packages/bakeoff/pyproject.toml packages/bakeoff/
COPY packages/fake-mcp-server/pyproject.toml packages/fake-mcp-server/
COPY packages/code-runner/pyproject.toml packages/code-runner/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package echo-pydanticai --package workload-a2a --no-install-workspace
COPY packages/workload-a2a/src packages/workload-a2a/src
COPY packages/workloads/echo-pydanticai/src packages/workloads/echo-pydanticai/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package echo-pydanticai --package workload-a2a

FROM ${PYTHON_IMAGE} AS runtime
# uid 10002: the one uid table in deploy/README.md. No home directory: HOME points at /tmp.
RUN groupadd --gid 10002 workload && useradd --uid 10002 --gid workload --no-create-home \
    --home-dir /tmp --shell /usr/sbin/nologin workload
WORKDIR /app
# Owned by root and read-only to the app user: the process cannot change its own code.
COPY --from=build /app /app
# No key, ever: the model URL and the tool URL are the chassis's proxies, which hold the
# credentials. 127.0.0.1: the chassis reaches the workload on localhost (sidecar lane). A Compose
# file that runs it in its own network namespace sets HOST=0.0.0.0 there, on purpose, and then
# needs `--allow-any-host` (workload-a2a refuses a host that is not loopback without it).
# PYDANTIC_AI_NO_BANNER: keep PydanticAI's first-run banner out of the service log.
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp PYDANTIC_AI_NO_BANNER=1 \
    HOST=127.0.0.1 PORT=9000 \
    CHASSIS_MODEL_URL=http://127.0.0.1:8090/v1 CHASSIS_TOOL_URL=http://127.0.0.1:8090/mcp
USER 10002:10002
EXPOSE 9000
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD ["python", "-c", "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PORT']}/.well-known/agent-card.json\", timeout=2).status == 200 else 1)"]
# `sh -c` so HOST and PORT come from the environment; `exec` keeps workload-a2a as PID 1 for
# signals. Extra `docker run` arguments are appended (for example `--log-level debug`).
ENTRYPOINT ["sh", "-c", "exec workload-a2a serve --handle echo_pydanticai:handle --name echo-pydanticai --version 0.1.0 --host \"$HOST\" --port \"$PORT\" \"$@\"", "--"]
