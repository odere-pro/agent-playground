# Findings R7: cross-cutting decisions

Session: R7. Area: `packages/guardrail-server/`, `packages/fake-langfuse/`, chassis `adapters/langfuse/`, `adapters/otel/`, `adapters/litellm/`, `adapters/evaluators.py`, `server/guard.py`, `server/marking.py`, `server/evaluator_gate.py`, `server/feedback.py`, `evals/`, `deploy/compose/poc07/`, `deploy/agent-scan/`, `.github/workflows/poc07-*.yml`, ADR-007 to ADR-010. PoC: `pocs/poc-07-cross-cutting-decisions` (open).
Rubric: [rubric.md](rubric.md).

## Summary

PoC-7 adds the four concerns at the chassis and LiteLLM, as planned: guardrail stages, OTel export with redaction, a feedback route, and an evaluator gate. Each has a fake and a contract suite. The top risks are in the security path. One injection regex is quadratic and can stall the guardrail server (measured below). A streamed answer skips the chassis output check. Exception text leaves in spans without redaction, and the Collector's traces pipeline has no redaction step either. The Compose stack pins no image by digest. Tests mostly cover the happy path of each concern, and several docs point at a CI run that cannot produce the evidence they wait on.

PoC-7 is open. Findings about an open exit criterion or its evidence are `deferred` and go to its plan, [2026-10-28-poc-07-cross-cutting.md](../../plans/2026-10-28-poc-07-cross-cutting.md). Already filed elsewhere and not repeated here: R0-08 (agent-scan workflow token), R1-01 and R1-07 (`chassis/redaction.py`), R1-20 (`test_contracts_poc07.py`), R2-10, R2-17, R2-21 (feedback and marking), R0-02 and R3-03 (shared harness and `sys.path`), and R0's template finding on `Builds on:`. LOW lens findings that did not fit the cap are in the shared notes folder as `R7-dropped-low.md`.

## Findings

Most severe first. At most 30. Severity: CRITICAL, HIGH, MEDIUM, LOW.
Lens: simple, secure, maintainable, docs, tests. Add `[shared]` when the fix touches
`Makefile`, `pyproject.toml`, `uv.lock`, CI, the root `CLAUDE.md`, or a PoC harness or conftest.

### R7-01 [HIGH] [secure] packages/guardrail-server/src/guardrail_server/classifier.py:57
Problem: The pattern `<\s*/?\s*(system|im_start|im_end)\s*>` has two adjacent `\s*` groups, so `<` followed by spaces is quadratic. Measured: 5,000 spaces take 0.57 s, and 10,000 spaces take 2.88 s.
Why it matters: One request holds the GIL and stalls the server. A 1 MiB chassis input reaches it, and past the 10 s timeout the chassis sees `guardrail_unavailable`, which is a bypass under `on_unavailable: fail_open`.
Fix: Write it as `<[\s/]*(system|im_start|im_end)\s*>` and add a regression test. Keeps behavior: yes.
Spec: "given `<` followed by 20,000 spaces, when `PatternClassifier.score` runs, then it returns in under 50 ms."
Status: open

### R7-02 [HIGH] [secure] packages/chassis/src/chassis/adapters/otel/__init__.py:229
Problem: `otel_span.record_exception(exc)` exports `str(exc)` and the stack trace as a span event without `redact`, while `_attribute` (line 127) redacts every other value. No test covers it, because `test_otel_telemetry.py:222` raises `RuntimeError("boom")`.
Why it matters: Exception text often carries prompt text, emails, or URLs. It reaches Langfuse and Jaeger raw, because the Collector does not redact traces either (R7-10).
Fix: Add the event with `exception.type` and `redact(str(exc))` instead of calling `record_exception`, and test it with an email in the message. Keeps behavior: yes (span name and status are unchanged).
Spec: "given a span body that raises `ValueError('mail jane@example.com')`, when the span ends, then no exported event or attribute holds the email."
Status: open

