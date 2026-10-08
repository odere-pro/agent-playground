# The code-runner image: the run_python MCP server for the gVisor sandbox pod (PoC-5).
# Build from the repo root:
#   docker build -f packages/code-runner/Dockerfile -t agent-platform/code-runner:poc05 .
# Same base and the same locked resolve as packages/fake-model-server/Dockerfile.
# No secret at build or run time. Works with a read-only root: the only writable path it
# needs is /tmp, which the pod mounts as a memory-backed emptyDir with a size cap.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.23@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
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
    uv sync --locked --no-dev --package code-runner --no-install-workspace
COPY packages/code-runner/src packages/code-runner/src
COPY packages/code-runner/README.md packages/code-runner/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package code-runner

FROM ${PYTHON_IMAGE} AS runtime
# uid 10003 per plan section 2.11. No home directory: HOME and TMPDIR point at /tmp.
RUN groupadd --gid 10003 runner && useradd --uid 10003 --gid runner --no-create-home \
    --home-dir /tmp --shell /usr/sbin/nologin runner
WORKDIR /app
# Owned by root and read-only to the runner: the code cannot change the server.
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp TMPDIR=/tmp
USER 10003:10003
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["code-runner", "--host", "0.0.0.0", "--port", "8000", "--tmp-root", "/tmp"]
