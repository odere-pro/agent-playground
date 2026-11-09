# CH-16 Compose stack drills: the budget alert and the fallback route, 2026-11-09

Status: **not yet run, Docker daemon down on 2026-11-09 at 15:21 CET (14:21 UTC).** This note holds the runbook. Add a dated section with the output when the drills run. Do not edit this section.

## Why

[136 CH-16](../../../docs/planning/issues/136-CH-16-model-proxy-failures.md) ticked two boxes on offline evidence and left a stack drill for each. The first stack run is the evidence for both:

- The alert rule `ChassisModelBudgetExhausted` (`deploy/prometheus/rules/chassis.yaml`) on `chassis_model_budget_exhausted_total` fires in a fault test.
- A provider error is answered by LiteLLM's fallback route, with one chassis model call (no chassis retry, ADR-013).

## The drills

| Drill | Test id | Stack |
| ----- | ------- | ----- |
| Budget alert fires | `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_a_budget_refusal_raises_the_counter_and_fires_the_alert` | `deploy/compose/poc08` (chassis x3, fake model, Valkey, Prometheus, Grafana) |
| Fallback route, one chassis call | `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_a_provider_error_is_answered_by_the_fallback_route_with_one_chassis_call` | `deploy/compose/poc08-litellm` (LiteLLM, Postgres, Presidio, guardrail-server, OTel Collector, Jaeger, chassis, fake model) |

Both files are `network` and are collected only with `COMPOSE_STACK=1` (`pocs/poc-08-cross-cutting-build/tests/conftest.py`, `GATED_DRILLS`). `make test` never sees them.

## Setup

- Commit: `382aa4d` (main, the handover checkpoint of 2026-11-09). Dirty only in `HANDOVER.md` and a PoC-4 note, edited by another session; no file the drills read.
- Machine: macOS 27.0.1, Apple M1 Pro, arm64.
- Tools: Docker 29.9.2 (build a82e528), Docker Compose v5.6.1, uv 0.10.10, Python 3.12.13.
- Docker daemon: not running. `docker info` ends with:

```text
Server:
failed to connect to the docker API at unix:///Users/aleksandrderechei/.docker/run/docker.sock; check if the path is correct and if the daemon is running: dial unix /Users/aleksandrderechei/.docker/run/docker.sock: connect: no such file or directory
```

## What did run offline

Both compose files are valid. `stack.sh` writes the passwords and tokens at `up`; the check fills those variables with the placeholder `x`. The LiteLLM file reads `.run/chassis.env`, which `stack.sh up` mints, so its check skips env-file resolution.

```bash
POC08_RUN_TOKEN=x VALKEY_PASSWORD=x GRAFANA_ADMIN_PASSWORD=x \
  docker compose -f deploy/compose/poc08/docker-compose.yaml config --quiet
LITELLM_DB_PASSWORD=x GUARDRAIL_TOKEN=x LITELLM_MASTER_KEY=x \
  docker compose -f deploy/compose/poc08-litellm/docker-compose.yaml config --quiet --no-env-resolution
```

```text
poc08 exit=0
poc08-litellm exit=0
```

The LiteLLM check also prints three warnings that `secrets.guardrail_token` `uid`, `gid`, and `mode` are ignored outside Swarm mode. Without placeholders, both checks exit 1 on the `${VAR:?}` variables only.

Both test ids collect:

```bash
COMPOSE_STACK=1 uv run pytest --collect-only -q -p no:cacheprovider <the two ids above>
```

```text
pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_a_budget_refusal_raises_the_counter_and_fires_the_alert
pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_a_provider_error_is_answered_by_the_fallback_route_with_one_chassis_call

2 tests collected in 1.53s
```

