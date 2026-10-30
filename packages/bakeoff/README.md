# bakeoff

The PoC-6 benchmark kit. It checks that every framework works ("a super simple task per framework") and measures the engines. Plan: `docs/plans/2026-10-09-poc-06-bake-off.md`.

```bash
make bakeoff ARGS="smoke"                                   # one PASS / FAIL / SKIP line per engine x lane
make bakeoff ARGS="smoke --engine echo-python --lane sidecar"
make bakeoff ARGS="run --tasks smoke,simplifier,lookup --repeat 3 --out /tmp/bakeoff-run"
make bakeoff ARGS="run --target echo-python=http://127.0.0.1:8080 --model-log model.jsonl"
```

`make bakeoff` is `uv run python -m bakeoff`. Run `make ts-check` once to build `echo-typescript`, or its rows are SKIP.

## What `smoke` and `run` start

For each engine and lane, real processes on localhost, on random free ports:

```
fake model server   a uvicorn thread of the kit, with the engine's script; its recorded calls are read
  <- chassis serve  a generated config: profile local, model litellm (dummy key), fake tools on /mcp
  <- workload       workload-a2a serve --handle ..., or node dist/src/main.js; none in `inprocess`
```

The kit sends `POST /v1/run` to the chassis. The model is the fake model server (`packages/fake-model-server/scripts/bakeoff.yaml`; smolagents uses its own `tests/scripts/smolagents.yaml`, because a code agent needs code-format replies). With `--target` there is no stack: the kit measures chassis instances that already run, with whatever model they have (the Mac command points it at a hosted model).

`smoke` runs `simplify: Hello.` once and prints one line per engine x lane. It exits 1 on any `FAIL`.

```
PASS echo-python         inprocess
SKIP kagent-adk          remote      runs only on kind (poc06-kind.yml)
```

With `--model-url URL --model-key-env NAME [--route big-default|local-small]` (hosted mode) there is no fake model server: the chassis processes call the LiteLLM at `URL` with the key in the variable `NAME` (a LiteLLM key, never a provider key), on that route. This is how `make poc06-mac` runs the hosted and SLM numbers (`deploy/compose/README.md`, "PoC-6: the one Mac command"). Only trusted engines run this way: an untrusted `--engine` is refused with exit 2, and the default is every trusted engine in the `inprocess` and `sidecar` lanes. The Prompt bytes and Request body keys columns are `-`, because no fake model records the requests.

`run` writes `results.json` and `results.md` to `--out`. It exits 1 only when a stack does not start; a low pass rate is a result.

## The matrix and the trust rule

| Engine | Runs as | Trust | Lanes |
| ------ | ------- | ----- | ----- |
| echo-python, echo-pydanticai, echo-langgraph, echo-openai-agents | `<pkg>:handle` under `workload-a2a serve` | trusted | inprocess, sidecar, remote |
| echo-typescript | `node packages/workloads/echo-typescript/dist/src/main.js` | trusted | sidecar, remote |
| echo-smolagents | `workload-a2a serve`, the smolagents script | untrusted | remote |
| echo-claude-agent | `workload-a2a serve`; needs chassis `POST /v1/messages` | untrusted | remote |
| kagent-adk | kind only | untrusted | remote (always SKIP here) |

An `untrusted` engine runs only in the `remote` lane (PoC-5, ADR-001 item 5). The registry (`registry.py`) and the chassis config (`spec.trust`) both refuse anything else.

SKIP reasons: `echo-typescript` has no `dist/` (run `make ts-check`); `echo-claude-agent` while `chassis.server.model_proxy_messages` cannot be imported; `kagent-adk` always; `remote` when the host has no non-loopback IP (the remote proxy binds the pod IP).

Lanes: `inprocess` loads the handle in the chassis. `sidecar` reaches the workload on loopback. `remote` starts the workload with `--require-token-env` and the chassis with `connector: remote`, `spec.engine.auth.token_env`, and the remote proxy listener on the host IP; the workload sends the token on every model and tool call.

## Processes and environments

- Every child (chassis, workload, node) gets an environment built from scratch: `PATH`, a temp `HOME`, and only the variables it needs. It never copies `os.environ`. A name that starts with `ANTHROPIC_` or `CLAUDE_` is refused (`envs.py`).
- The model key is a dummy string. The token of the remote lane is random per cell and is never printed.
- Each child has its own process group. `atexit`, SIGTERM, and SIGHUP kill the groups. Temp dirs are removed.

## Columns of `results.md`

