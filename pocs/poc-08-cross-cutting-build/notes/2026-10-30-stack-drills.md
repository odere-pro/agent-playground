# PoC-8 stack drills: stage latency, LiteLLM, and Presidio

Date: 2026-10-30. Runs on GitHub, on the PR 39 head `db13d6a` (tested as its merge ref `9668af3`). A hosted runner: 4 CPUs, Linux x86_64, Python 3.12.3. No key and no hosted model.

## Stage latency

Run: [poc08-stack 41011333372](https://github.com/odere-pro/agent-playground/actions/runs/41011333372), artifact `latency.json`. All 4 stack drill workflows passed on this commit.

```bash
deploy/compose/poc08/stack.sh up
deploy/compose/poc08/stack.sh wait
uv run python pocs/poc-08-cross-cutting-build/tests/poc08_stack_latency.py
```

Setup: `POST /v1/run`, `inprocess` lane, `echo_python:handle`, the fake model server. 200 requests per target after 10 warmup, concurrency 1, one request per target in turn.

| Target | Config | p50 ms | p95 ms | Errors |
| ------ | ------ | ------ | ------ | ------ |
| PoC-8 stages on | `chassis/stages.yaml` | 45.53 | 64.69 | 0 |
| PoC-8 stages off | `chassis/plain.yaml` | 44.52 | 63.87 | 0 |
| Overhead | | 1.01 | 0.82 | |

suggested: the budget is 25 ms at p50 and 50 ms at p95; the run is within it. The request counts and the concurrency are suggested too. This is one run on a shared runner with a fake model, so read it as "the stages add about 1 ms here", not as a production number.

## LiteLLM and Presidio

Run: [poc08-litellm 41011333394](https://github.com/odere-pro/agent-playground/actions/runs/41011333394), artifacts `drill.txt`, `pii.json`, `redteam.json`, `trace-*.json`, `digests.txt`. Also green on `main` at `bc67179`: [41011346881](https://github.com/odere-pro/agent-playground/actions/runs/41011346881).

```bash
deploy/compose/poc08-litellm/stack.sh up
deploy/compose/poc08-litellm/stack.sh wait
POC08_LITELLM=1 uv run pytest pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py
```

Stack: LiteLLM v1.103.0 with auth on, its Presidio hook, guardrail-server with the pattern stand-in, the OTel Collector, Jaeger.

- 4 of 4 drill tests passed: one run is one trace with a `litellm` span under the model proxy, PII reaches the model masked, plain text reaches it unchanged, and the PII and secret red-team cases report their numbers.
- Red team on this stack: 5 of 5 cases stopped (`exfil-02`, `exfil-04`, `secret-01`, `secret-03`, `secret-04`). This is the PII and secret subset, not the full 18. Its stand-in numbers are never Prompt Guard 2 numbers.

Found by the first run: LiteLLM keeps `/guardrails/apply_guardrail` for admins unless the key's `allowed_routes` names it. The chassis's guard stages got 401 and failed closed with `guardrail_unavailable`. `stack.sh` now mints the key with `openai_routes` plus that route. Any deployment with auth on needs the same.

The six images that were on tags are now pinned to the digests these runs printed.
