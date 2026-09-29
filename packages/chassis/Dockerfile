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
COPY pyproject.toml uv.lock ./
COPY packages/chassis/pyproject.toml packages/chassis/
COPY packages/contract-suites/pyproject.toml packages/contract-suites/
COPY packages/fake-model-server/pyproject.toml packages/fake-model-server/
COPY packages/workloads/echo-python/pyproject.toml packages/workloads/echo-python/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package chassis --package echo-python --no-install-workspace
# The echo-python workload rides in this image for the `inprocess` lane (PoC-1 only; PoC-2 moves
# it to its own container).
COPY packages/chassis packages/chassis
COPY packages/workloads/echo-python packages/workloads/echo-python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package chassis --package echo-python

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --gid 10001 chassis && useradd --uid 10001 --gid chassis --create-home chassis
WORKDIR /app
COPY --from=build --chown=chassis:chassis /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER chassis
EXPOSE 8080
# The chassis answers `/health` before the engine is up and `/ready` after (server/app.py).
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["chassis", "serve"]
CMD ["--config", "/etc/chassis/config.yaml", "--host", "0.0.0.0", "--port", "8080"]