### R7-03 [HIGH] [secure] deploy/compose/poc07/docker-compose.yaml:56
Problem: None of the eight images is pinned by digest. `langfuse-worker:4` (:56), `langfuse:4` (:61), `redis:7` (:105), and `postgres:17` (:114) are moving major tags, and `cgr.dev/chainguard/minio` (:92) has no tag. `stack.sh digests` (stack.sh:90) prints digests, but nothing checks them.
Why it matters: `deploy/CLAUDE.md` and `deploy/compose/SECURITY.md:30` require `name:tag@sha256:<digest>`, and every other compose file follows that. The Collector in this stack holds `LANGFUSE_AUTH`.
Fix: Pin all eight from the `stack.sh digests` output, and add an `@sha256:` check to `test_poc07_stack_static.py`. Keeps behavior: yes.
Spec: "given the poc07 compose file, when every `image:` is read, then each matches `^[^@]+:[^@]+@sha256:[0-9a-f]{64}$`."
Status: open

### R7-04 [HIGH] [secure] packages/chassis/src/chassis/server/pipeline.py:370
Problem: The `output` guard stage runs only `if not request.stream`, and the client sets `stream`. A streamed answer is never checked for secrets or PII by the chassis.
Why it matters: Any caller can send `stream: true` to skip `secret-guard` and `pii-mask` on the answer. LiteLLM's `post_call` guardrails do not see text from the workload or its tools.
Fix: Add a buffered or tail-window check on `delta` frames, or refuse `stream` while `guardrails.output` is on. Keeps behavior: no (proposal: changes what a streamed client receives).
Spec: "given `spec.guardrails.output` is on and the engine streams a delta holding `sk-` and 20 characters, when `/v1/run` has `stream: true`, then the secret does not reach the client."
Status: proposal (also note for the PoC-7 plan: criterion 5 claims each concern works, and the gap is not stated there)

### R7-05 [HIGH] [tests] packages/chassis/tests/test_guard_stages.py:182
Problem: `test_a_stream_is_not_held_back_by_the_output_stage` asserts that `jane@example.com` reaches the client, so it reads as a spec for the leak in R7-04. The outage tests (:189, :200) cover only the input stage, never assert `retryable`, and no test asserts `GUARDRAIL_UNAVAILABLE`.
Why it matters: The fail-open and fail-closed branches in `guard._check` serve three stages, and only one is tested.
Fix: Rename the stream test as a known gap with `xfail(strict=True)` against the intended behavior, and parametrize both outage tests over `input`, `output`, and `tool_output` with a `retryable is True` assert. Keeps behavior: yes.
Spec: "given the guardrail is down and `on_unavailable` is `fail_closed`, when any stage runs, then the error is `guardrail_unavailable` and retryable."
Status: open

### R7-06 [HIGH] [tests] packages/guardrail-server/tests/test_guardrail_server.py:60
Problem: `test_secret_is_blocked` covers only the `sk-` shape. `aws_access_key`, `private_key`, `key_assignment`, and `bearer` in `secret_scan.SECRET_PATTERNS` have no test anywhere.
Why it matters: A regex edit can drop a credential shape with no failing test, and this is the only secret check behind LiteLLM.
Fix: Parametrize over one sample per pattern, plus a clean negative such as `TOKEN_COUNT = 12345`, with `# pragma: allowlist secret` on the samples. Keeps behavior: yes.
Spec: "given text holding an AWS key, a private-key header, `X_API_KEY=...`, or a Bearer token, when `secrets` is checked, then it is blocked with the kind and without the value."
Status: open

### R7-07 [HIGH] [simple] [shared] pocs/poc-07-cross-cutting-decisions/demo/demo.py:79
Problem: `_trace_and_feedback` is 143 lines, and its setup copies `run_once` in `tests/test_poc07_obs_trace_join.py:123`. The demo also imports helpers from test modules (`workload_on_uds` at :102, `gated_run` and `routes` at :267), which resolve only through the `sys.path` setup.
Why it matters: The demo breaks when a test is renamed, importing a `test_*` module can collect test code, and the function is almost three times the 50-line limit.
Fix: Move `workload_on_uds`, `run_once`, `gated_run`, and `routes` into the `poc07_*_harness.py` files, import them from there, and split the function. Keeps behavior: yes. Do not change the dated demo record.
Spec: "given `test_poc07_obs_trace_join.py` is renamed, when the demo runs, then it still imports its helpers, and no demo function is over 50 lines."
Status: open

