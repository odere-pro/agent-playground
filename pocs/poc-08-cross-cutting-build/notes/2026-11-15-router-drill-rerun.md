# Router drill rerun on main, after the 012 route rule (2026-11-15)

## Why

PR 195 (`2026-11-13-router-live.md`) first ran the router drill. Since then main changed the
code the drill reaches:

- 012 H-3, PR 198 (a6d27bb): admission lets a run's model call through only on its own route.
- 012 H-3, PR 208 (880bb0b): route labels are bounded, the model is capped, plain-a2a has one route.
- 004 G-2, PR 214 (c19947b): Prometheus scrapes LiteLLM with a `/metrics`-only virtual key; the
  Prometheus test is no longer `xfail`, and a new test checks the scrape key.
- Also in the drill's path: PR 206 (407144b, `router-drill.sh` exports `TRACE_SAMPLE_PERCENT`),
  PR 211 (033c4d3, cancel in every lane).

This run checks that the drill still passes on main with those changes.

## Setup

- Commit: branch `test/router-drill-rerun-1115` off `origin/main` c19947b, clean before this note.
- Machine: the user's Mac (8 GB Docker), Docker server 29.9.3, uv Python 3.12.13. One stack only:
  `docker ps` was empty before `up`.
- Compose project `router1115` (new, fresh volumes): `docker-compose.yaml` with `router-drill.yaml`.
  `TRACE_SAMPLE_PERCENT` came from `router-drill.sh` (default rate 1.0).
- Images: `ghcr.io/berriai/litellm:v1.103.0`, `postgres:17.6-alpine`, `prom/prometheus:v3.5.0`
  (digest pins as in the base file), and the repo builds
  `agent-platform/chassis:poc08-litellm` (image 063d129ac934) and
  `agent-platform/fake-model-server:poc08-litellm` (image 6580f025f246).
- Keys: made by `router-drill.sh up` (`openssl rand`, `/key/generate`) into mode-600 files under
  `.run/`. No real provider key. No key value is in this note or the log.

## Commands

```
env -u VIRTUAL_ENV ROUTER_DRILL_PROJECT=router1115 deploy/compose/poc08-litellm/router-drill.sh up
env -u VIRTUAL_ENV ROUTER_DRILL_PROJECT=router1115 ROUTER_DRILL=1 uv run --directory <worktree> \
    pytest -m network -p no:cacheprovider -v -rs \
    pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py
env -u VIRTUAL_ENV ROUTER_DRILL_PROJECT=router1115 deploy/compose/poc08-litellm/router-drill.sh down
```

## Output

One full run, exit 0, tail:

```
...::test_an_unknown_route_returns_a_clear_error_not_a_default_model PASSED [  9%]
...::test_changing_the_model_behind_big_default_switches_the_model PASSED [ 18%]
...::test_no_key_in_the_router_config_or_the_logs PASSED [ 27%]
...::test_every_call_makes_one_usage_record PASSED [ 36%]
...::test_calls_with_no_user_tag_show_under_unknown PASSED [ 45%]
...::test_one_query_returns_totals_per_request_user_and_agent_for_a_date_range PASSED [ 54%]
...::test_prometheus_scrapes_token_and_cost_metrics_by_agent_and_route PASSED [ 63%]
...::test_the_scrape_key_is_refused_every_route_but_metrics_and_spends_nothing PASSED [ 72%]
...::test_the_echo_agent_gets_a_streamed_answer_from_big_default_through_the_router PASSED [ 81%]
...::test_spend_goes_on_the_calling_key_even_when_metadata_names_another_agent PASSED [ 90%]
...::test_the_drill_runs_from_this_checkout PASSED [100%]
============================= 11 passed in 49.52s ==============================
```

`router-drill.sh down` removed the containers, the networks, the volumes, and `.run/`.

## Reading

- No failure, so no stale test and no regression. The stricter 012 route rule (PRs 198, 208)
  still lets the echo agent's call on `big-default` through, and spend still lands on the
  calling key.
- Against PR 195's final run (9 passed, 1 xfailed, 10 tests): the Prometheus test now passes
  instead of `xfail`, and the scrape-key test is new. Both came with PR 214.

## Boxes

Nothing newly ticked. The boxes PR 195 ticked (003 unknown route and swap; 004 one usage record,
`unknown`, one totals query; 012 echo streamed and spend on the calling key) stay as they are,
and PR 214 already ticked 004 Prometheus. Still open: 002 G-1 no key (keyless half only),
004 "counted under the agent of its key" (not ticked by PR 195; this run adds no new proof),
013 CH-2.
