# The Agent Scan sandbox image (PoC-7, W5). Build it only through scan.sh:
#   docker build -f deploy/agent-scan/Dockerfile .
# Its context is the allowlist in Dockerfile.dockerignore (BuildKit), not the root .dockerignore.
# Two virtualenvs, no repo source:
#   /opt/scan   snyk-agent-scan 0.6.9, every wheel pinned by sha256 (requirements.txt).
#   /app/.venv  the third-party deps of fake-mcp-server and code-runner, from uv.lock.
# The repo's code comes from a read-only mount at /repo at run time (stdio_server.py puts the
# package sources on sys.path), so the scan reads the checked-out tool descriptions, not a copy
# baked in at build time.
# Same base and the same uv as packages/fake-mcp-server/Dockerfile. No secret at build time.
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
COPY packages/workloads/echo-openai-agents/pyproject.toml packages/workloads/echo-openai-agents/
COPY packages/workloads/echo-claude-agent/pyproject.toml packages/workloads/echo-claude-agent/
COPY packages/workloads/echo-smolagents/pyproject.toml packages/workloads/echo-smolagents/
COPY packages/bakeoff/pyproject.toml packages/bakeoff/
COPY packages/code-runner/pyproject.toml packages/code-runner/
COPY packages/fake-model-server/pyproject.toml packages/fake-model-server/
COPY packages/fake-mcp-server/pyproject.toml packages/fake-mcp-server/
COPY packages/fake-langfuse/pyproject.toml packages/fake-langfuse/
COPY packages/guardrail-server/pyproject.toml packages/guardrail-server/
COPY packages/agentctl/pyproject.toml packages/agentctl/
COPY packages/agent-testkit/pyproject.toml packages/agent-testkit/
COPY packages/poc-harness/pyproject.toml packages/poc-harness/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package fake-mcp-server --package code-runner \
    --no-install-workspace
COPY deploy/agent-scan/requirements.txt /tmp/scan-requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python /usr/local/bin/python3.12 /opt/scan && \
    uv pip install --python /opt/scan/bin/python --require-hashes --no-deps \
    -r /tmp/scan-requirements.txt

FROM ${PYTHON_IMAGE} AS runtime
COPY --from=build /app/.venv /app/.venv
COPY --from=build /opt/scan /opt/scan
# `python` on PATH is the servers' venv: mcp.json starts each server with it. The scanner is
# called by its full path, /opt/scan/bin/snyk-agent-scan. HOME is the run's tmpfs.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
WORKDIR /repo
# nobody: scan.sh also passes --user 65534:65534.
USER 65534:65534
ENTRYPOINT ["/opt/scan/bin/snyk-agent-scan"]