### R7-08 [HIGH] [docs] docs/planning/adr/008-security-identity-guardrails-defense-in-depth.md:87
Problem: Item 4 says `guardrail-server` splits text with `split_for_classifier` into 384-word pieces with 32 of overlap. The server uses its own `WINDOW = 512` and `OVERLAP = 64` (`classifier.py:32-34`), and may not import `chassis`.
Why it matters: The ADR is the decision of record, and it gives the wrong size and the wrong component for the hard control.
Fix: Say the server cuts text into 512-token windows with 64 of overlap, and that `split_for_classifier` (384/32) is used only by the chassis fake. Keeps behavior: yes.
Spec: "given ADR-008 item 4, when a reader compares it with `classifier.py` and the guardrail-server README, then all three name the same window, overlap, and component."
Status: open

### R7-09 [HIGH] [docs] pocs/poc-07-cross-cutting-decisions/README.md:18
Problem: The README says LiteLLM's `presidio` hook waits on the `poc07-stack.yml` run, but line 13 and ADR-008:14 say that stack has no LiteLLM. Lines 16 and 29, ADR-010:71, `notes/backlog-changes.md:34`, and three dated notes also wait on a `poc07-stack` run that already passed (run 40735435727) without that evidence.
Why it matters: Open items point at a run that cannot produce their evidence, so nobody owns the rest of the work.
Fix: Name one concrete pending check for each item (a new stack test, the Mac run, or PoC-8), and add an update line under each stale note. Keeps behavior: yes.
Spec: "given a 'waits on' line in the PoC-7 docs, when it names a CI workflow, then that workflow has a test that produces the evidence and has not already run without it."
Status: deferred (PoC-7 open: note for its plan)

### R7-10 [MEDIUM] [secure] deploy/compose/poc07/otel-collector.yaml:60
Problem: The traces pipeline runs only `batch`, while the logs pipeline runs `transform/redact` (:30-36, :64).
Why it matters: Span attributes and events, including workload spans the chassis never sees, go to Langfuse and to Jaeger's unauthenticated UI without the Collector redaction that the file's own comment (:13-15) promises.
Fix: Add a `transform/redact` with `trace_statements` for span attributes and span events, ahead of `batch`, with the same patterns. Keeps behavior: yes (both backends still get identical spans).
Spec: "given a span with attribute `note=a@b.com` sent to :4318, when it reaches Jaeger, then the attribute is masked."
Status: open

### R7-11 [MEDIUM] [secure] deploy/agent-scan/scan.sh:53
Problem: Both `docker run` calls mount the whole checkout read-only (:40, :53), git-ignored files included, such as `deploy/compose/poc07/.env.poc07`. The second run has bridge egress and runs the repo's MCP server code. Line 8 says no env file goes in.
Why it matters: Server code on a branch can read generated platform credentials from `/repo` and send them out during the scan. This is separate from R0-08.
Fix: Mount only `packages/fake-mcp-server/src`, `packages/code-runner/src`, and `deploy/agent-scan` at the same paths under `/repo`. Keeps behavior: yes.
Spec: "given the two `docker run` argv lists in scan.sh, when their `-v` sources are read, then none is the repo root."
Status: open

### R7-12 [MEDIUM] [secure] packages/guardrail-server/src/guardrail_server/app.py:111
Problem: The server has no limit on text count or size, and a classifier exception becomes a bare 500. LiteLLM's `unreachable_fallback` is never set in `litellm/config.yaml`.
Why it matters: A multi-megabyte text blocks the server (see R7-01), and an unset fallback may turn a classifier fault into a pass.
Fix: Cap `texts` by item count and total characters with a 413, answer a fixed 503 on a classifier error, and set fail-closed explicitly in `config.yaml`. Keeps behavior: yes.
Spec: "given a 5 MB text, when it is posted to either route, then the answer is 413 in under 100 ms, and given `score` raises, then the answer is 503 with a fixed body."
Status: open