| Column | Meaning |
| ------ | ------- |
| Pass | Repetitions that passed, out of the total. A repetition runs one complete and one streaming `/v1/run`; both must pass the task's check |
| Tool calls | `lookup` only: repetitions where the two tools were called in order (`glossary_lookup{term: SLM}`, `acronym_expand{acronym: RAG}`) with arguments valid against each tool's schema |
| p50 ms, p95 ms | Latency of the complete `/v1/run`, wall time, linear interpolation. With few repetitions p95 is near the maximum |
| TTFT p50 ms | Time from the request to the first `delta` frame of the streaming `/v1/run` |
| In tok, Out tok | `metrics.input_tokens` and `output_tokens` of the response, mean over repetitions. The fake model's counts are scripted, so they show the shape, not the cost |
| Prompt bytes per call | One number per model call of the first repetition: the UTF-8 bytes of the `messages` JSON the model received, in order |
| Request body keys | The keys of the first model call's body. Through the chassis this is what the chassis forwards, not what the workload sent |
| Code lines | Mapping size per engine: source lines minus blanks, comments, and docstrings, counted as PoC-2's `measure.py` does, over the engine's own source files (`registry.py` lists them) |

Pass checks (plan): smoke is `end{status: ok}` after at least one `delta`; simplifier output holds `2026`, `Acme`, and `30`; lookup is the two tool calls above and an answer that holds `small language model` and `retrieval-augmented generation`.

`--target` runs the same tasks by URL, lane `target`. Prompt bytes and body keys are `-` unless `--model-log FILE` names a JSONL request log of the target's model: one JSON request body per line, or `{"body": {...}}`. The kit reads the lines a run appended.

## Tests

`scripts/check_offline.sh packages/bakeoff -q` runs the unit tests offline. `test_bakeoff_smoke_network.py` is marked `network`: it runs `bakeoff smoke` for `echo-python` in `inprocess` and `sidecar`.

## PoC-7: the eval tool trial

```bash
uv run python -m bakeoff.evaltrial --tool deepeval --out /tmp/evals     # or --tool promptfoo; --offline
uv run python -m bakeoff.golden.generate --check                        # generated files in sync
uv run python -m bakeoff.langfuse_datasets --report /tmp/evals/deepeval-echo-python.json
```

`evaltrial` starts the stack per engine (default `echo-python,echo-langgraph`, sidecar lane) with the golden model script and a scripted judge, runs the tool (configs in `evals/`), and writes `<tool>-<engine>.json` (`eval-report.v0`) and `<tool>-<engine>.raw.json`. Results and the pick: `pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-eval-tool-trial.md`.

## PoC-8: the eval gate and the red team in CI

```bash
make evals               # DeepEval on the 50 gate cases, every engine and lane, then the gate
make evals-regression    # the drop-facts business logic must fail the gate (exit 1)
make redteam             # the PoC-7 red-team set, every engine, lane, and guardrail stack
uv run python -m bakeoff.evalrun --engine echo-python --lane sidecar --logic drop-facts --out /tmp/e
uv run python -m bakeoff.evalgate --report /tmp/e --engine echo-python --lane sidecar
```

- Gate set: `golden/cases.yaml` (the PoC-7 20) plus `golden/cases-gate.yaml` (30 more): 40 labelled `pass`, 10 `fail`. The model scripts per logic and the judge are generated (`golden-gate*.yaml`, `generate --check`).
- `evalrun` writes `deepeval-<engine>-<lane>[-<logic>].json` (`eval-report.v0`, with `meta.lane` and `meta.logic`). Engines that read a script another way get it rewritten (`dialects.py`: Claude CLI tool-name prefix, smolagents code blocks).
- `evalgate` checks each report against `evals/gate/baseline.json`: the pass rate per engine and lane, the `must_pass` ids, and the `must_fail` ids (wrong answers on purpose: one that passes means a check stopped checking). Exit 0 pass, 1 regression or missing report, 2 bad input. `--record` rewrites the baseline from real reports.
- `redteam` starts each stack with `guardrails: litellm` (the LiteLLM stub and the pattern stand-in, on the fake model's base URL) or `guardrails: fake`, and `tools: mcp` against an MCP tool server in a thread. Stop rules and the baseline format: `chassis_contracts.redteam.live`. Baseline: `evals/redteam/baseline.json`.
- `evalcheck` scores the gate set offline (no engine, no eval tool) for the tests; it agrees with DeepEval on every PoC-7 case and assertion.
