# Eval tool trial: promptfoo and DeepEval on the golden set (2026-10-28)

Exit criterion 8: "One eval tool is picked for CI, and it produces the same report format for every engine." Work package W6. Test: `tests/test_poc07_evals_report_format.py`.

## Pick: DeepEval 4.3.2

The three strongest reasons:

1. **Vendor neutrality.** DeepEval is Apache-2.0 from an independent maintainer (Confident AI). promptfoo is MIT, but promptfoo joined OpenAI ([reuse analysis](../../../docs/planning/poc/010-reuse-analysis.md), link 186). The CI gate scores every engine and every model vendor, so it should not belong to one of them.
2. **One toolchain, a small install.** It runs from the pinned `uv run --no-project --with deepeval==4.3.2`, with no Node and no change to `uv.lock`: 69 packages, 118 MB. promptfoo needs Node 22.22 or later and `npx`: 620 packages, 743 MB of `node_modules`, and a 28 s cold start.
3. **The checks are Python code we own.** The deterministic checks (facts kept, banned words, tool calls in order) are three small `BaseMetric` classes, and `GEval` takes any OpenAI-compatible judge (`LocalModel`). The M-2 metrics (`facts_kept`, banned words) can then be one Python module, used by this gate and by the chassis's online evaluator gate. It runs under pytest, so JUnit comes from `--junitxml`.

What promptfoo does better, recorded so the pick can be revisited: it is faster (3.8 s against 7.1 s warm per engine), its `http` provider is a black box by config with no test code, and it writes JUnit itself. A TypeScript or remote agent fits both tools the same way, because both call only `POST /v1/run`.

## Setup

- Golden set: `packages/bakeoff/src/bakeoff/golden/cases.yaml`, 20 cases (12 simplifier, 8 lookup), each labelled `pass` or `fail` by hand: 14 pass, 6 fail. Loader and validation: `bakeoff.golden.load_cases`.
- Generated from it (`uv run python -m bakeoff.golden.generate --write`, kept in sync by `test_bakeoff_golden.py`): the engines' model script `packages/fake-model-server/scripts/golden.yaml`, the judge script `golden-judge.yaml`, and `evals/promptfoo/tests.yaml`.
- The scripted model answers every case with its reference answer. Six references are wrong on purpose: g09 drops a fact, g10 keeps a banned word, g11 changes a number, g12 adds a claim (only the rubric can catch it), g19 skips a tool call, g20 calls the wrong tool.
- The judge is a second fake model server. It returns a JSON verdict keyed on the answer text. It agrees with the human label on 19 of 20 cases. On g08 it fails a correct answer on purpose (judge error, so an agreement number has something to measure). An unknown answer gets a failing verdict.
- Each case gets four kinds of assertion in both tools: `contains` (facts or expected content, case-insensitive), `banned` (words that must not appear), `tools` (the `output.tool_calls` of the response envelope, names and arguments in order; none for simplifier cases), and `rubric` (promptfoo `llm-rubric`; DeepEval `GEval` with a 0-1 rubric).
- Configs take the engine only through the environment (`CHASSIS_URL`, `JUDGE_URL`, `EVAL_ENGINE`): `evals/promptfoo/promptfooconfig.yaml` (+ `transform.js`, `tool_calls.js`), `evals/deepeval/test_golden.py`.
- Launcher: `bakeoff.evaltrial`. Per engine it starts the bake-off stack (fake model server, chassis, workload, sidecar lane) and the judge, then runs the tool as a child process with an environment built from scratch.

## Commands

```bash
uv run python -m bakeoff.evaltrial --tool promptfoo --out "$OUT"             # echo-python, echo-langgraph
uv run python -m bakeoff.evaltrial --tool deepeval --out "$OUT"
uv run python -m bakeoff.evaltrial --tool promptfoo --engine echo-python --offline --out "$OUT2"
uv run python -m bakeoff.evaltrial --tool deepeval --engine echo-python --offline --out "$OUT2"
```

The tool commands the launcher runs (`bakeoff.evaltrial.tool_argv`):

```bash
npx -y promptfoo@0.125.1 eval -c evals/promptfoo/promptfooconfig.yaml -o raw.json --no-cache --no-write --no-table --no-progress-bar
uv run --no-project --python <venv python> --with deepeval==4.3.2 --with httpx --with pyyaml deepeval test run evals/deepeval/test_golden.py
```

## Numbers

One line per run, as `bakeoff.evaltrial` printed it. Wall time is the tool's child process only. The stack starts in 2.6 s on top of that. Each run sent 31 calls to the engine's model and 20 to the judge.

| Tool | Engine | Passed | Failed | Tool exit | Wall s (warm) | Wall s (first run) | Wall s (offline, warm) |
| ---- | ------ | ------ | ------ | --------- | ------------- | ------------------ | ---------------------- |
| promptfoo 0.125.1 | echo-python | 13 | 7 | 100 | 3.89 | 27.89 (npx fetch) | 3.58 |
| promptfoo 0.125.1 | echo-langgraph | 13 | 7 | 100 | 3.77 | - | - |
| DeepEval 4.3.2 | echo-python | 13 | 7 | 1 | 7.13 | 12.75 (wheels already in the uv cache) | 5.85 |
| DeepEval 4.3.2 | echo-langgraph | 13 | 7 | 1 | 7.18 | - | - |