### R7-13 [MEDIUM] [secure] packages/guardrail-server/src/guardrail_server/app.py:104
Problem: The generic route scans only `texts`. It ignores `tool_calls` and `structured_messages`, which its docstring lists, and it accepts `checks: []`, which runs nothing.
Why it matters: A secret in `tool_calls[].function.arguments` returns `{"action": "NONE"}`, and a bad config silently turns the guardrail off.
Fix: Scan the strings in `tool_calls` and `structured_messages`, and reject an empty `checks` with 422. Keeps behavior: no (proposal: more requests are blocked or refused).
Spec: "given `texts: []` and `tool_calls` holding `sk-` and 20 characters, when posted to the generic route, then the action is BLOCKED."
Status: proposal

### R7-14 [MEDIUM] [secure] packages/guardrail-server/src/guardrail_server/secret_scan.py:12
Problem: `find_secret` returns None for common shapes: `ghp_` tokens, `xoxb-` tokens, JWTs, `ASIA` keys, `glpat-` tokens, `AIza` keys, `password=...`, lowercase `api_key=...`, JSON `"api_key"`, `AWS_SECRET_ACCESS_KEY=...`, `postgres://u:pw@h/db`, and lowercase `bearer`. A zero-width space inside `sk-` also evades it.
Why it matters: With R1-07, the chassis has no other secret backstop on this path.
Fix: Add the vendor prefixes, make `key_assignment` and `bearer` case-insensitive with optional quotes, and apply NFKC and zero-width stripping before the scan. Keeps behavior: no (proposal: blocks more, and the recorded red-team pass rate must be rerun).
Spec: "given `ghp_` and 36 alphanumerics, `password=hunter2hunter2`, or `sk​-` and 20 alphanumerics, when `find_secret` runs, then it returns a kind."
Status: proposal

### R7-15 [MEDIUM] [secure] deploy/compose/poc07/otel-collector.yaml:25
Problem: The OTLP receiver has no authenticator, but the exporter attaches Langfuse Basic auth (:44), and line 4 names the workload as a sender.
Why it matters: Anyone who reaches :4318 writes into Langfuse under the Collector's key and can forge `trace_id`s that the feedback and eval joins rely on. That goes against ADR-001 hard requirement 1.
Fix: Add a `bearertokenauth` server authenticator with a token that `stack.sh` generates and only the chassis gets. Keeps behavior: no (proposal: an exporter without the token is refused).
Spec: "given a POST to :4318/v1/traces without the chassis token, when the Collector receives it, then it answers 401 and Langfuse gets no span."
Status: proposal

### R7-16 [MEDIUM] [secure] packages/chassis/src/chassis/server/feedback.py:413
Problem: The route checks that `request_id` has a finished-run lookup, but not that the caller owns the run, and it accepts a client-chosen `feedback_id`. The body `label` (any text, up to 64 characters) also goes straight into `telemetry.counter` as a metric label (:444).
Why it matters: A caller with `feedback_scope` can rate, or overwrite by id, another caller's score, and can mint label values without bound. Check this against R2-10 (:226) before a fix.
Fix: Store `subject` in the lookup and answer `unknown_request` on a mismatch when auth is on, and count with a bounded label set (`other` for the rest). Keeps behavior: no (proposal).
Spec: "given caller A's run `r1` and caller B with `feedback_scope`, when B posts feedback for `r1`, then the answer is 404 `unknown_request` and A's score is unchanged."
Status: proposal

