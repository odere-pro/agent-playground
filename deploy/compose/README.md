# deploy/compose

The Compose stacks for the PoCs. The PoC-1 stack (chassis, LiteLLM, fake model server or llama.cpp) and how to run it are in [`../README.md`](../README.md). The rules for keys, images, ports, health, and the shared network namespace are in [`SECURITY.md`](SECURITY.md).

| File | What |
| ---- | ---- |
| `docker-compose.yaml` | PoC-1 base, `fake` variant: the chassis (workload `inprocess`), LiteLLM, the fake model server |
| `docker-compose.local.yaml` | Real models: `big-default` on an API provider, `local-small` on llama.cpp |
| `docker-compose.sidecar.yaml` | PoC-2 overlay, `sidecar` lane: one workload container next to the chassis |
| `demo.sh` | PoC-1 demo (unchanged) |
| `demo-sidecar.sh` | PoC-2 demo: the same request to four engines |
| `demo-interfaces.sh` | PoC-3 demo: one agent called from the OpenAI SDK, the Anthropic SDK, and an MCP client, on each of the four engines |
| `docker-compose.scale.yaml` | PoC-4 scale stack, project `poc04`: Traefik, 1 to 4 chassis and workload pairs, Valkey, MinIO, the fake model server, Kafka (profile `events`) |
| `docker-compose.scale-dapr.yaml` | PoC-4 overlay: a daprd sidecar in each pair; components in `dapr/` |
| `traefik/poc04.yaml` | Traefik's routes: the four chassis, health check on `/ready` |
| `scale.sh` | The one entry point for the scale stack: `build`, `up`, `down`, `ps`, `logs` |

## Sidecar variant (PoC-2)

The chassis mounts `packages/chassis/configs/sidecar.yaml` (`spec.engine.connector: sidecar`, `url: http://127.0.0.1:9000`, model `litellm`, tools `fake`). One workload runs next to it, picked by a Compose profile, in the chassis's network namespace (`network_mode: "service:chassis"`):

| Profile | Service | Image built from |
| ------- | ------- | ---------------- |
| `python` | `workload-python` | `packages/workloads/echo-python/Dockerfile` |
| `pydanticai` | `workload-pydanticai` | `packages/workloads/echo-pydanticai/Dockerfile` |
| `langgraph` | `workload-langgraph` | `packages/workloads/echo-langgraph/Dockerfile` |
| `typescript` | `workload-typescript` | `packages/workloads/echo-typescript/Dockerfile` (its own folder is the context) |

Each workload serves A2A on `127.0.0.1:9000` and calls the chassis's model proxy at `http://127.0.0.1:8090/v1` and the MCP tool endpoint at `http://127.0.0.1:8090/mcp`. It holds no key and publishes no port; only `127.0.0.1:8080` (the chassis) and `127.0.0.1:4000` (LiteLLM) are on the host.

```bash
cd deploy/compose
dc() { docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml "$@"; }   # bash and zsh

# One workload. The chassis starts first and waits for the workload's agent card.
dc --profile pydanticai up -d --wait
curl -s 127.0.0.1:8080/ready
curl -s -X POST 127.0.0.1:8080/v1/run -H 'content-type: application/json' \
  -d '{"input": {"text": "glossary: what does SLM mean?"}}'

# Swap the workload: remove the old one, recreate the chassis next to the new one.
dc --profile pydanticai rm -sf workload-pydanticai
dc --profile langgraph up -d --wait --no-deps --force-recreate chassis workload-langgraph

dc --profile python --profile pydanticai --profile langgraph --profile typescript down

# Real models: add the local file last.
dc -f docker-compose.local.yaml --profile python up -d --wait

# The PoC-2 demo: all four, complete and streamed, token lines, and the no-key check.
# Writes pocs/poc-02-two-engines-one-contract/demo/<date>-demo-sidecar.md.
./demo-sidecar.sh            # fake
./demo-sidecar.sh --local    # real models
```

