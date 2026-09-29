# deploy

How the chassis and its workloads run.

| Folder | Arrives in | What |
| ------ | ---------- | ---- |
| `compose/` | PoC-1 walking skeleton | Docker Compose: the chassis, LiteLLM, and the fake model server or llama.cpp; later the workload container, MinIO, and the observability stack |
| `kind/` | PoC-5 | A local kind cluster with native sidecars, a CNI that enforces NetworkPolicy, a gVisor RuntimeClass, and the admission policies |
| `helm/` | PoC-9 | The shared library chart that adds the chassis container with a pinned tag (024 CH-3) |

## Compose (PoC-1)

Two variants, one file each. The rules for keys, images, ports, and health are in [`compose/SECURITY.md`](compose/SECURITY.md).

```bash
cd deploy/compose
cp .env.example .env                      # optional; the fake variant runs with every value empty

# fake: chassis + LiteLLM + the scripted fake model server. No key, no internet after the pull.
docker compose up -d --wait
curl -s 127.0.0.1:8080/health
curl -s -X POST 127.0.0.1:8080/v1/run -H 'content-type: application/json' \
  -d '{"input": {"text": "simplify: the quick brown fox"}}'
docker compose down

# local: big-default on an API provider, local-small on llama.cpp.
# Needs OPENAI_API_KEY, BIG_DEFAULT_MODEL, and LLAMA_MODEL_PATH (a GGUF file) in .env.
docker compose -f docker-compose.yaml -f docker-compose.local.yaml up -d --wait

# The PoC-1 demo: both routes, streaming and complete, then the router's token log.
./demo.sh            # fake
./demo.sh --local    # real models
```

What runs:

| Service | Image | Port on the host | Holds |
| ------- | ----- | ---------------- | ----- |
| `chassis` | built from `packages/chassis/Dockerfile`, config `packages/chassis/configs/local.yaml` | `127.0.0.1:8080` | `LITELLM_API_KEY` |
| `litellm` | `ghcr.io/berriai/litellm`, pinned by digest, config `compose/litellm/config.yaml` (fake) or `config.local.yaml` (local) | `127.0.0.1:4000` | `LITELLM_MASTER_KEY`, `OPENAI_API_KEY` |
| `fake-model-server` | built from `packages/fake-model-server/Dockerfile` | none | nothing |
| `llama-cpp` (local only) | `ghcr.io/ggml-org/llama.cpp:server`, pinned by digest | none | nothing |

Routes: `big-default` and `local-small`. The chassis picks one in `spec.model.route`; the demo swaps it by mounting an edited config and restarting the chassis. Token counts per call show as one `token_log` line in `docker compose logs litellm` (route, tokens, cost, `agent:<name>` tag; never the text), printed by `compose/litellm/token_log.py`. LiteLLM's `/spend/logs` needs a database behind it, which PoC-1 does not run.

The fake variant runs LiteLLM without a master key and passes it no key env at all: LiteLLM treats an empty `LITELLM_MASTER_KEY` as "auth on" and rejects every call, which would break `docker compose up` with an empty `.env`. The `local` variant needs `LITELLM_MASTER_KEY` and, for PoC-1, `LITELLM_API_KEY` set to the same value (debt: `pocs/poc-01-walking-skeleton/notes/2026-09-29-compose-key-debt.md`).

Bump an image on purpose, in its own commit, with the output of `docker buildx imagetools inspect <image:tag>` in the PR.
