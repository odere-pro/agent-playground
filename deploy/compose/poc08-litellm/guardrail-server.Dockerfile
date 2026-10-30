# guardrail-server for the PoC-8 LiteLLM drill, with the `pattern` stand-in (no Prompt Guard 2:
# that needs a Hugging Face token, and CI holds none). Build from the repo root:
#   docker build -f deploy/compose/poc08-litellm/guardrail-server.Dockerfile .
# Same base, the same uv, and the same locked resolve as packages/chassis/Dockerfile.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.23@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# `--locked` checks the whole workspace against uv.lock, so every member's pyproject.toml is here.
COPY pyproject.toml uv.lock ./
COPY packages/chassis/pyproject.toml packages/chassis/
COPY packages/contract-suites/pyproject.toml packages/contract-suites/
COPY packages/fake-model-server/pyproject.toml packages/fake-model-server/
COPY packages/workloads/echo-python/pyproject.toml packages/workloads/echo-python/
COPY packages/workload-a2a/pyproject.toml packages/workload-a2a/
COPY packages/workloads/echo-pydanticai/pyproject.toml packages/workloads/echo-pydanticai/
COPY packages/workloads/echo-langgraph/pyproject.toml packages/workloads/echo-langgraph/
COPY packages/workloads/echo-openai-agents/pyproject.toml packages/workloads/echo-openai-agents/
COPY packages/workloads/echo-claude-agent/pyproject.toml packages/workloads/echo-claude-agent/
COPY packages/workloads/echo-smolagents/pyproject.toml packages/workloads/echo-smolagents/
COPY packages/bakeoff/pyproject.toml packages/bakeoff/
COPY packages/fake-mcp-server/pyproject.toml packages/fake-mcp-server/
COPY packages/code-runner/pyproject.toml packages/code-runner/
COPY packages/fake-langfuse/pyproject.toml packages/fake-langfuse/
COPY packages/guardrail-server/pyproject.toml packages/guardrail-server/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package guardrail-server --no-install-workspace
COPY packages/guardrail-server/src packages/guardrail-server/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package guardrail-server

FROM ${PYTHON_IMAGE} AS runtime
# suggested: uid 10007, not in deploy/README.md's uid table yet.
RUN groupadd --gid 10007 guard && useradd --uid 10007 --gid guard --no-create-home \
    --home-dir /tmp --shell /usr/sbin/nologin guard
WORKDIR /app
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
USER 10007:10007
EXPOSE 8095
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8095/health', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["guardrail-server", "--host", "0.0.0.0", "--port", "8095", "--classifier", "pattern"]
