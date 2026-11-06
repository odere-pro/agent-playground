# eval-kit

The eval code of the PoC track, on the bake-off stack: the PoC-7 eval tool trial, the PoC-8 eval
gate, and the PoC-8 red-team run. It starts stacks with `bakeoff.stack.running_stack` and reads
the engines from `bakeoff.registry`. `bakeoff` never imports it (R6-14,
`docs/reviews/2026-10-29-core-review/findings-R6-bakeoff.md`).

| Module | What |
| ------ | ---- |
| `golden/` | the golden set: `cases.yaml` (PoC-7, 20 cases), `cases-gate.yaml` (30 more), `generate.py`, `logic.py` |
| `evalreport.py` | `eval-report.v0`: each tool's report, normalized |
| `evaltrial.py` | PoC-7: promptfoo or DeepEval through the chassis, per engine |
| `langfuse_datasets.py` | PoC-7: the golden set and the reports as Langfuse datasets and runs |
| `evalrun.py`, `evalgate.py` | PoC-8: DeepEval on the 50 gate cases, then the gate against the baseline |
| `evalcheck.py` | PoC-8: the offline reference scorer, for tests |
| `dialects.py` | a fake-model script rewritten for the engines that read it another way |
| `redteam.py` | PoC-8: the PoC-7 red-team set on both guardrail stacks |

The old `python -m bakeoff.<module>` commands still run (`packages/bakeoff/README.md`, "Evals").

## PoC-7: the eval tool trial

```bash
uv run python -m eval_kit.evaltrial --tool deepeval --out /tmp/evals     # or --tool promptfoo; --offline
uv run python -m eval_kit.golden.generate --check                        # generated files in sync
uv run python -m eval_kit.langfuse_datasets --report /tmp/evals/deepeval-echo-python.json
```

Each run first installs the tool into its temp dir from a hashed lock: DeepEval from `evals/deepeval/requirements.txt` (`--require-hashes --no-deps`), promptfoo with `npm ci` from `evals/promptfoo/package-lock.json`. `evaltrial` starts the stack per engine (default `echo-python,echo-langgraph`, sidecar lane) with the golden model script and a scripted judge, runs the tool (configs in `evals/`), and writes `<tool>-<engine>.json` (`eval-report.v0`) and `<tool>-<engine>.raw.json`. Results and the pick: `pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-eval-tool-trial.md`.

## PoC-8: the eval gate and the red team in CI

```bash
make evals               # DeepEval on the 50 gate cases, every engine and lane, then the gate
make evals-regression    # the drop-facts business logic must fail the gate (exit 1)
make redteam             # the PoC-7 red-team set, every engine, lane, and guardrail stack
uv run python -m eval_kit.evalrun --engine echo-python --lane sidecar --logic drop-facts --out /tmp/e
uv run python -m eval_kit.evalgate --report /tmp/e --engine echo-python --lane sidecar
```

- Gate set: `golden/cases.yaml` (the PoC-7 20) plus `golden/cases-gate.yaml` (30 more): 40 labelled `pass`, 10 `fail`. The model scripts per logic and the judge are generated (`golden-gate*.yaml`, `generate --check`).
- `evalrun` writes `deepeval-<engine>-<lane>[-<logic>].json` (`eval-report.v0`, with `meta.lane` and `meta.logic`). Engines that read a script another way get it rewritten (`dialects.py`: Claude CLI tool-name prefix, smolagents code blocks).
- `evalgate` checks each report against `evals/gate/baseline.json`: the pass rate per engine and lane, the `must_pass` ids, and the `must_fail` ids (wrong answers on purpose: one that passes means a check stopped checking). Exit 0 pass, 1 regression or missing report, 2 bad input. `--record` rewrites the baseline from real reports.
- `redteam` starts each stack with `guardrails: litellm` (the LiteLLM stub and the pattern stand-in, on the fake model's base URL) or `guardrails: fake`, and `tools: mcp` against an MCP tool server in a thread. Stop rules and the baseline format: `chassis_contracts.redteam.live`. Baseline: `evals/redteam/baseline.json`.
- `evalcheck` scores the gate set offline (no engine, no eval tool) for the tests; it agrees with DeepEval on every PoC-7 case and assertion.

## Tests

`scripts/check_offline.sh packages/eval-kit -q` runs the unit tests offline. `test_eval_kit_redteam_network.py` is marked `network`: it runs `python -m eval_kit.redteam` on real localhost processes.
