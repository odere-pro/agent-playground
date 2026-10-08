# deploy

How the chassis and its workloads run.

| Folder | Arrives in | What |
| ------ | ---------- | ---- |
| `compose/` | PoC-1 walking skeleton | Docker Compose: the chassis, LiteLLM, and the fake model server or llama.cpp; since PoC-2 one workload container next to the chassis (the sidecar variant); later MinIO and the observability stack. Files and variants: [`compose/README.md`](compose/README.md) |
| `kind/` | PoC-4, then PoC-5 | PoC-4: a local kind cluster `poc04` with the two container-role variants (native sidecar, preStop) and their drills; see [kind (PoC-4)](#kind-poc-4). PoC-5 adds a gVisor RuntimeClass and the admission policies |
| `helm/` | PoC-9 | The shared library chart that adds the chassis container with a pinned tag (024 CH-3) |

## Container uids

suggested: one non-root uid per image role. The gid equals the uid. The Dockerfile creates it and sets a numeric `USER uid:gid`. Every Compose `user:` and every kind `runAsUser`/`runAsGroup` that runs the image uses the same value.

| Image | uid:gid |
| ----- | ------- |
| `chassis` | 10001:10001 |
| `echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-typescript` (every workload) | 10002:10002 |
| `code-runner` | 10003:10003 |
| `fake-model-server` | 10004:10004 |
| `fake-mcp-server` | 10005:10005 |

The chassis and its workload never share a uid (PoC-5 H26), and no fake server shares one with a workload or the code-runner. Every image copies `/app` owned by root, so the app user can read its code but not change it. No image has a home directory: `HOME=/tmp`, and Python images set `PYTHONDONTWRITEBYTECODE=1`. `packages/chassis/tests/test_image_uids.py` checks the table against every Dockerfile, Compose file, and kind manifest. Rejected admission fixtures are skipped: each differs from its admitted twin in one named path on purpose. Third-party images keep their own users (Valkey 999:1000, Postgres 70, LiteLLM 10010 in kind, MinIO and `mc` 10001 in Compose and kind, Traefik 65534).

## Compose (PoC-1)

The PoC-2 sidecar variant (one workload container in the chassis's network namespace, `demo-sidecar.sh`) is in [`compose/README.md`](compose/README.md#sidecar-variant-poc-2).

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

## kind (PoC-4)

Which container roles drain without losing a request: the workload as a Kubernetes native sidecar, or two plain containers ordered by preStop sleeps. Plan: `docs/plans/2026-10-01-poc-04-stateless-scalable.md`, section 8b. Plain manifests with kustomize, no Helm.

```bash
make kind-poc04 ARGS="up native-sidecar"            # create, build, kind load, apply (echo-python)
make kind-poc04 ARGS="apply prestop typescript"     # switch variant or engine
make kind-poc04 ARGS="drill-rolling"                # rollout restart under 20 clients, no retry
make kind-poc04 ARGS="drill-hung"                   # SIGSTOP one workload from the node
make kind-poc04 ARGS="delete"                       # kind delete cluster --name poc04
```

`run.sh` touches only the kind cluster `poc04`: every kubectl call passes `--context kind-poc04`, every kind call `--name poc04`. The agent Service's nodePort 30080 is on `127.0.0.1:18081` (suggested). Images are built with the tag `poc04` and loaded with `kind load`; nothing is pulled for them (`imagePullPolicy: Never`).

What runs, in namespace `poc04` (Pod Security Standard `restricted`, enforced):

| Folder | What |
| ------ | ---- |
| `kind/cluster.yaml` | One node, kind v0.33.0's default image `kindest/node:v1.37.0`, pinned by digest |
| `kind/poc04/base/` | The fake model server, Valkey (no persistence), the agent Service (chassis port only), the bootstrap ConfigMap (`config: memory`, `state: valkey`), and NetworkPolicies: default deny in and out, then the agent pod may reach only the fake model server, Valkey, and DNS |
| `kind/poc04/native-sidecar/` | The workload in `initContainers` with `restartPolicy: Always`, the chassis the main container. Chassis `--drain-delay-s 10 --drain-timeout-s 30` (`Connection: close` while draining), grace 50 s |
| `kind/poc04/prestop/` | Two plain containers. Chassis `preStop` sleep 5 and `--drain-delay-s 0`; workload `preStop` sleep 35; grace 50 s. The chassis waits for the agent card before it starts |
| `kind/poc04/typescript/<variant>/` | The same variant with `echo-typescript`: its image, `node` probes, and `DRAIN_TIMEOUT_MS=30000` |

Both variants: 3 replicas, `maxUnavailable: 0`, `maxSurge: 1`, no service account token. Every container has a read-only root file system, its own `emptyDir` at `/tmp`, a non-root user, no privilege escalation, and no capability. The chassis is ready on `/ready` (every 2 s) and live on `/health`. The workload listens on 127.0.0.1 only, so the kubelet's `httpGet` cannot reach it: its startup and liveness probes `exec` a GET on the agent card. Its liveness probe has its own `terminationGracePeriodSeconds: 10` (suggested): a hung, stopped process ignores SIGTERM, and without it the restart waits for the preStop sleep and the pod's grace.

Secrets: `run.sh` generates `VALKEY_PASSWORD` with `openssl rand` into the Secret `poc04-secrets`, through a pipe. Only the chassis and Valkey read it; the workload gets four plain variables and no Secret. Known PoC gap: Valkey gets the password as a command-line argument inside its own pod.
