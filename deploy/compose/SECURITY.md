# Compose stack: security requirements (PoC-1, `local` and `fake` profiles)

Rules from ADR-001 hard requirement 1 and item 4, `deploy/CLAUDE.md`, and the root hard rules. The developer follows this; the reviewer checks it. Values the epic does not give are marked `suggested:`.

## 1. Who holds which credential

| Credential | Lives in | Set from | Never in |
| ---------- | -------- | -------- | -------- |
| Provider API key (`OPENAI_API_KEY` or similar) | LiteLLM container env only | `.env` (untracked) | `docker-compose.yaml`, `litellm/config.yaml`, chassis env, logs, prompts |
| LiteLLM master key (`LITELLM_MASTER_KEY`) | LiteLLM container env only | `.env` | any other container, any file |
| Chassis key (`LITELLM_API_KEY`) | Chassis container env only | `.env` | LiteLLM config, logs, prompts, the fake model server |

- Commit `deploy/compose/.env.example` with every name and an empty value. `.gitignore` already ignores `.env` and keeps `.env.example`.
- Pass keys by interpolation, one per service: LiteLLM gets `OPENAI_API_KEY=${OPENAI_API_KEY}` and `LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}`; the chassis gets `LITELLM_API_KEY=${LITELLM_API_KEY}` and `LITELLM_BASE_URL=http://litellm:4000/v1` (plus `CHASSIS_MODEL_URL`, the chassis's own proxy for the in-process workload; not a secret). Do not put `env_file: .env` on the chassis or the fake model server: that would hand them the provider key.
- `litellm/config.yaml` references keys as `api_key: os.environ/OPENAI_API_KEY` and `master_key: os.environ/LITELLM_MASTER_KEY` (suggested: LiteLLM's `os.environ/` syntax). A literal that starts with `sk-` is a defect, except the placeholder `api_key: fake` for the fake model server, which is not a secret.
- **Master key vs. virtual key.** The master key administers LiteLLM; the chassis should call with a scoped virtual key (models, budget, rate limit). LiteLLM issues virtual keys only with a Postgres behind it. For PoC-1 (one consumer, `inprocess` lane, no workload container) do this: **suggested: set `LITELLM_API_KEY` to the master key's value in `.env`, PoC-1 only.** Keep the two names separate so the switch to a real virtual key is a `.env` edit. Record it as debt in `pocs/poc-01-*/notes/`: it fails G-1's criterion "no service config holds the master key". It must be paid by PoC-5 (one scoped key per service is an exit criterion) and it blocks PoC-7 budgets. Not recommended now: the LiteLLM Postgres, because `docker compose up` would then need a key-generation step before the chassis can start, which breaks "no manual steps".
- No secret in any prompt or log: the chassis never puts env values into a prompt, and the fake model server's scripts contain no key.

## 2. Images and dependencies pinned

- Every `image:` is `name:tag@sha256:<digest>`. Resolve with `docker buildx imagetools inspect ghcr.io/berriai/litellm:<tag>` (or `docker manifest inspect`) and paste the digest. The digest is checked in and bumped on purpose, in its own commit, with the command output in the PR.
- LiteLLM: suggested a release after 1.82.8; 1.82.7 and 1.82.8 were malicious (reuse analysis, watch-outs). Verify the tag against <https://docs.litellm.ai/blog/security-update-march-2026>.
- llama.cpp and Postgres (if ever added) follow the same rule. The chassis and fake model server images are built from this repo; their `FROM` base is pinned by digest too.
- Chassis Python deps come from `uv.lock` only: suggested `uv sync --locked --no-dev` in the Dockerfile, so uv checks the lock's hashes and refuses a drifted resolve. No `pip install <name>` without a hash.

## 3. Network

- One Compose network (the project default is fine). No `network_mode: host` on any service. No `extra_hosts`, no `privileged`, no Docker socket mount.
- Published ports, and only these: chassis `127.0.0.1:8080:8080`; LiteLLM `127.0.0.1:4000:4000` for the demo `curl` and the token log. Fake model server and llama.cpp have no `ports:` at all. Every published port has the `127.0.0.1:` prefix.
- PoC-5 sidecar hostile case (`docs/planning/poc/005-PoC-5-sandboxed.md`, "hostile suite for the `sidecar` lane"): the workload cannot reach LiteLLM on its own. In PoC-1 the workload is in-process, so the case is not yet testable; it becomes live in PoC-2 when the workload container arrives and must get no `LITELLM_API_KEY`.

## 4. Offline variant

- Two files: `docker-compose.yaml` is the `fake` profile and the default. `docker-compose.local.yaml` is the `local` override (`docker compose -f docker-compose.yaml -f docker-compose.local.yaml up`). suggested: the offline stack is the default so a bare `docker compose up` needs no key.
- `fake`: LiteLLM loads `litellm/config.yaml`, where `big-default` and `local-small` both point at `http://fake-model-server:8081/v1` with `api_key: fake`. llama.cpp is not started. It must start with an empty `.env` and no internet.
- `local`: LiteLLM loads `litellm/config.local.yaml`; `big-default` is the API model, `local-small` is llama.cpp at `http://llama-cpp:8080/v1`.

## 5. Tags, logging, health

- Tags: every model call from the chassis carries `metadata.tags: ["agent:<name>"]` and the same in the `x-litellm-tags` header (suggested; per LiteLLM tag-based cost tracking). The route is the `model` field, so it needs no tag. suggested: `litellm_settings.enable_tag_budgets: true` is not needed in PoC-1; do not set it. Debt for G-2: the agent tag comes from the request, so it is spoofable until the virtual key carries it.
- Logging: `litellm_settings.turn_off_message_logging: true` (suggested), so LiteLLM keeps token counts but not request or response content. `set_verbose` and `--detailed_debug` stay off. The chassis adapter logs route, tokens, and status; never the `Authorization` header, never `LITELLM_API_KEY`.
- Health: LiteLLM `healthcheck` hits `/health/liveliness` (no key needed); the chassis `healthcheck` hits `/health`. The chassis has `depends_on: litellm: condition: service_healthy`; LiteLLM in `fake` depends on the fake model server the same way. suggested: use `python -c "import urllib.request,sys; ..."` for the LiteLLM check, because `curl` may be absent in the image; verify.

## Tests that prove it

- `grep -rn "sk-" deploy/compose --include=*.yaml` prints nothing except `api_key: fake`. `git ls-files deploy/compose | grep -x '.env'` prints nothing.
- `docker compose config | grep -E "image:"` shows `@sha256:` on every line. `docker compose config | grep -A1 published` shows only `127.0.0.1` hosts, and only ports 8080 and 4000.
- `docker compose exec chassis env | grep -c OPENAI` prints `0`. `docker compose exec fake-model-server env | grep -c LITELLM` prints `0`.
- With `.env` empty and the network off: `docker compose up -d --wait` exits 0 and `curl -s 127.0.0.1:8080/health` answers.
- `docker compose logs | grep -c "$LITELLM_API_KEY"` prints `0` after one demo request. LiteLLM's log shows tokens and the `agent:` tag, not the prompt text.

## Review checklist

1. Provider key and master key appear only in `.env` and only in LiteLLM's env? Y/N
2. Chassis env has `LITELLM_API_KEY` and nothing else secret; no `env_file: .env` on chassis or fake server? Y/N
3. `.env` untracked, `.env.example` committed with empty values? Y/N
4. Every `image:` has `@sha256:`, LiteLLM tag is after 1.82.8, PR shows the inspect output? Y/N
5. Chassis image installs from `uv.lock` with hash checking? Y/N
6. Only `127.0.0.1:8080` and `127.0.0.1:4000` published; no `network_mode: host`, no Docker socket? Y/N
7. Default `docker compose up` (fake) starts with an empty `.env` and no internet? Y/N
8. `turn_off_message_logging: true` set; no debug flags; adapter never logs the key or the header? Y/N
9. Healthchecks on LiteLLM and chassis with `service_healthy`, no manual step? Y/N
10. Master-key-as-chassis-key debt written in the PoC-1 notes with PoC-5 as the deadline? Y/N