### R7-17 [MEDIUM] [secure] packages/chassis/src/chassis/server/evaluator_gate.py:106
Problem: The gate samples with `sha256(request_id)`, and native `/v1/run` accepts any client `request_id`. An `EvaluatorError` fails open (:258, :321).
Why it matters: At a sample rate of 0.1, a caller can pick ids that are never gated (about 93 of 100 tried), or make the judge fail to skip the gate.
Fix: Salt the hash with a per-process secret or hash a chassis-minted id, and add a `fail_closed` option like `guardrails.on_unavailable`. Keeps behavior: no (proposal: the sampling decision changes).
Spec: "given sample rate 0.1 and 100 native requests with ids the caller chose, when they are gated, then about 10 percent are scored, whatever the ids."
Status: proposal

### R7-18 [MEDIUM] [secure] [shared] packages/bakeoff/src/bakeoff/evaltrial.py:115
Problem: `poc07-evals.yml:46-49` installs DeepEval with `--with deepeval==4.3.2 --with httpx --with pyyaml`, so `httpx`, `pyyaml`, and every transitive dependency are unpinned and unhashed. promptfoo runs as `npx -y promptfoo@0.125.1` (:95) with no lockfile. The workflow's checkout (`poc07-evals.yml:35`) keeps `persist-credentials: true`, and `setup-uv` caches on `pull_request`, unlike `poc07-stack.yml:37`.
Why it matters: A new upstream release runs in CI unreviewed, next to a persisted token. The two PoC-7 workflows follow different rules.
Fix: Install from a hashed lock with `--require-hashes` (as `deploy/agent-scan/requirements.txt` does) and `npm ci` from a lockfile, and set `persist-credentials: false` with the cache off for `pull_request`. Keeps behavior: yes.
Spec: "given the evals job, when its install steps and checkout are read, then every dependency carries a hash and `persist-credentials` is false."
Status: open

### R7-19 [MEDIUM] [maintainable] packages/chassis/src/chassis/fakes/guardrail.py:25
Problem: The fake's `INJECTION_PATTERNS` (:25) and `SECRET_PATTERNS` (:37) are hand copies of the server's (`classifier.py:46`, `secret_scan.py:12`), and they have drifted: 5 injection patterns against 7, and 3 secret patterns against 5. `classifier.py:64` copies `split_for_classifier`, and `litellm_stub.py:46` copies three PII patterns from `chassis/redaction.py:18`.
Why it matters: The import rule forbids sharing, so a fix in one copy silently misses the other, and the fake stops answering like the real server.
Fix: Add one test in `packages/contract-suites` that runs the same corpus through the fake and the server's pattern classifier and expects the same verdicts, and add a "copy of" comment on each side. Keeps behavior: yes.
Spec: "given `API_TOKEN=abcdefgh12345`, when the fake and the pattern server check it at the output stage, then both block it, or the pinning test fails."
Status: open

### R7-20 [MEDIUM] [maintainable] packages/chassis/src/chassis/adapters/litellm/guardrails.py:37
Problem: The module imports the private `_redact` from `client.py`, and the `_KEY_SHAPE` regex in `client.py:89` is repeated in `adapters/a2a/plain.py:79`.
Why it matters: An underscore import hides a shared contract, and two copies of the key pattern can diverge.
Fix: Make one public `redact_error_text(text, api_key)` in `chassis.redaction` and use it from all three modules. Keeps behavior: yes.
Spec: "given an upstream error holding `sk-abcdefgh1234`, when any of the three adapters formats it, then each shows `[redacted]` through one function."
Status: open

### R7-21 [MEDIUM] [maintainable] [shared] pocs/poc-07-cross-cutting-decisions/tests/poc07_evals_harness.py:73
Problem: `tei_stub` and `_FACT` (:70) copy `packages/chassis/tests/test_evaluators.py:89-113`. `poc07_sec_harness.py:136` `_config` repeats `poc02_harness.chassis_config` with another `version`, and `conftest.py:10` adds five earlier test folders to `sys.path` (the shared-harness fix is R0-02).
Why it matters: Two TEI fakes and two config builders can drift, so the contract binding and the scenario tests may check different things.
Fix: Put one `tei_stub(seen=None)` in a package and import it in both places, and call `chassis_config` with a `version` argument. Keeps behavior: yes.
Spec: "given the chassis encoder test and the PoC-7 evals test, when the toy entailment rule changes, then both use the one new rule."
Status: open

