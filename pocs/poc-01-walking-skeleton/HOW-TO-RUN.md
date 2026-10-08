# PoC-1: how to run and test it locally

One request through the chassis and the router. What the pieces are and why they exist is in the README's [What we built](README.md#what-we-built), with the diagram. This file is only about running it. Three ways, from cheapest to fullest: the offline tests, one chassis process with the fake model, and the Docker Compose stack with LiteLLM.

## Install first

| Tool | Version used | Why |
| ---- | ------------ | --- |
| `uv` | 0.11 | Installs Python 3.12 and every package; `make setup` uses it. Install: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| GNU `make` | 3.81 or later | Every gate is a make target |
| `git` | any recent | The pre-commit hook runs `make quick` |
| `curl` | any | To send requests by hand |
| Docker with Compose v2 | Docker 29, Compose 5 | The Compose stack and the two `network` tests only. Docker Desktop on macOS is fine |

Python itself is not a prerequisite: `uv` installs 3.12 into `.venv`. No API key is needed for anything below except the last section.

```bash
make setup
```

This installs the workspace and the git hook. Run it once, and again after a dependency change.

## 1. Run the tests (no network, no keys)

```bash
make test-poc POC=01      # this iteration's scenario tests, one per exit criterion
make test                 # every test in the repo
make quick                # format, lint, import rules, and the tests of what you changed
make check                # what CI runs: quick plus mypy, planning check, harness lint
```

`make test` disables sockets and strips every `*_API_KEY` from the environment, so a test that reaches the network fails. Two tests need sockets and Docker and are skipped by every gate. Run them on purpose:

```bash
uv run pytest -m network -p no:socket -q
```

They start `chassis serve` as a real process, then bring up the Compose stack, send one request, and check the router's token log. About 30 seconds.

## 2. Run one chassis process with the fake model

```bash
uv run chassis serve --config packages/chassis/configs/fake.yaml
```

It listens on `127.0.0.1:8080`. The `fake` profile uses the scripted model (`ScriptedModel`) and the simplifier workload in the `inprocess` lane, so no other process is needed. In another shell:

```bash
curl -s 127.0.0.1:8080/ready
```

```bash
curl -s -X POST 127.0.0.1:8080/v1/run -H 'content-type: application/json' \
  -d '{"input": {"text": "simplify: the quick brown fox jumps over the lazy dog"}}'
```

```bash
curl -s -N -X POST 127.0.0.1:8080/v1/run -H 'content-type: application/json' \
  -d '{"input": {"text": "simplify: the quick brown fox"}, "stream": true}'
```

The complete call returns one `Response` JSON. The streamed call returns one server-sent event per chassis event (`start`, `delta`, `metrics`, `end`) and a final `response` event with the same `Response`. Both carry `versions` (chassis, config hash, prompt, model route) and token counts in `metrics`.

Note: in the `fake` profile the workload calls the chassis's own model proxy at `CHASSIS_MODEL_URL` (default `http://127.0.0.1:8080/v1`), so the port in the URL and `--port` must match if you change one.

## 3. Run the Compose stack (chassis, LiteLLM, fake model server)

```bash
cd deploy/compose && docker compose up -d --wait
```

No `.env` is needed for this variant. LiteLLM routes `big-default` and `local-small` both to the fake model server. The chassis is on `127.0.0.1:8080`, LiteLLM on `127.0.0.1:4000`. The same `curl` calls as above work. Token counts per call appear in the router log:

```bash
cd deploy/compose && docker compose logs litellm | grep token_log
```

The scripted demo does all of this, including the switch to `local-small`, and prints everything:

```bash
cd deploy/compose && ./demo.sh
```

Its recorded output is in [demo/](demo/). Stop the stack with `docker compose down -v` from `deploy/compose`.

## 4. Real models (optional, needs a key)

```bash
cp deploy/compose/.env.example deploy/compose/.env
```

Fill in `LITELLM_MASTER_KEY`, `LITELLM_API_KEY` (the same value in PoC-1, see the notes), the provider key, `BIG_DEFAULT_MODEL`, and the GGUF path for llama.cpp. Then:

```bash
cd deploy/compose && docker compose -f docker-compose.yaml -f docker-compose.local.yaml up -d --wait
```

`.env` is git-ignored. Never put a key anywhere else. This variant has not been run yet in this repo; report what you find in `notes/`.

## If something fails

- `make test` says a test needs a socket: it is marked wrong. Every gate test must run offline; mark it `network` or use the httpx ASGI transport.
- `address already in use` on 8080 or 4000: another chassis or LiteLLM is running. `lsof -i :8080` finds it; or pass `--port` and set `CHASSIS_MODEL_URL` to match.
- The Compose test skips: Docker is not on `PATH` or not running.
- `/ready` answers 503: the engine is not set up yet, or `spec.engine.handle` names a module the process cannot import. The chassis log says which.
- A 401 from LiteLLM in the `local` variant: `LITELLM_API_KEY` in `.env` does not match `LITELLM_MASTER_KEY`.
