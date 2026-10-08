# The echo-python workload: `handle` served over A2A by workload-a2a, from the locked
# workspace. PoC-1 ran it inside the chassis image (`inprocess`); PoC-2 gives it its own container
# for the `sidecar` lane (deploy/compose/docker-compose.sidecar.yaml). Build from the repo root:
#   docker build -f packages/workloads/echo-python/Dockerfile -t echo-python .
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
COPY packages/fake-mcp-server/pyproject.toml packages/fake-mcp-server/
COPY packages/code-runner/pyproject.toml packages/code-runner/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package echo-python --package workload-a2a --no-install-workspace
COPY packages/workload-a2a packages/workload-a2a
COPY packages/workloads/echo-python packages/workloads/echo-python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package echo-python --package workload-a2a

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --gid 10001 workload && useradd --uid 10001 --gid workload --create-home workload
WORKDIR /app
COPY --from=build --chown=workload:workload /app /app
# No key, ever: the model URL and the tool URL are the chassis's proxies, which hold the
# credentials. 127.0.0.1: the chassis reaches the workload on localhost (sidecar lane). A Compose
# file that runs it in its own network namespace sets HOST=0.0.0.0 there, on purpose, and then
# needs `--allow-any-host` (workload-a2a refuses a host that is not loopback without it).
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    HOST=127.0.0.1 PORT=9000 \
    CHASSIS_MODEL_URL=http://127.0.0.1:8090/v1 CHASSIS_TOOL_URL=http://127.0.0.1:8090/mcp
USER workload
EXPOSE 9000
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD ["python", "-c", "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PORT']}/.well-known/agent-card.json\", timeout=2).status == 200 else 1)"]
# `sh -c` so HOST and PORT come from the environment; `exec` keeps workload-a2a as PID 1 for
# signals. Extra `docker run` arguments are appended (for example `--log-level debug`).
ENTRYPOINT ["sh", "-c", "exec workload-a2a serve --handle echo_python:handle --name echo-python --version 0.1.0 --host \"$HOST\" --port \"$PORT\" \"$@\"", "--"]