### R7-22 [MEDIUM] [maintainable] deploy/compose/poc07/docker-compose.yaml:92
Problem: The stack does not use the `x-hardening` block from `docker-compose.scale.yaml:33` (`cap_drop: [ALL]`, `no-new-privileges`, `read_only`), not even on the Collector that holds `LANGFUSE_AUTH` (:133). Its MinIO image differs from the pinned one in the scale stack (:198), and `deploy/compose/README.md` does not list this stack.
Why it matters: The rubric says config shared across `deploy/` is listed, not silently copied, and `SECURITY.md` sets the hardening baseline.
Fix: List the stack and its deviations in `deploy/compose/README.md`, and apply the hardening anchor where the images allow it, starting with `otel-collector` and `jaeger`. Keeps behavior: yes (confirm on the stack run).
Spec: "given `docker compose -p poc07 config`, when the services are listed, then each has `cap_drop: [ALL]` or an entry in the README's deviation list."
Status: open

### R7-23 [MEDIUM] [tests] packages/chassis/tests/test_evaluator_gate.py:174
Problem: Outage coverage is one sync test on the first attempt. Nothing tests an `EvaluatorError` on the retry, an error in async or stream mode (`outcome="error"`), or the `EVALUATOR_DROPPED` pending cap.
Why it matters: These are the gate's fail-open and shedding paths, and the user's answer must stay unchanged on each.
Fix: Add three small tests with the fake evaluator (`unavailable`, and a stalled evaluator with `max_pending=1`). Keeps behavior: yes.
Spec: "given the judge is down on the second attempt, when the gate retries, then the second answer goes out and `gate_failed_open` is 1."
Status: open

### R7-24 [MEDIUM] [tests] [shared] pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_trace_join.py:152
Problem: One test makes 16 asserts on seven behaviors, with `for` and `while` around asserts (:183-198). The same shape appears in `test_poc07_sec_marking.py:77` (nested loops, no docstring), `test_poc07_evals_gate.py:103` (`if isinstance(gate, GatedEngine)` around the only gate assert), and `test_poc07_stack_static.py:102`.
Why it matters: These are criterion tests, and one failure hides the rest. The `if` lets the evals test pass with no gate in place.
Fix: Share the run in a fixture, split into parametrized one-behavior tests, assert `isinstance(gate, GatedEngine)` unconditionally, and move the span-ancestry walk into the harness. Keeps behavior: yes.
Spec: "given one `/v1/run` with a caller `traceparent`, when the spans are read, then every span is in the caller's trace" (one test per behavior).
Status: deferred (the split renames tests that README criteria 4 and 5 cite: note for the PoC-7 plan)

### R7-25 [MEDIUM] [tests] pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py:114
Problem: No `test_poc07_*` test has a one-line docstring naming its exit criterion word for word. About 55 have no docstring, and the criterion is only paraphrased in module docstrings. `test_poc07_sec_engines.py:80` `test_auth_and_scopes` checks 401, 403, and 200 in one test, and `evals/deepeval/test_golden.py:141` (the CI eval gate) has no docstring.
Why it matters: `pocs/CLAUDE.md` requires the criterion in the test, so the checklist cannot be audited from the tests.
Fix: Add a one-line docstring that quotes the criterion from the planning doc, and in `test_golden` say that the rubric score is scripted offline. Keeps behavior: yes.
Spec: "given a PoC-7 scenario test, when it is read, then its docstring quotes the exit criterion it proves."
Status: open (docstrings only. Splitting `test_auth_and_scopes` renames cited evidence, so it waits for PoC-7 to close.)