CI has not run them either. `poc08-stack.yml` and `poc08-litellm.yml` are `workflow_dispatch` only. Their latest runs (2026-11-08, `9fc9a28`, `43d1d67`, `2cc70e0`, `4cfcfe7`) all predate the CH-16 commit `518c585` (#117): `git merge-base --is-ancestor 518c585 <sha>` is false for each.

## Runbook

Run from the repo root with Docker Desktop up. Each stack is brought down before the next.

### 1. The budget alert (metrics stack)

```bash
deploy/compose/poc08/stack.sh up && deploy/compose/poc08/stack.sh wait
export "$(deploy/compose/poc08/stack.sh env)"
COMPOSE_STACK=1 uv run pytest -m "network or stack" -v \
  "pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_a_budget_refusal_raises_the_counter_and_fires_the_alert" \
  2>&1 | tee /tmp/ch16-budget-alert.txt
echo "exit=${PIPESTATUS[0]}"
deploy/compose/poc08/stack.sh logs > /tmp/ch16-poc08-logs.txt   # redacted; on failure only
deploy/compose/poc08/stack.sh down
```

What passes: the run ends `status: error`; `chassis_model_budget_exhausted_total{route="big-default"}` reaches 1 within `POC08_SCRAPE_WAIT_S` (90 s); Prometheus shows `ChassisModelBudgetExhausted` for the `echo` agent as `firing` within `POC08_ALERT_WAIT_S` (180 s). The rule has `for: 1m`, so expect about a minute in `pending` first. The test uses the module's `drill` fixture, so the full drill load runs first; a run of the whole file is also fine.

### 2. The fallback route (LiteLLM stack)

```bash
deploy/compose/poc08-litellm/stack.sh up && deploy/compose/poc08-litellm/stack.sh wait
export "$(deploy/compose/poc08-litellm/stack.sh env)"
COMPOSE_STACK=1 uv run pytest -m "network or stack" -v -s \
  "pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_a_provider_error_is_answered_by_the_fallback_route_with_one_chassis_call" \
  2>&1 | tee /tmp/ch16-fallback.txt
echo "exit=${PIPESTATUS[0]}"
deploy/compose/poc08-litellm/stack.sh down
```

What passes: the fake answers `big-default`'s deployment with a 503 (`model_script.PROVIDER_FAULT_TEXT`); the answer is `model_script.FALLBACK_REPLY`, from `local-small` (`litellm.yaml` `router_settings.fallbacks`); `chassis_model_calls_total{route="big-default"}` rises by exactly 1. The artifact `fallback-route.json` lands in `POC08_LITELLM_OUT`.

Control for (2): the same stack's `test_plain_text_reaches_the_model_unchanged` shows the route answers when no fault is scripted. Run it in the same pytest call if the fallback test fails.

### Without a local daemon

Dispatch both workflows on a branch that contains `518c585`: `gh workflow run poc08-stack.yml --ref <branch>` and `gh workflow run poc08-litellm.yml --ref <branch>`. Link the runs here as the 2026-10-30 note does.

## Reading

Both drills have passed on CI (below). The fallback box of 136 CH-16 rests on run 42634113481, the "fires" box on run 42701220591, after the fix in this note. Neither drill has run on this Mac: Docker is down here.

## Run on CI, 2026-11-09

Both workflows were dispatched on `main` at `e6133ec` at 14:22 UTC (`workflow_dispatch`). `e6133ec` contains the CH-16 commit `518c585` (`git merge-base --is-ancestor 518c585 e6133ec` is true). They ran on hosted runners: Linux x86_64, Python 3.12.13, pytest 9.1.1. The lines below come from `gh run view <id> --log`, with the runner prefix and timestamps cut.

### The fallback route: passed

Run: [poc08-litellm 42634113481](https://github.com/odere-pro/agent-playground/actions/runs/42634113481), conclusion `success`.

```bash
COMPOSE_STACK=1 uv run pytest -m network -v -s \
  pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py \
  2>&1 | tee "$POC08_LITELLM_OUT/drill.txt"
```

```text
pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_plain_text_reaches_the_model_unchanged PASSED
...
pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_a_provider_error_is_answered_by_the_fallback_route_with_one_chassis_call PASSED
...
============================== 7 passed in 43.75s ==============================
```

Trimmed: the printed output of the other five tests, which all passed. The control `test_plain_text_reaches_the_model_unchanged` passed in the same run, on the same route.

### The budget alert: failed

Run: [poc08-stack 42634108264](https://github.com/odere-pro/agent-playground/actions/runs/42634108264), conclusion `failure`.

```bash
COMPOSE_STACK=1 uv run pytest -m network -v \
  pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py
```

```text
pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_a_budget_refusal_raises_the_counter_and_fires_the_alert FAILED [ 93%]
...
pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py:297:
...
E               AssertionError: timed out after 180 s waiting for ChassisModelBudgetExhausted firing

packages/poc-harness/src/poc_harness/metrics_stack.py:130: AssertionError
...
FAILED pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_a_budget_refusal_raises_the_counter_and_fires_the_alert - AssertionError: timed out after 180 s waiting for ChassisModelBudgetExhausted firing
=================== 1 failed, 15 passed in 195.28s (0:03:15) ===================
```

Trimmed: the 15 passing test lines and the source lines of the traceback.

What did pass in the refusal part: at `e6133ec`, line 297 of the test file is the second `wait_until`, the wait for the alert. So the run's `status == "error"` assert passed. So did the wait for `chassis_model_budget_exhausted_total{agent="echo", route="big-default"} >= 1` in Prometheus. The log shows no line for the refusal's own 429. The CI log step keeps the last 200 lines per service, and the chassis tail starts at 14:27:36, after the budget run. The one `"POST /v1/run HTTP/1.1" 429` in the tail (14:27:36.909) belongs to `test_a_burst_meets_the_rate_limit_kept_in_valkey`, not to this drill.

### Reading

- The fallback path holds. A provider error on `big-default` is answered by `local-small`, with one chassis model call. This run is the evidence for the fallback box of 136 CH-16.
- The budget refusal reaches the counter, and Prometheus scrapes it. `ChassisModelBudgetExhausted` did not reach `firing` within 180 s (`POC08_ALERT_WAIT_S`). The "fires" box stays open.
- Cause: the chassis created `chassis_model_budget_exhausted_total{route}` on the route's first refusal. Prometheus first saw the series at 1, and `increase(...[15m])` over a series that starts at 1 is 0, so the rule's expression never went above 0. The counter was right; the alert could not see the rise. A route's first refusal was invisible to the alert by construction, on every stack.

## The fix, 2026-11-09

`render_metrics` now writes every route `spec.model` declares (`route`, `routes`, `fallback_route`, `judge_route`) at 0 on each scrape, before the counters are read, so the first refusal is a rise from 0 to 1. The rule's expression and `for: 1m` are unchanged. Offline evidence: `packages/chassis/tests/test_metrics.py::test_the_budget_counter_is_zero_for_every_declared_route_before_a_refusal` and `packages/chassis/tests/test_alert_rules.py::test_the_budget_alert_has_a_zero_to_rise_from_on_every_declared_route`. Limit: a route the workload names that `spec.model` does not declare is not seeded, so its first refusal is still invisible (CH-16 review, LOW). A PromQL-only fix (`unless ... offset 15m`) was tried first and rejected: it fires a false critical after any data gap.

Other counters created on their first count, which blind the error-rate and fallback-rate rules the same way on a quiet agent, are listed in [117 X-3](../../../docs/planning/issues/117-X-3-slos-alerts.md), "What".

## Rerun on CI after the fix, 2026-11-09

`poc08-stack.yml` dispatched on `claude/ch16-compose-drills` at `4d4c8d8` (the fix) at 21:18 UTC. Same runner image, Python 3.12.13, pytest 9.1.1.

Run: [poc08-stack 42701220591](https://github.com/odere-pro/agent-playground/actions/runs/42701220591), conclusion `success`.

```bash
COMPOSE_STACK=1 uv run pytest -m network -v \
  pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py
```

```text
21:19:44 ...::test_the_dashboard_datasource_resolves_to_prometheus PASSED [ 87%]
21:20:53 ...::test_a_budget_refusal_raises_the_counter_and_fires_the_alert PASSED [ 93%]
21:20:55 ...::test_a_burst_meets_the_rate_limit_kept_in_valkey PASSED [100%]
======================== 16 passed in 78.21s (0:01:18) =========================
```

Trimmed: the 13 earlier test lines, all passed. The budget drill took 69 s from its start to `firing`: one scrape (15 s), a rule evaluation that sees the rise, `for: 1m`, and the next evaluation. That sits inside the 180 s wait with room for one missed scrape, which `test_poc08_stack_static.py` now checks from the rule file: `(scrape + 2 × eval + for) × 2 <= POC08_ALERT_WAIT_S`.

### Reading

- `ChassisModelBudgetExhausted` fires on a route's first refusal. This run is the evidence for the "fires" box of 136 CH-16.
- The two CH-16 drills both have a passing run. Line 35 of issue 136 cites this note.
- The litellm drill was not rerun: the fix touches the metrics route only, and its run on `e6133ec` stands.