- Both tools give the same verdict on every case and every assertion (`test_bakeoff_evalreport_tools_agree_case_by_case`). The 13 passes are the 14 cases labelled `pass` minus g08, where the judge disagrees. Both engines give the same verdicts.
- Exit codes: promptfoo exits 100 and DeepEval exits 1 when a case fails. A CI gate must read the report, not the exit code, to tell a regression from a broken run.

## Installs

| | promptfoo 0.125.1 | DeepEval 4.3.2 |
| - | ----------------- | -------------- |
| Runtime | Node 22.22 or later (`engines`) | Python 3.12 (here) |
| Install | `npm install promptfoo@0.125.1`: 620 packages, 743 MB, 28 s | `uv pip install deepeval==4.3.2`: 69 packages, 118 MB venv |
| In this repo | nothing in `uv.lock` or any `package.json`; `npx -y` | nothing in `uv.lock`; `uv run --no-project --with` |
| License | MIT | Apache-2.0 |
| Vendor | promptfoo, joined OpenAI | Confident AI (independent); the CLI promotes its cloud (`deepeval view`, "Use Confident AI") |

## Report formats

- promptfoo `-o raw.json`: top-level keys `evalId, results, config, shareableUrl, metadata, vars, runtimeOptions`; `results.results[]` has one row per test with `success`, `response` (with our `metadata`: tool calls, trace id), `gradingResult.componentResults[]` (one per assertion: `pass`, `score`, `reason`, `assertion`), and `results.stats`. About 5 KB per case. `-o` also takes `.csv`, `.yaml`, `.html`, and `junit.xml` (from `SUPPORTED_OUTPUT_FILE_FORMATS` in the installed dist; JUnit not run here).
- DeepEval `DEEPEVAL_RESULTS_FOLDER`: `test_run_<timestamp>.json` with `testFile, testCases, conversationalTestCases, metricsScores, testPassed, testFailed, runDuration, evaluationCost, official`; `testCases[]` has `name`, `success`, `actualOutput`, `toolsCalled`, `metricsData[]` (one per metric: `name`, `success`, `score`, `threshold`, `reason`), and `metadata`. About 2 KB per case. JUnit: pytest `--junitxml` (not run here).
- One schema across tools and engines: `bakeoff.evalreport.normalize` writes `eval-report.v0` (`schema, engine, tool, cases: [{id, pass, trace_id, assertions: [{kind, value, pass, reason}]}], summary`). The launcher adds `meta` (timings, exit code).
- Committed: `notes/evals/<tool>-<engine>.json` (eval-report.v0) and `<tool>-<engine>.raw.json` (the tool's own report). Each raw report is trimmed by `evalreport.trim_raw`, which drops promptfoo's `config.tests` and the rendered judge prompts, DeepEval's `verboseLogs`, and host paths.

## Offline and telemetry

- Both send usage telemetry by default. promptfoo: `PROMPTFOO_DISABLE_TELEMETRY=1` (and `PROMPTFOO_DISABLE_UPDATE`, `PROMPTFOO_DISABLE_SHARING`, `PROMPTFOO_DISABLE_REMOTE_GENERATION`; names from the installed dist). DeepEval: `DEEPEVAL_TELEMETRY_OPT_OUT=1` (`deepeval/telemetry/__init__.py`: "Opt out with `DEEPEVAL_TELEMETRY_OPT_OUT=1`"; field in `deepeval/config/settings.py`; the backend is PostHog in `deepeval/telemetry/client.py`). The launcher always sets all of them (`bakeoff.evaltrial.TELEMETRY_OFF`).
- `--offline` removes every proxy and CA variable from the tool's environment and sets `UV_OFFLINE=1` and `npm_config_offline=true`. Both tools finished, with the same results, from a warm cache.
- No hosted model and no key: the engines' model and the judge are both the fake model server on localhost, and the judge's `apiKey` is the literal `not-a-key`.

## Fit

- Black-box HTTP contract: both call only `POST /v1/run` and read the response envelope (`output.text`, `output.tool_calls`, `trace_id`), so a TypeScript or a remote engine fits unchanged. promptfoo does it by config plus 23 lines of JavaScript. DeepEval needs a 156-line test file: an httpx call and three deterministic metrics.
- CI: `.github/workflows/poc07-evals.yml` runs DeepEval on both engines against the fake model server, with no secret and `permissions: contents: read`, and uploads the reports. Each case's `trace_id` is in the report, so `bakeoff.langfuse_datasets` can link it to a Langfuse dataset run.

## Not verified

- No packet capture: the telemetry opt-outs are taken from the source, not watched on the wire. The offline run proves the tools work without the proxy, but not that they never try to connect.
- The DeepEval first run was not cold: its wheels were already in the uv cache from a scratch install. A truly cold install time was not measured.
- JUnit output was not produced by either tool.
- Langfuse datasets: `bakeoff.langfuse_datasets` was tested against an ASGI stub only, not a live Langfuse v4 (`deploy/compose/poc07` has not been run).