The demo sends the three Python workloads a `glossary` request, so each runs the `glossary_lookup` tool through the chassis. The TypeScript profile gets a plain `simplify` request instead, because it has no tool client. The key check lists key-like variable names in each workload container and allow-lists one name, `GPG_KEY`: the official python image sets it to the public id of the CPython release signing key, not a credential (`SECURITY.md`, section 6). The fake variant ran on 2026-10-01 and exited 0: [the record](../../pocs/poc-02-two-engines-one-contract/demo/2026-10-01-demo-sidecar.md). The `--local` variant has not run yet; it needs a provider key in `.env`.

Start order: the workload needs the chassis's namespace, so the chassis container starts first. Its entrypoint waits for `http://127.0.0.1:9000/.well-known/agent-card.json` (suggested: up to 120 s), then runs `chassis serve`, so `up --wait` needs no manual step. There is no restart policy: a restarted chassis gets a new namespace and would strand the workload. Why, and what Kubernetes does instead: `SECURITY.md`, section 6.

`dc --profile <p> config` renders the merged file without a running daemon. The offline checks are `pocs/poc-02-two-engines-one-contract/tests/test_compose_sidecar.py`.

## Every client (PoC-3)

`demo-interfaces.sh` runs the sidecar stack above (`fake` variant, no key) once per engine and calls the agent through the chassis's public port with off-the-shelf clients only: the OpenAI Python SDK and the Anthropic Python SDK (both streamed, each chunk printed with its time), and `fastmcp.Client` (list the tool, call it), once at `127.0.0.1:8080/v1/mcp` and once through LiteLLM's MCP gateway at `127.0.0.1:4000/mcp/`. The clients are `pocs/poc-03-one-interface-every-client/demo/clients.py`; they get a base URL, a placeholder key, and `model: echo`. After each call the script prints the router's new `token_log` lines and fails if there are none. It needs `uv` on `PATH` and writes `pocs/poc-03-one-interface-every-client/demo/<date>-demo-interfaces.md`.

```bash
./demo-interfaces.sh
```

The gateway is the `mcp_servers.chassis_agent` entry in `litellm/config.yaml`: LiteLLM reaches `http://chassis:8080/v1/mcp` on the Compose network, carries no auth header, and adds no published port. `allow_all_keys: true` is needed because the fake LiteLLM runs with no keys; without it the gateway lists no tools. Only the fake config has the entry; `config.local.yaml` (auth on) does not yet. The offline checks are `pocs/poc-03-one-interface-every-client/tests/test_compose_interfaces.py`.

## Scale stack (PoC-4)

`docker-compose.scale.yaml` is a file of its own, project `poc04`, network `poc04`. Traefik on `127.0.0.1:18080` balances over explicit pairs `chassis-N` and `workload-N`; each workload joins its chassis's network namespace, as in the sidecar variant. The chassis reaches the fake model server directly (no LiteLLM in the measured path), keeps state in Valkey, and reads its agent config from MinIO (`s3://agent-configs/agents/echo.yaml`, seeded by `minio-init` on every `up`). Design: `docs/plans/2026-10-01-poc-04-stateless-scalable.md`, section 8a. Rules: `SECURITY.md`, section 7.

| Pairs | Profiles | Services |
| ----- | -------- | -------- |
| 1 | none | `chassis-1`, `workload-1` |
| 2 | `pairs2` | adds `chassis-2`, `workload-2` |
| 4 | `pairs4` | adds pairs 2, 3, and 4 |

Use `scale.sh`, never a bare `docker compose`: it generates the secrets the files require into the git-ignored `.env.poc04` (mode 600) and passes `-p poc04` on every command.

```bash
cd deploy/compose
./scale.sh build                               # chassis, fake model server, four workloads, tag poc04
./scale.sh up echo-python 2                    # 2 pairs; echo-pydanticai, echo-langgraph, echo-typescript too
./scale.sh up inprocess 1                      # echo_python in the chassis, no workload: the hop baseline
./scale.sh up echo-python 1 --events kafka     # Kafka, result events from the chassis's own client
./scale.sh up echo-python 1 --events dapr      # Kafka through a daprd per pair (the Dapr overlay)
curl -s 127.0.0.1:18080/ready
./scale.sh ps
./scale.sh down                                # project poc04 only; deletes .env.poc04
```

