# Router drill: a real LiteLLM in front of the fake model, keyless (2026-11-13)

## Why

The open router boxes a keyless run can reach: 003 G-1b (unknown route, model swap), 002 G-1 (no
key in config or logs), 004 G-2 (usage records, `unknown` user, one totals query, Prometheus),
012 H-3 (echo streamed through the router, spend on the calling key). 013 CH-2 (stock clients in
the workload container) was not run: no budget left.

## Setup

- Commit: branch `test/router-live-run-1113` off `origin/main` a620823, dirty (this drill).
- Machine: macOS 27.0.1, Apple M1 Pro, Docker Desktop 29.9.3 (10 CPUs, 8319438848 bytes),
  Docker Compose v5.7.0, uv Python 3.12.13.
- Compose project `router1113`: `deploy/compose/poc08-litellm/docker-compose.yaml` with the
  overlay `router-drill.yaml`. Services: postgres, fake-model-server, litellm, chassis, prometheus.
- Images (pins from the base file and PoC-8):
  `ghcr.io/berriai/litellm:v1.103.0@sha256:bd089afdcd35b894b14a93f9743cdc8b591f82da1a38dd43a010a7b0c9de5fd7`,
  `postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94`,
  `docker.io/prom/prometheus:v3.5.0@sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996`,
  and the repo builds `agent-platform/fake-model-server:poc08-litellm`,
  `agent-platform/chassis:poc08-litellm` (image sha256:2381b21614c3...).
- Router config `router-drill.litellm.yaml`: `big-default` and `local-small` on the fake,
  suggested prices on `big-default` (3e-6 in, 1.5e-5 out per token), spend logs in Postgres,
  `callbacks: ["prometheus"]`. Chassis config `router-drill.chassis.yaml` (echo, `model: litellm`).
- Keys: `router-drill.sh up` makes the master key and the database password with `openssl rand`
  into `.run/router.env` (mode 600, git-ignored); the chassis key and the drill's keys
  (`svc-alpha-*`, `svc-beta-*`, `svc-gamma-*`) are minted through `/key/generate` into mode-600
  files. No real provider key exists in this run.

## Commands

```
deploy/compose/poc08-litellm/router-drill.sh up
ROUTER_DRILL=1 uv run pytest -m network -p no:cacheprovider -q -rA pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py
ROUTER_DRILL=1 uv run pytest -m network -p no:cacheprovider -q -rA -k "every_call or no_user or one_query or spend_goes" pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py
docker compose -p router1113 down -v
```

## Output

Run 1 (all tests, before the fixes below), exit 1, tail:

```
PASSED ...::test_an_unknown_route_returns_a_clear_error_not_a_default_model
PASSED ...::test_changing_the_model_behind_big_default_switches_the_model
PASSED ...::test_no_key_in_the_router_config_or_the_logs
PASSED ...::test_the_echo_agent_gets_a_streamed_answer_from_big_default_through_the_router
PASSED ...::test_the_drill_runs_from_this_checkout
FAILED ...::test_every_call_makes_one_usage_record
FAILED ...::test_calls_with_no_user_tag_show_under_unknown
FAILED ...::test_one_query_returns_totals_per_request_user_and_agent_for_a_date_range
FAILED ...::test_prometheus_scrapes_token_and_cost_metrics_by_agent_and_route
FAILED ...::test_spend_goes_on_the_calling_key_even_when_metadata_names_another_agent
5 failed, 5 passed in 353.61s (0:05:53)
```

The echo run's stream (trimmed to the frames):

```
event: delta     data: {"type":"delta","text":"answered-by-fake-model"}
event: metrics   data: {"input_tokens":10,"output_tokens":5,"cost_usd":null,"model_route":"big-default",...}
event: response  data: {...,"output":{"text":"answered-by-fake-model"},"status":"ok",...}
```

Fixes after run 1 (test and query bugs, not router behavior): the request's `user` is stored in
`LiteLLM_SpendLogs.end_user`, not `user` (that column is the key owner, `default_user_id` for the
master key); the route tests now use their own key (`svc-gamma-*`), so `svc-alpha-*` holds only
the three usage calls. Prometheus: LiteLLM answered every `GET /metrics/` from Prometheus with
`401 Unauthorized`, so no `litellm_*` series exists; the test is `xfail(strict=True)`.

Run 2 (usage tests only, fresh keys), exit 1, tail:

```
PASSED ...::test_every_call_makes_one_usage_record
PASSED ...::test_calls_with_no_user_tag_show_under_unknown
FAILED ...::test_one_query_returns_totals_per_request_user_and_agent_for_a_date_range
  assert agent["calls"] == "3"   ->  '10' == '3'
FAILED ...::test_spend_goes_on_the_calling_key_even_when_metadata_names_another_agent
  assert by[("agent", "beta")]["calls"] == "1"   ->  '2' == '1'
2 failed, 2 passed, 6 deselected in 13.97s
```

Both failures are the test's: the agent totals sum every key tagged `agent:alpha` (or `beta`),
and run 1's keys carry the same tags. In the spend test every key-level assert before the agent
total passed: beta's row carries beta's key hash and beta's key spend equals it, and alpha's key
spend and row count did not move.

Seen in the spend logs (no key values): the spoofed call's `request_tags` were
`["agent:alpha", "agent:beta", ...]`. LiteLLM merges request tags into key tags, so a per-agent
total by `request_tags` is spoofable; `usage_totals.sql` takes the agent from the key's
`metadata.tags` (join on `LiteLLM_VerificationToken`) instead. A call to an unknown route leaves a
spend-log row with an empty `model_group` and zero tokens.

## Final single run (the evidence for every tick)

After run 2 the test gives each run a unique agent tag and key aliases (`alpha-<run>`, and so
on), so totals count only this run's calls. The stack was brought up fresh, and the whole file ran once:

```
deploy/compose/poc08-litellm/router-drill.sh up
ROUTER_DRILL=1 uv run --directory <worktree> pytest -m network -p no:cacheprovider -v -rs pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py
```

Exit 0, tail:

```
...::test_an_unknown_route_returns_a_clear_error_not_a_default_model PASSED [ 10%]
...::test_changing_the_model_behind_big_default_switches_the_model PASSED [ 20%]
...::test_no_key_in_the_router_config_or_the_logs PASSED [ 30%]
...::test_every_call_makes_one_usage_record PASSED [ 40%]
...::test_calls_with_no_user_tag_show_under_unknown PASSED [ 50%]
...::test_one_query_returns_totals_per_request_user_and_agent_for_a_date_range PASSED [ 60%]
...::test_prometheus_scrapes_token_and_cost_metrics_by_agent_and_route XFAIL [ 70%]
...::test_the_echo_agent_gets_a_streamed_answer_from_big_default_through_the_router PASSED [ 80%]
...::test_spend_goes_on_the_calling_key_even_when_metadata_names_another_agent PASSED [ 90%]
...::test_the_drill_runs_from_this_checkout PASSED [100%]
=================== 9 passed, 1 xfailed in 127.78s (0:02:07) ===================
```

LiteLLM v1.103.0 findings: (1) the request's `user` lands in `LiteLLM_SpendLogs.end_user`; the
`user` column holds the key's owner. (2) LiteLLM appends the request's metadata tags to the key's tags in
`request_tags`, so the agent must come from the key (`usage_totals.sql` joins
`LiteLLM_VerificationToken`), never from `request_tags`. (3) LiteLLM answers Prometheus's
`GET /metrics/` with 401, so no `litellm_*` series exists; the Prometheus box stays open.

Ticked from this run: 003 unknown route, 003 swap, 004 one usage record, 004 `unknown`, 004 one
query for a date range, 012 echo streamed, 012 spend on the calling key. Open: 002 no key (keyless
half only), 004 Prometheus (401), 013 CH-2 (not run).

## Reading (runs 1 and 2; superseded by the final run above)

- 003 G-1b unknown route: passes (400, the error names `no-such-route`, no `choices`; control 200).
- 003 G-1b swap: passes (the same client call answered by `fake-model`, then `fake-model-fallback`
  after a router-config edit and a LiteLLM restart, then `fake-model` again).
- 004 G-2 one usage record per call, and `unknown`: pass (run 2).
- 012 H-3 echo streamed from `big-default` through the router: passes.
- Open: 004 one totals query (test needs a unique agent tag per run, then a rerun); 012 H-3
  spend on the calling key (same test fix, then a rerun); 004 Prometheus (LiteLLM v1.103.0
  `/metrics` answers 401: needs the metrics auth setting or a scrape credential, then a rerun);
  002 G-1 no key (only the keyless half: no literal key in the router config, zero log lines with
  any run key; a provider key was never present, so the logs half is not shown for one);
  013 CH-2 not run.

## Controls

The unknown route's control is the same body on `big-default`, answered 200. The swap's control is
the swap back, answered by the original deployment.
