# Live drill: stock SDK clients in the workload container, and the token and cost dashboard on router traffic (2026-11-13)

## Why

- 013 CH-2: "A stock OpenAI client and a stock Anthropic client in the workload container, pointed at the proxy, get streamed and complete answers."
- 006 G-6: "The PoC-8 stack loads every dashboard with no manual steps."; "The dashboard shows token spend and cost from live router traffic."; "A test call through the router shows on the dashboard within one minute."

## Setup

- Commit: `85b108a` (branch `test/compose-live-2-1113`), dirty: the drill files below, not yet committed.
- Machine: macOS 27.0.1, Apple M1 Pro, 32 GB. Docker 29.9.3 (linux/arm64), Docker VM memory 8319438848 bytes, 10 CPUs. Compose v5.7.0. uv 0.10.10, Python 3.12.13.
- Stack: `deploy/compose/poc08-litellm/docker-compose.yaml` plus the overlay `live-drill.yaml`, Compose project `live2x1113`, through `stack.sh` (now takes `POC08_LITELLM_PROJECT` and `POC08_LITELLM_OVERLAYS`).
  - The chassis in the `sidecar` lane (`chassis-drill.yaml`): model and guardrails `litellm`, telemetry `otel`, route `big-default`, `spec.observability.prices` for `big-default` (suggested: 3.0 in, 15.0 out USD per million tokens).
  - `workload`: the echo-python image plus the stock SDKs from uv.lock, openai 3.22.1 and anthropic 1.11.0 (`sdk-workload.Dockerfile`, image `agent-platform/echo-python-sdk:poc08-live`). It has `network_mode: service:chassis`, uid 10002, a read-only root, no capabilities, and only the four workload variables. `sdk_clients.py` is mounted read-only at `/opt/drill`.
  - LiteLLM `ghcr.io/berriai/litellm:v1.103.0@sha256:bd089afd...` (auth on, Postgres, Presidio and the guardrail server on every call), then the fake model server.
  - Prometheus `prom/prometheus:v3.5.0@sha256:63805ebb...`, which scrapes `chassis:8080/metrics` every 5 s (`prometheus-drill.yml`).
  - Grafana `grafana/grafana:12.1.0@sha256:6ac590e7...`, with the PoC-8 provisioning (`deploy/compose/poc08/grafana/provisioning`) and `deploy/dashboards/` mounted read-only.
- No real provider key. The keys are generated at run time into the git-ignored `.env.poc08-litellm` and `.run/`, and the run printed none of them.

## Command

```
POC08_LITELLM_PROJECT=live2x1113 POC08_LITELLM_OVERLAYS=live-drill.yaml deploy/compose/poc08-litellm/stack.sh up
POC08_LITELLM_PROJECT=live2x1113 POC08_LITELLM_OVERLAYS=live-drill.yaml deploy/compose/poc08-litellm/stack.sh wait
LIVE_DRILL=1 POC08_LITELLM_PROJECT=live2x1113 uv run --no-sync pytest -m network -v -s -p no:cacheprovider pocs/poc-08-cross-cutting-build/tests/test_poc08_live_drill.py
POC08_LITELLM_PROJECT=live2x1113 POC08_LITELLM_OVERLAYS=live-drill.yaml deploy/compose/poc08-litellm/stack.sh down
```

## Output

This is one run on a fresh stack. The header lines (platform, plugins) are trimmed, and the `sdk answers` line is cut at 400 characters. The anthropic half of that line held the same `control-seen-unchanged` values, which the passing anthropic tests assert. Exit code 0.

```
test_a_stock_client_in_the_workload_container_gets_a_complete_answer[openai] sdk answers: {"model_url": "http://127.0.0.1:8090/v1", "key_like_env": ["GPG_KEY"], "openai": {"sdk": "openai 3.22.1", "complete": "control-seen-unchanged", "streamed": ["control-seen-unchanged"]}, "anthropic": {"sdk": "anthropic 1.11.0", "complete": "control
PASSED
test_a_stock_client_in_the_workload_container_gets_a_complete_answer[anthropic] PASSED
test_a_stock_client_in_the_workload_container_gets_a_streamed_answer[openai] PASSED
test_a_stock_client_in_the_workload_container_gets_a_streamed_answer[anthropic] PASSED
test_the_workload_container_calls_the_proxy_on_loopback_and_holds_no_key PASSED
test_the_router_recorded_the_sdk_calls PASSED
test_grafana_loads_every_dashboard_file_with_no_manual_step PASSED
test_the_dashboard_shows_token_spend_and_cost_from_live_router_traffic runs: [(200, 'ok'), (200, 'ok'), (200, 'ok')]
router usage: {'records': 19, 'prompt': 70, 'completion': 35} chassis tokens: 105.0 panels: {'Model tokens by direction': 0.1933275563258232, 'Model cost (USD per hour)': 0.0004062608318890814}
PASSED
test_the_dashboard_counts_the_tokens_the_router_counted PASSED
test_a_test_call_through_the_router_shows_on_the_dashboard_within_one_minute the test call showed on the dashboard after 3.4 s
PASSED
============================= 10 passed in 27.00s ==============================
```

## Reading

- **CH-2.** Both stock SDK clients ran in the workload container, not in the chassis, and called the chassis's loopback proxy. Each got the fake model's reply to the control text (`control-seen-unchanged`), both complete and streamed. The fake sits only on the internal network, so that reply came through LiteLLM. LiteLLM's usage table held the four calls. The CH-2 box passes.
- **G-6.** Grafana loaded every file of `deploy/dashboards/` as provisioned, with no manual step. Run through Grafana, the token and cost panels of `chassis-echo` held numbers from the router traffic. The chassis's token counter (105) equals the token total in LiteLLM's `LiteLLM_SpendLogs` (70 + 35). A test call raised the raw counter and both panels within 3.4 s. The limit is 60 s. The three boxes pass.
- **Usage records.** There were 19 records for 8 model calls. The extra records are not model calls; the likeliest source is the guard stages' `apply_guardrail` calls. They hold no tokens, so the token totals are unaffected. This is not checked.

## Controls

- In an earlier run, on a stack that was not fresh, the key check failed on `GPG_KEY`. That variable is the python base image's public CPython release-signing fingerprint, so it is now named in `NOT_CREDENTIALS`. Every other key-like name still fails the test.
- The one-minute test first waits until Prometheus holds every token that LiteLLM recorded. It then needs the raw counter to move, so a value that was still in flight cannot pass for the test call. An earlier version without this wait "passed" in 0.2 s, and that result was not trusted.

## Not proven here

- 004 G-2: Prometheus scraping LiteLLM's own token and cost metrics, labeled by agent and route. Not attempted: LiteLLM's `prometheus` callback is off, and `/metrics/` answers 401.
- 006 G-6: "Spend can be filtered by agent, user, and route". Not run live.
- 013 CH-2: "Token counts from the proxy match the router's counts". The totals were equal in this run (105 = 105), but the test compares totals, not each call. That box stays open.
- 016 H-9: hot reload. Not attempted.
- No break-the-code mutation was run on the stack after the final pass.