Every `up` starts from a clean project: earlier pairs, daprd, and Valkey data are removed first. The chassis config is picked by `CHASSIS_CONFIG`, which `scale.sh` sets: `packages/chassis/configs/scale.yaml` (sidecar), `scale-kafka.yaml`, `scale-dapr.yaml`, or `scale-inprocess.yaml`. Each chassis runs `chassis serve --drain-delay-s 3 --drain-timeout-s 30`, and each Python workload `workload-a2a serve --drain-timeout-s 30` (TypeScript: `DRAIN_TIMEOUT_MS=30000`). To stop a pair without failing a request, send SIGTERM to the chassis first (`docker kill -s TERM poc04-chassis-2-1`), wait for it to exit, then stop the workload: `docker compose stop` stops the workload first, which is the wrong order (plan, section 6).

Render without a daemon, with dummy secrets: `VALKEY_PASSWORD=x MINIO_ROOT_PASSWORD=x CHASSIS_S3_ACCESS_KEY=x CHASSIS_S3_SECRET_KEY=x DAPR_API_TOKEN=x APP_API_TOKEN=x docker compose -p poc04 -f docker-compose.scale.yaml --profile pairs4 config`. The Dapr overlay needs `--profile events` (daprd depends on Kafka). The offline checks are `pocs/poc-04-stateless-scalable/tests/test_read_only_compose.py`.

## PoC-6: the one Mac command

`scripts/poc06_mac.sh` produces the PoC-6 numbers that need a real model. Nothing else in PoC-6 needs one. It is the only place a provider key is used. Plan: `docs/plans/2026-10-09-poc-06-bake-off.md`. Offline checks: `pocs/poc-06c-pretrained-slm/tests/test_poc06c_mac_static.py`.

**What you do.**

1. Put the provider key in `deploy/compose/.env`. The variable is `OPENAI_API_KEY`, as in the PoC-1 `local` variant. For another name, set `POC06_HOSTED_KEY_ENV`. For another model, set `POC06_HOSTED_MODEL` to a LiteLLM model string (suggested default: `openai/gpt-4o-mini`).
2. `brew install llama.cpp`. Docker Desktop must run, with about 7.75 GiB (the `kind` step needs at least 6000 MiB, `POC06_KIND_MIN_MIB`, suggested). `uv`, `node`, `npm`, and `make` must be installed. The `kind` step also needs `kind`, `kubectl`, and `jq`, and no kind cluster named `poc05` (it deletes the one it makes).
3. `make poc06-mac ARGS="--dry-run"` first. It prints every step and checks that every file exists. It reads no key and starts nothing.
4. `make poc06-mac ARGS="--push"` for the real run. `--only hosted|slm|scale|load|kind` runs one step. `--engine A,B` picks the engines of `hosted` and `slm`.

**What it runs.** Every engine here is a trusted one: `echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-openai-agents`, `echo-typescript`. The script refuses any other engine, and so does `bakeoff run --model-url`. An untrusted engine (`echo-smolagents`, `echo-claude-agent`, `kagent-adk`) runs model-written code, so with a real model it runs only on kind under gVisor. That is the `kind` step (part 2).

| Step | What | Model | Where |
| ---- | ---- | ----- | ----- |
| `hosted` | `bakeoff run --route big-default --tasks smoke,simplifier,lookup --repeat 5`, `inprocess` and `sidecar` lanes (6a and 6b criterion 5) | the hosted model | LiteLLM in `poc06/litellm-hosted.yaml` (project `poc06mac`, `127.0.0.1:14000`); chassis and workloads as host processes, as in `bakeoff smoke` |
| `slm` | the same run with `--route local-small` (6c) | Qwen3-1.7B Q8_0 | `llama-server --jinja --chat-template-kwargs {"enable_thinking":false} -c 16384 -np 4` on the host, behind the same LiteLLM through `host.docker.internal` |
| `scale` | the PoC-4 matrix with 1, 2, 4 pairs of `echo-python` and `echo-openai-agents`, 16 users, 60 s each (6c criterion 4) | the same llama-server | the PoC-4 scale stack (project `poc04`) plus `poc06/scale-litellm-slm.yaml`: LiteLLM and `spec.model.route: local-small` |
| `load` | the PoC-4 matrix for `echo-openai-agents` and `echo-typescript`, 64 users, 60 s each (6a criterion 4) | the fake model server | the PoC-4 scale stack, through `poc06/load_driver.py` |
| `kind` | `echo-smolagents`, `echo-claude-agent`, and `kagent-adk`: smoke, simplifier, lookup, 5 repeats, `--target` mode (6b criterion 5, untrusted engines). Runs last, after the Compose stacks are down | the hosted model, route `big-default` | PoC-5's kind cluster under gVisor: `deploy/kind/poc06/run.sh up`, then `hosted.sh up` (provider key on stdin), `hosted.sh run`, and on any exit `hosted.sh down` and `run.sh delete` |