### R7-26 [MEDIUM] [tests] packages/guardrail-server/tests/test_litellm_stub.py:77
Problem: Stub branches with no test: guardrail server unreachable (500), a non-200 from it, `GUARDRAIL_INTERVENED` with `texts`, card and phone masking in `mask_pii`, and `create_app(threshold=...)`. None of the package's 18 tests has a docstring.
Why it matters: `LiteLLMGuardrails` is tested against this stub, so a wrong error shape in the stub makes that adapter's fail-closed tests wrong too.
Fix: Add tests for the two 500 branches and the intervened path, a parametrized `mask_pii` test, and a threshold test with a stub classifier. Keeps behavior: yes.
Spec: "given the guardrail server is down or answers 500, when `prompt-guard` is applied, then the stub answers 500 with its message."
Status: open

### R7-27 [MEDIUM] [tests] pocs/poc-07-cross-cutting-decisions/README.md:38
Problem: Criterion 6 is ticked and cites only `notes/2026-10-28-per-engine-mapping.md`. The README itself requires a scenario test or a recorded reason, and neither exists. The note's "Run" cells are not tied to the engines the tests use.
Why it matters: The "Run" claims can drift from what the tests actually run.
Fix: Add a test that reads the table and compares its "Run" cells to the engine lists in the scenario tests, or record the reason in the README. Keeps behavior: yes.
Spec: "given the per-engine note, when its table is read, then each cell marked Run matches an engine that a scenario test runs."
Status: deferred (open exit criterion: note for the PoC-7 plan)

### R7-28 [MEDIUM] [docs] docs/planning/adr/010-evals-offline-tool-online-gate.md:66
Problem: The tool criteria count as "met by DeepEval in the trial unless noted", but "judge calls go through LiteLLM" was not exercised (the trial's judge was the fake model server, through `JUDGE_URL`). Item 5 (:83) is a broken sentence that names two spans where the code has one (`SCORE_SPAN`, `evaluator_gate.py:93`). ADR-009 item 7 (:52) says gate results go to Langfuse as scores, which ADR-010 :83 denies and :96 asserts.
Why it matters: The user is asked to accept ADRs that overstate one criterion and disagree on where scores go.
Fix: Add a "waits on the Mac run" note to the LiteLLM criterion, keep one sentence that names `chassis.evaluator.score`, and say once, in ADR-010 item 5, that gate results are span attributes today and Langfuse scores in PoC-8. ADR-009 links there. Keeps behavior: yes.
Spec: "given ADR-009 and ADR-010, when each says where evaluator scores go, then both say the same thing, and every unexercised criterion carries a 'waits on' note."
Status: open

### R7-29 [LOW] [docs] pocs/poc-07-cross-cutting-decisions/README.md:1
Problem: The README has no `Builds on:` line and no `Hands to:` line. Its first three lines never say how to run it, because `make test-poc POC=07` first appears at line 40.
Why it matters: A reader cannot follow the PoC chain or start a run from the top of the file. The template gap is R0's finding, and this is the PoC-7 instance.
Fix: Add `Builds on: PoC-1 (ports), PoC-5, PoC-6` and `Hands to: PoC-8` under the title, plus a one-line run command. Keeps behavior: yes.
Spec: "given the PoC-7 README, when it is read, then it has `Builds on:` and `Hands to:` lines, and its first three sentences say what it is, why it exists, and how to run it."
Status: open

### R7-30 [LOW] [secure] packages/guardrail-server/src/guardrail_server/app.py:99
Problem: `blocked_reason` carries the classifier's score and name (`prompt_injection (score 0.73, pattern)`), and LiteLLM passes it back to the caller (`litellm_stub.py:124`). FastAPI's default 422 also echoes the rejected value, so a secret-shaped `checks` entry comes back in the body.
Why it matters: A caller can tune an injection by watching the score, and a secret-shaped value can reach LiteLLM's error logs. That breaks "errors name the reason, never the text".
Fix: Return only the reason word, log the score on the server, and add a `RequestValidationError` handler that drops `input` and `ctx`. Keeps behavior: yes (the chassis maps only the reason word, `guardrails.py:106`).
Spec: "given an injection text, when the generic route blocks it, then `blocked_reason` has no digit, and given `checks: ['sk-...']`, then the 422 body has no `sk-`."
Status: open
