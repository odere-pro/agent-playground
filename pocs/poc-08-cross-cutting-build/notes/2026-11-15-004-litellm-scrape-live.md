# Prometheus scrapes LiteLLM, no user label (2026-11-15)

## Why

004 G-2, the Prometheus box: "Prometheus scrapes the token and cost metrics, labeled by agent and
route; `user` is in the usage record only." The 2026-11-13 router drill
(`2026-11-13-router-live.md`) left it open: LiteLLM v1.103.0 answered every `GET /metrics/` from
Prometheus with 401, so no `litellm_*` series existed and the test was `xfail(strict=True)`.

## What changed

- `router-drill.sh up` mints a second virtual key, `prometheus-scrape`, after the chassis key:
  `allowed_routes` `/metrics` and `/metrics/` only, model list `no-route`, `max_budget` 0. The
  master key goes to curl on stdin, as for every other key. The raw value goes to
  `.run/prometheus-scrape.key` (git-ignored; mode 0444 inside the mode-0700 `.run/`, so the
  container's `nobody` can read it and no other host user can). `logs` and `key-hits` mask it.
- `router-drill.yaml` mounts that file read-only into Prometheus. `router-drill.prometheus.yml`
  sends it as `authorization: {type: Bearer, credentials_file: ...}`. No key in any committed file.
- `router-drill.prometheus.yml` drops every label whose name holds `user` before storage
  (`metric_relabel_configs`, `labeldrop`, regex `.*user.*`).
- `router-drill.yaml` names its networks from `ROUTER_DRILL_PROJECT` (default `router1113`), and
  `router-drill.sh` exports it, so the drill runs under its own project.
- The test drops its `xfail`. It now also asserts that LiteLLM's Prometheus target is `up`, that a
  `litellm_*` series exists, that no series of job `litellm` has a label named for a user, and
  that no label holds the call's user value.

## Setup

- Commit: branch `test/004-litellm-scrape-live-1115` off `origin/main` 2b70aa6, dirty (this change).
- Machine: macOS, Docker Desktop 29.9.3 (10 CPUs, 8319438848 bytes), Docker Compose 5.7.0, uv
  Python 3.12.13. One stack on the Docker VM; no other project was up (`docker ps` empty).
- Compose project `r004x1115`: `docker-compose.yaml` with `router-drill.yaml`. Services: postgres,
  fake-model-server, litellm (v1.103.0), chassis, prometheus (v3.5.0). Image pins as in
  `2026-11-13-router-live.md`. Run suffix of the drill's keys: `72c92c`.
- No real provider key exists in this run.

## Command

```
env -u VIRTUAL_ENV ROUTER_DRILL_PROJECT=r004x1115 deploy/compose/poc08-litellm/router-drill.sh up
env -u VIRTUAL_ENV ROUTER_DRILL=1 ROUTER_DRILL_PROJECT=r004x1115 uv run pytest -m network -v -s \
    pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py > /private/tmp/claude-501/live-004-run.log 2>&1
env -u VIRTUAL_ENV ROUTER_DRILL_PROJECT=r004x1115 deploy/compose/poc08-litellm/router-drill.sh down
```

`up` ended with every container healthy and printed `minted prometheus-scrape into .../.run/prometheus-scrape.key`.
`down` removed the five containers, both networks, and `.run/`.

## Output

Exit code 0. Trimmed: the printed series lists (labels of the three token and spend metrics) and
the echo SSE stream. One series as printed (`litellm_input_tokens_metric_total`) has these
label names: `api_key_alias` (value `svc-alpha-72c92c`), `api_provider` (`openai`), the key hash
(value cut), `instance` (`litellm:4000`), `job` (`litellm`), `model` (`fake-model`), `model_id`
(value cut), `org_alias`, `org_id`, `team`, `team_alias` (all `None`), and `requested_model`
(`big-default`).

```
...::test_an_unknown_route_returns_a_clear_error_not_a_default_model PASSED
...::test_changing_the_model_behind_big_default_switches_the_model PASSED
...::test_no_key_in_the_router_config_or_the_logs PASSED
...::test_every_call_makes_one_usage_record PASSED
...::test_calls_with_no_user_tag_show_under_unknown PASSED
...::test_one_query_returns_totals_per_request_user_and_agent_for_a_date_range PASSED
...::test_prometheus_scrapes_token_and_cost_metrics_by_agent_and_route
{"litellm_series": 335, "user_labels": []}
PASSED
...::test_the_echo_agent_gets_a_streamed_answer_from_big_default_through_the_router PASSED
...::test_spend_goes_on_the_calling_key_even_when_metadata_names_another_agent PASSED
...::test_the_drill_runs_from_this_checkout PASSED
======================== 10 passed in 117.46s (0:01:57) ========================
```

## Reading

With the scrape key, LiteLLM's target is up and 335 `litellm_*` series are stored. The token and
spend series of the calling key carry the agent (`api_key_alias`, the key tagged `agent:<name>`)
and the route (`requested_model: big-default`). No stored LiteLLM series has a label named for a
user, and none holds the call's user value; the call's spend-log row has it. The box passes.

Limits:

- The run shows the stored series after `labeldrop`. It does not show which user labels LiteLLM
  emitted before the drop (the raw `/metrics/` was not read).
- `labeldrop` merges series that differ only by a user label. Prometheus then drops the
  duplicate samples of that scrape. In this drill each series had one user value, so no merge
  happened. suggested: before production, set LiteLLM's own label config so it never emits the
  label, and keep `labeldrop` as the second line.
- The agent label is the key alias, not the `agent:` tag itself. The tag is read in SQL
  (`usage_totals.sql`).
- Not yet reviewed by `platform-security` (a new key and a new mount; `deploy/CLAUDE.md`).

## Controls

- The 2026-11-13 run is the control for the credential: the same LiteLLM and Prometheus with no
  scrape key gave 401 and no `litellm_*` series.

## Rerun 2 (2026-11-15): the scrape key's limits

Why: the security review (RETRY) asked for proof that the scrape key can do nothing but scrape,
for a rerun-safe `up`, and for an exact `key-hits` value count.

Changes: new test `test_the_scrape_key_is_refused_every_route_but_metrics_and_spends_nothing`;
`up` keeps existing keys (LiteLLM refuses a second key with the same alias, so a second `up`
failed at `mint svc-echo` before any scrape-key step); the scrape key is written to a 0600
temp file, made 0444, then moved; `.key` values under 8 characters are skipped; the `key-hits`
test expects the exact count of values.

Command (project `r004z1115`, `up` run twice before the test; the second `up` minted nothing
and exited 0):

```
env -u VIRTUAL_ENV ROUTER_DRILL=1 ROUTER_DRILL_PROJECT=r004z1115 uv run pytest -m network -v -s \
    pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py > /private/tmp/claude-501/live-004-run2.log 2>&1
```

Output (exit code 1):

```
...::test_the_scrape_key_is_refused_every_route_but_metrics_and_spends_nothing {"control_metrics": 200, "refused": {"chat big-default": 403, "chat local-small": 403, "models": 429, "key info": 403}, "spend": [{"spend": "0"}]}
FAILED
E       AssertionError: {'chat big-default': 403, 'chat local-small': 403, 'models': 429, 'key info': 403}
=================== 1 failed, 10 passed in 292.41s (0:04:52) ===================
```

Reading: with the scrape key, `/metrics/` answers 200. Both chat routes and `/key/info` answer
403. `GET /v1/models` answers 429, not 401 or 403. The run printed only status codes, so it does
not show why: the route check may not stop `/v1/models`, and `max_budget` 0 or a rate limit may.
The key's spend is 0. The limits criterion fails as written. This run does not show that
`max_budget` 0 blocks anything. The Prometheus test passed again.

## Rerun 3 (2026-11-15): 429 accepted on `/v1/models` only

Decision (coordinator): accept 429 on `GET /v1/models` as a refusal, for that route only. Chat on
both routes and `/key/info` must still be 401 or 403. A first try on project `r004w1115` failed
on this test's own extra check, "no spend-log row": LiteLLM logs refused calls too (3 rows, each
0 tokens, spend 0). That check now requires every row of the key to have 0 tokens and spend 0.

Command (fresh project `r004v1115`, then `down`):

```
env -u VIRTUAL_ENV ROUTER_DRILL=1 ROUTER_DRILL_PROJECT=r004v1115 uv run pytest -m network -v -s \
    pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py > /private/tmp/claude-501/live-004-run3.log 2>&1
```

Output (exit code 0; the other tests' lines trimmed):

```
{"litellm_series": 335, "user_labels": []}
...::test_the_scrape_key_is_refused_every_route_but_metrics_and_spends_nothing {"control_metrics": 200, "refused": {"chat big-default": 403, "chat local-small": 403, "models": 429, "key info": 403}, "spend": [{"spend": "0"}]}
======================== 11 passed in 166.45s (0:02:46) ========================
```

Reading: the scrape key scrapes (200), is refused both chat routes and `/key/info` (403), and
spends nothing. The route rule is not shown to block `GET /v1/models`. Its 429 most likely comes
from `max_budget` 0, but that is not confirmed: the body was not read.

Exception to ADR-001 HR-1 (one LiteLLM key, `/metrics` routes only, no model, mounted read-only
into Prometheus only, in the router-drill Compose stack): sign-off is not recorded on this branch.
`deploy/CLAUDE.md` is unchanged.
