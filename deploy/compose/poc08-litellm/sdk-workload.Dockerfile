# The live drill's workload (live-drill.yaml, service `workload`): the echo-python image, the
# sidecar workload the chassis serves over A2A, plus the stock `openai` and `anthropic` SDKs in its
# own venv, so a stock client runs in the workload container (CH-2, 013). The versions are
# uv.lock's (openai 3.22.1, anthropic 1.11.0). suggested: no hashes; the drill image is never
# shipped. `workload-base` is the echo-python image, a Compose `service:` build context.
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.23@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479

FROM ${UV_IMAGE} AS uv

FROM workload-base AS sdk
USER root
COPY --from=uv /uv /usr/local/bin/uv
RUN uv pip install --python /app/.venv/bin/python openai==3.22.1 anthropic==1.11.0

FROM workload-base
# The venv owned by root, read-only to the workload user, as in the base image.
COPY --from=sdk /app/.venv /app/.venv
# The drill's client script (sdk_clients.py) is mounted read-only by live-drill.yaml at
# /opt/drill: the repo's .dockerignore keeps deploy/ out of every build context.
USER 10002:10002