`poc06/load_driver.py` loads the PoC-4 `run_matrix.py` unchanged and points it at `poc06/scale-slm.sh`, which wraps `scale.sh`. The wrapper adds the `echo-openai-agents` image (scale.sh does not list it) and, for `scale`, the LiteLLM overlay. The `slm` step changes only `--route`; `scale` changes only the route in the seeded chassis config (`poc06/chassis-configs/scale-slm.yaml`).

**Keys.** The provider key goes from `.env` into one shell variable, then into the environment of the one `docker compose` command that starts LiteLLM. Only the LiteLLM container of the `hosted` step has it; the `slm` step's LiteLLM gets none. Set a spend limit at the provider before the run. The master-key-as-chassis-key shortcut is recorded in `SECURITY.md`, section 1. The chassis gets a LiteLLM key generated for the run (`sk-poc06-` and 48 hex characters), and it is the proxy's master key for that run. The script prints neither, and scrubs both from the notes.

**Keys on kind.** The `kind` step puts the provider key in one Secret, `litellm-provider`, that only the LiteLLM pod reads. LiteLLM is the only pod with internet egress, TCP 443 only. Each of the three chassis keys is scoped to two routes (`fake-chat`, `big-default`) and a budget. No workload holds a key. See `SECURITY.md`, section 1.

**The model file.** `llama-server` loads `~/.cache/poc06/Qwen3-1.7B-Q8_0.gguf` from `Qwen/Qwen3-1.7B-GGUF` (suggested). The first run downloads it (about 1.8 GB) and writes its sha256 to `deploy/compose/poc06/model.sha256`. The download is HTTPS only, from revision `main` (`POC06_GGUF_REV`, suggested); that is trust on first use, and the pin is what protects later runs. Later runs refuse a file with another hash. `--push` does not commit that pin, because it commits only the notes; commit it once by hand.

**Time and memory.** Suggested: 60 to 90 minutes. The first run adds the image builds and the download. Memory: the Docker VM runs one stack at a time. The 4-pair scale and load runs are the tight ones: PoC-4 saw 4 pairs strain a 7.9 GiB VM (`pocs/poc-04-stateless-scalable/notes/2026-10-01-load-results.md`), and LiteLLM adds up to 768 MiB. The host runs `llama-server` (about 2.5 GiB with 4 slots at a 16384-token context, suggested) outside Docker. A failed scenario is kept in the note. Close other containers first.

**What it writes.** Dated notes, each with the command, the environment (commit, machine, Docker memory, models, llama.cpp version, GGUF sha256, server flags), the last 60 lines of output, the results table, and an empty "Reading":

- `pocs/poc-06a-bake-off-sidecar-lane/notes/<date>-hosted-run.md` and `<date>-load-run.md`
- `pocs/poc-06c-pretrained-slm/notes/<date>-slm-run.md` and `<date>-scale-run.md`
- a `.results.json` next to each; the note holds `results.md`

With `--push` it commits only those files by exact path, on the current branch (never `main` or `master`), and pushes it. It refuses when any other commit on the branch is not pushed yet, and names it.

**Cleanup.** On any exit, including Ctrl-C, a `trap` stops `llama-server`, the Compose projects `poc06mac` and `poc04`, and removes its temp files. It touches no other project and prunes nothing.
