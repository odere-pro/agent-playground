# Fix plan

Written by R8 on 2026-10-29. It covers R4 to R7, the leftovers of R0 to R3, and the baseline findings.
It also merges duplicates, settles contradictions, and lists the gaps in the PoC story.
Proposals are in [proposals.md](proposals.md). The user decides each one.

## Waves

| Wave | Sessions | Starts when |
| ---- | -------- | ----------- |
| 1 | F0, F1, F2, F3 (running) | now |
| 2 | F4, F5, F6, F7 | all four wave-1 PRs are merged |
| 3 | F0 follow-up, F9 | all four wave-2 PRs are merged |
| 4 | F8 | the F0 follow-up PR is merged |

Why this order:

- Wave 2 waits for F0, because F0 moves the harness and changes the import lines of PoC tests that wave 2 edits.
- Wave 2 also waits for F1 to F3, because F4 to F7 edit `server/`, `ports/`, `adapters/`, and `redaction.py` after them.
- The F0 follow-up waits for wave 2, because it edits harness files and test import lines in PoC-4 to PoC-7.
- F8 goes last, because it edits the PoC-4 test bodies that F4 and the F0 follow-up touch, and it uses the final harness.

## Rules for every session

- One small PR against `main`. Rebase on `main` before the merge.
- Keep behavior. A fix that changes the wire contract, the event schema, or what an exit criterion means stops and goes to [proposals.md](proposals.md).
- Edit only the files listed for the session. A needed change elsewhere goes to its owner's list, or to the F0 follow-up.
- Shared root files (`Makefile`, `pyproject.toml`, `uv.lock`, CI, `.claude/`, root `CLAUDE.md`) and every PoC harness, conftest, and support file belong to F0 only.
- Do not tick or untick a box in PoC-6 or PoC-7. Keep their `Status:` values.
- A renamed test updates every README line that cites it, in the same PR.
- Run uv with `UV_NO_SYNC=1`. Before the PR: `make quick`, `make check`, and `make test-poc` for each PoC touched (`POC=06` works once B-3 is in).
- Set each fixed finding to `Status: fixed (PR)` in the PR description, not in the findings file.

## F4: stateless and scalable (R4)

Wave 2. Merge first: F0, F1, F2, F3.

Owns findings:

- R4-03, R4-04, R4-05, R4-06, R4-07, R4-08, R4-09, R4-11, R4-12, R4-13, R4-15, R4-16, R4-19, R4-20, R4-21, R4-22, R4-23, R4-24, R4-25, R4-26, R4-28.
- R4-10, the backoff and disconnect part only. The waiter cap is a proposal.
- R4-27, the `surrogatepass` part only. The length cap is part of proposal R1-09.
- R4-29, the docstring part only. The entry format is a proposal.
- R5b-27, because F4 owns `deploy/compose/SECURITY.md`.
- May take the dropped LOW items in its files (`R4-dropped-low.md` in the notes folder).

Owns files:

- `packages/chassis/src/chassis/server/`: `idempotency.py`, `config.py`, `config_loader.py`, `lifecycle.py`.
- `packages/chassis/src/chassis/adapters/`: `valkey/`, `s3/`, `dapr/`, `kafka/`, and one new publish-retry helper module for R4-19.
- `packages/contract-suites/src/chassis_contracts/containers/` (`dapr.py`, `kafka.py`, `minio.py`, `valkey.py`).
- Chassis tests for these modules: `test_idempotency.py`, `test_idempotency_serve.py`, and the config, loader, Valkey, S3, Dapr, and Kafka tests.
- `deploy/compose/docker-compose.scale*.yaml`, `deploy/compose/scale.sh`, `deploy/compose/SECURITY.md`.
- `deploy/kind/cluster.yaml`, `deploy/kind/run.sh`, `deploy/kind/poc04/`.
- `pocs/poc-04-stateless-scalable/`: `README.md`, `CLAUDE.md`, `notes/`, `load/README.md`, and the test files that are not a harness or conftest (`test_hidden_state.py`, `test_killed_replica.py`, `test_idempotency_replicas.py`, `test_idempotency_valkey.py`, `test_graceful_shutdown.py`, `test_config_reload.py`, `test_config_minio.py`, `test_workload_drain.py`, `test_read_only_compose.py`, `test_compose_scale.py`, and a new network-policy static test for R4-12).
- `docs/guides/poc-04-how-it-works.md`, `docs/contracts/contract-v3.md`, `docs/contracts/contract-v4.md`, and a new `docs/contracts/README.md`.

Notes:

- R4-05 and R4-06 change docstrings only. F8 changes the wall-clock waits in the same files in wave 4.
- R4-11 changes the Compose network. Rerun `deploy/compose/scale.sh` and record it with `record-measurement`.
- Story gaps F4 may close: a "Superseded by" banner on contract v3, and a new `docs/contracts/README.md` that maps each version to its PoC.

## F5: sandboxed (R5a and R5b)

Wave 2. Merge first: F0, F1, F2, F3.

Owns findings:

- R5a-03, R5a-08, R5a-09, R5a-10, R5a-13, R5a-14, R5a-15, R5a-17, R5a-18, R5a-19, R5a-20, R5a-21.
- R5b-01, R5b-03, R5b-04, R5b-05, R5b-06, R5b-07, R5b-09, R5b-13, R5b-14, R5b-15, R5b-16, R5b-17, R5b-18, R5b-19, R5b-20, R5b-21, R5b-23, R5b-24, R5b-25, R5b-28, R5b-30.
- R5b-29, except the `.claude/` skill line (F0 follow-up).
- May take the dropped LOW items in its files (`R5-dropped-low.md`).

Owns files:

- `deploy/kind/poc05/run.sh`, `deploy/kind/poc05/platform/seed.sh`. No edit to `admission/` or any `network-policy.yaml`: those changes are proposals.
- `packages/code-runner/` (all of it).
- `packages/chassis/src/chassis/adapters/mcp/gateway.py`, `adapters/oidc/__init__.py`, `server/tool_endpoint.py`, `ports/tool.py`.
- Chassis tests for those modules, including `test_tool_gateway_contract.py` (the R5b-09 drift test).
- `pocs/poc-05-sandboxed/`: `README.md`, `CLAUDE.md`, `KICKOFF.md`, `notes/`, `demo/README.md`, and the test files that are not `poc05_harness.py`, `poc05_kind.py`, or `poc05_conftest.py`. New kind test files for R5a-08 are fine.
- `docs/guides/poc-05-how-it-works.md`, `docs/guides/poc-05-runbooks.md`, `deploy/CLAUDE.md`, `deploy/README.md`.

Notes:

- Do R5a-03 before R5b-25. The guide sentence must match the CI verb after R5a-03.
- Story gap F5 may close: link contract v4 from the PoC-5 README.
- R5b-22 splits `test_poc05_admission_static.py` into harness modules in wave 3. F5 edits that file first (R5b-01, R5b-13). Keep the R5b-01 mutation test small, so the split stays easy.

## F6: framework bake-off (R6)

Wave 2. Merge first: F0, F1, F2, F3.

Owns findings:

- R6-02, R6-05, R6-08, R6-10, R6-12, R6-15, R6-16, R6-17, R6-20, R6-22, R6-23, R6-25, R6-26, R6-27, R6-28, R6-29, R6-30.
- R6-19, the rename part only. Changing what the test matches is a proposal.
- R6-24, the doc parts only. Where 6c keeps its record goes to the PoC-6 plan.
- May take the dropped LOW items in its files (`R6-dropped-low.md`).

Owns files:

- `packages/workloads/echo-claude-agent/`, `echo-openai-agents/`, `echo-smolagents/`, and the `handle.py` and `mapping.py` files of `echo-pydanticai` and `echo-langgraph` that R6-15 names.
- `packages/workload-a2a/`: one new helper module for `event`, `timeout_s`, and the error helpers, with its test. Not `server.py` (F9).
- `packages/bakeoff/src/bakeoff/`: `stack.py`, `report.py`, `evaltrial.py` (the `run_one` split only), and `packages/bakeoff/README.md`.
- `deploy/kind/poc06/` (manifests, `run.sh`, `hosted.sh`, `seed.sh`, `hosted/network-policy.yaml`), `deploy/compose/poc06/litellm-hosted.yaml`.
- `docs/guides/poc-06-how-it-works.md`.
- `pocs/poc-06a-*`, `poc-06b-*`, `poc-06c-*`: `README.md`, `CLAUDE.md`, `notes/`, and these tests: `test_poc06c_mac_static.py`, `test_poc06b_kind_static.py`. Not the harness, conftest, or `poc06b_kind.py` files.
- `packages/workloads/echo-claude-agent/tests/test_echo_claude_agent_handle.py`.

Notes:

- PoC-6 is open. R6-08 and R6-25 change the README openings, not the boxes or the `Status:` value.
- R6-29 changes image tags. If `poc06b_kind.py` names a tag, that line goes to the F0 follow-up.

## F7: cross-cutting decisions (R7)

Wave 2. Merge first: F0, F1, F2, F3.

Owns findings:

- R7-01, R7-02, R7-03, R7-05, R7-06, R7-08, R7-10, R7-11, R7-12, R7-19, R7-20, R7-22, R7-23, R7-26, R7-28, R7-29, R7-30.
- R7-25, docstrings only. Splitting `test_auth_and_scopes` waits for PoC-7 to close.
- May take the dropped LOW items in its files (`R7-dropped-low.md`), except the ADR-001 pointer, which waits for ADR-008.

Owns files:

- `packages/guardrail-server/`, `packages/fake-langfuse/`.
- `packages/chassis/src/chassis/adapters/otel/`, `adapters/litellm/guardrails.py`, `adapters/litellm/client.py`, `adapters/a2a/plain.py` (the `_KEY_SHAPE` line), `redaction.py` (the new public `redact_error_text`), `fakes/guardrail.py`.
- Chassis tests: `test_guard_stages.py`, `test_evaluator_gate.py`, `test_otel_telemetry.py`, and the LiteLLM guardrail tests.
- One new corpus test in `packages/contract-suites/tests/` for R7-19.
- `deploy/compose/poc07/`, `deploy/compose/README.md`, `deploy/agent-scan/scan.sh`.
- `docs/planning/adr/007-*.md` to `010-*.md`.
- `pocs/poc-07-cross-cutting-decisions/README.md`, and the docstrings of `tests/test_poc07_*.py`. Not `conftest.py` or `poc07_*_harness.py`.

Notes:

- PoC-7 is open. R7-29 adds the `Builds on:` and run lines only. No box changes.
- R7-05 marks the stream test `xfail(strict=True)` with the reason "R7-04 open". If the user rejects R7-04, the test becomes a named known-gap test that passes.
- ADR-007 to ADR-010 wait for the user (criterion 3). R7-08 and R7-28 fix text only. They do not change a decision.

## F0 follow-up (F0b)

Wave 3. Merge first: wave 2 (F4, F5, F6, F7). Run by F0 if its session is still open, else by a new session.

Duplicates merged into an F0 canonical ID. F0 takes them in wave 1 if its branch is still open, else here:

- R4-01, R5b-02, R6-03 (into R0-02). R4-17, R5b-11 (into R0-15). R5a-04 (into R0-13).

Findings from R4 to R7 that need a harness, conftest, support, or root file:

- R4-02: move the PoC-4 load tools into `packages/`, and drop the `sys.path` lines in `idem_cost.py` and `median.py`.
- R4-18: one `wait_until` in `poc_harness`, and replace the seven poll helpers in the PoC-4 tests.
- R5b-10: one `node_sh` and one `exec_probe` in `poc05_kind.py`, used by `poc06b_kind.py` too.
- R5b-22: move the admission and netpol models into harness modules, and split the two test files by topic.
- R5b-26, and the skill line of R5b-29: `.claude/skills/poc-05-operate/SKILL.md`.
- R6-04: derive trust and lane in the harness and the kind table from `bakeoff.registry`.
- R6-13: public helper names for what 06b imports from PoC-5 tests.
- R6-14: move the eval trial into its own package (`pyproject.toml`, `uv.lock`, `evals/`, the PoC-7 workflow).
- R6-21: the manifest and poll helpers in `poc06b_kind.py`, and the split of the two tests that use them.
- R7-07: move `workload_on_uds`, `run_once`, `gated_run`, and `routes` into the PoC-7 harness, and split `_trace_and_feedback`. Do not change the dated demo record.
- R7-18: hashed locks for the eval tools and `persist-credentials: false` in `poc07-evals.yml` (one edit with R0-28's part).
- R7-21: one `tei_stub`, and `chassis_config(version=...)`.

Leftovers from wave 1 that need F0's files:

- R1-12 (`PortBundle.state` required; about 22 harness calls). R1-15 (a PoC-1 conftest).
- R1-06, the skill half. R1-21, the template half (part of R0-25). R1-22, the `CLAUDE.md` half.
- R3-25, the `tests/poc03_support.py:4` docstring.
- R6 dropped note: the `poc06b_conftest.py:46` skip reason (near R0-13).

## F9: chassis and kind leftovers

Wave 3. Merge first: wave 2 (F4 owns `config.py` and `deploy/kind/run.sh` until then; F5 and F6 own the kind scripts).

Owns findings:

- R2-05: one `task_store.py` copied byte for byte in both A2A servers, a `drive_stream` helper, and the copy guard in `test_workload_a2a_copies.py` (plan in `F2-handover.md`).
- R2-09: a separate inbound token, `engine.auth.proxy_token_env`, that falls back to `token_env`. If `chassis-architect` says it changes contract v4 or v5, it becomes a proposal.
- R5b-12: one `deploy/kind/lib.sh`, sourced by every kind script.
- Any item a wave-1 session leaves open in a chassis source file (for example the R1-10 `inbound.py` half, if F3 leaves it).

Owns files:

- `packages/chassis/src/chassis/adapters/a2a/server.py` and a new `adapters/a2a/task_store.py`.
- `packages/workload-a2a/src/workload_a2a/server.py`, a new `task_store.py`, `auth.py`, and `packages/workload-a2a/tests/`.
- `packages/chassis/src/chassis/server/config.py`, `server/remote_auth.py`, `adapters/a2a/remote.py`, and their chassis tests.
- `deploy/kind/lib.sh` (new), `deploy/kind/run.sh`, and the `.sh` files under `deploy/kind/poc05/` and `deploy/kind/poc06/`.

## F8: test bodies and wall-clock tests

Wave 4. Merge first: F4 and the F0 follow-up.

Owns findings:

- R2-41, R2-42 (PoC-2 test names and docstrings).
- R3-17, R3-22 (PoC-3 test bodies).
- R2-22, part 2: reuse the harness helpers in PoC-2 `demo/measure.py`.
- B-1, with R4-14 merged in, and every test in R4's "Wall-clock tests" list marked tight. The wide ones need no change.
- Any test-only item a wave-1 session leaves open (for example the sleep loop in R2-44).

Owns files:

- PoC-2: `tests/test_tools.py`, `tests/test_boundaries.py`, `demo/measure.py`, and the README lines that cite renamed tests.
- PoC-3: `tests/test_exit_criteria.py`, `tests/test_openapi_props.py`, and the README lines that cite them.
- PoC-4: `test_killed_replica.py`, `test_timeout_budget_per_engine.py`, `test_graceful_shutdown.py`, `test_workload_drain.py`, `test_idempotency_replicas.py`, `test_idempotency_valkey.py`, `test_liveness.py`.
- Chassis tests: `test_shutdown.py`, `test_readiness.py`, `test_client_disconnect.py`.
- Contract suites: `chassis_contracts/state.py`, `events.py`, `config.py`, and the integration tests `test_dapr_events_contract.py` and `test_s3_config_contract.py`.
- `server/lifecycle.py` and `server/readiness.py`, only to inject a clock whose default is the real one.

Notes:

- Use a fake clock or an event the code sets. Widen a margin only where a fake clock would need a behavior change.
- Real sleeps stay only as hang guards.
- A harness change still goes to F0. Use the existing `OffsetClock` in `poc04_harness.py` where it fits.

## Routed to a PoC plan, not a fix session

PoC-6 plan, [2026-10-09-poc-06-bake-off.md](../../plans/2026-10-09-poc-06-bake-off.md):

- R6-06: add `test_poc06b_criteria.py`, one test per criterion.
- R6-07: reword three skip reasons in `test_poc06a_criteria.py`. Making criterion 3 call the scorecard check is a proposal.
- R6-09: align the `kagent-adk` status in ADR-006, the 6b README, and the plan.
- R6-18: what criteria 1 and 2 of 6c assert (also a proposal).
- R6-24: whether 6c's record is `demo/` or `notes/`.

PoC-7 plan, [2026-10-28-poc-07-cross-cutting.md](../../plans/2026-10-28-poc-07-cross-cutting.md):

- R7-04: criterion 5 claims each concern works, but a streamed answer skips the output check. State the gap (also a proposal).
- R7-09: one concrete pending check per "waits on" item.
- R7-24: split the trace-join and marking tests after the PoC closes.
- R7-25: split `test_auth_and_scopes` after the PoC closes.
- R7-27: a test or a recorded reason for criterion 6.
- R1-20, the `test_contracts_poc07.py` rename: `test_poc07_ports.py:36` reads it by path. F0 renames `test_poc04_seams.py` now.
- R7 dropped note: the "Tempo" stand-in name in `test_poc07_obs_telemetry_swap.py`.

## Duplicates

One canonical ID per problem. The owner of the canonical ID fixes all of it.

| Canonical | Merged | The problem | Owner |
| --------- | ------ | ----------- | ----- |
| R0-02 | R2-03, R3-03, R4-01, R5b-02, R6-03 | Shared test harness lives in PoC folders, reached through `sys.path`. | F0 |
| R0-25 | R1-21, R2-26, R3-05, R4-21, R5b-23 (lines part), R6-08, R7-29 (lines part) | No README has `Builds on:` or `Hands to:`, and the template lacks them. | F0 for the template; each area session for its README |
| B-3 | R0-11 | `make test-poc POC=06` matches no folder. | F0 |
| R1-20 | R2-33 | Two chassis test files carry a PoC number. | F0 (see the PoC-7 plan for one file) |
| R1-03 | R2-25 | `packages/chassis/README.md:3` names a different current contract than `CLAUDE.md`. | F1 |
| R0-15 | R2-40, R3-16, R4-17, R5b-11 | `free_port`, the Unix-socket server, and the SSE parser are copied across harnesses. | F0 |
| R0-13 | R5a-04 | The kind gate is copied per PoC, and a set gate with no cluster skips instead of failing. | F0 |
| R3-02 | R1-08 | Native `/v1/run` and MCP return the run's raw error text. | proposal |
| R2-10 | R7-16 | The feedback route does not check that the caller owns the run. | proposal |
| B-1 | R4-14 | Tests use real sleeps as sync and fail under load. | F8 |

23 findings are merged into 10 canonical IDs.

Related, not merged, because the code differs:

- Body caps: R2-01 (proxy chat route), R3-01 (`BodyLimit`), R5a-11 (code runner), R7-12 (guardrail server).
- FastAPI's default 422 echoes input: R3-29 (native, MCP), R7-30 (guardrail server).
- Images not pinned by digest: R0-28, R4-13, R6-28, R7-03.
- Secret shapes kept in four places: R1-07, R7-14, R7-19, R7-20.
- Copied code with no drift test: R2-05, R5b-09, R7-19.
- Criterion not quoted word for word: R0-20, R4-05, R5b-18, R6-06, R7-25.
- Client id bounds: R1-09, R4-27, and the R3 and R7 dropped notes.
- Contract banners: R1-25, R2-23, and the R4 dropped note on v3.

## Contradictions and calls

1. **R1-20 against F0's note.** R1-20 renames `test_contracts_poc07.py`, but `test_poc07_ports.py:36` reads it by path, and PoC-7 is open. Call: F0 renames `test_poc04_seams.py` now. The other rename goes to the PoC-7 plan.
2. **Where shared test code lives.** F0 chose `packages/poc-harness`. R4-17 and R4-18 say `chassis_contracts`. R6-03 says a pytest plugin. Call: `poc_harness` holds all cross-PoC test support, as a plain module. `chassis_contracts` keeps only the contract suites. A plugin comes only if a shared fixture needs one.
3. **R5a-03 against R5b-25.** R5b-25 rewrites the guide to say which kind suites run in CI, and R5a-03 changes that list. Call: F5 does R5a-03 first and writes the sentence for the final state.
4. **R7-05 against R7-04.** R7-05 marks the stream test as a gap "against the intended behavior", but R7-04 is not decided. Call: `xfail(strict=True)` with the reason "R7-04 open". The user's answer on R7-04 sets the final test.
5. **R7-19 against R1-07 and R7-14.** R7-19 wants the fake's patterns to match the server's. R1-07 and R7-14 would each change one side. Call: the R7-19 corpus test pins today's patterns. Decide R1-07 and R7-14 together, and change the fake in the same PR.
6. **R2b's count.** The R2b summary says two proposals. Only R2-31 has `Status: proposal`. Call: one proposal.

## Story gaps

- **No `Builds on:` or `Hands to:` line.** On `main`, none of the nine PoC READMEs has one, and the template has none (R0-25). F1, F2, and F3 add them for PoC-1 to PoC-3 in their branches. PoC-4 (F4), PoC-5 (F5), PoC-6 (F6), and PoC-7 (F7) follow in wave 2. PoC-8 and PoC-9 have not started; the template change covers them.
- **Contract steps.** Each contract has one PoC: v0 PoC-1, v1 PoC-2, v2 PoC-3, v3 PoC-4, v4 PoC-5, v5 PoC-6, v6 PoC-7 (draft). Gaps:
  - The PoC-5 README never links contract v4. The 6a and 6c READMEs link no contract.
  - `docs/contracts/` has no index from version to PoC (R5 dropped note). The R5 brief itself named the wrong version.
  - v1, v2, and v3 have no "Superseded by" banner (R2-23, R4 dropped note). The v0 banner points at v1, which is itself superseded (R1-25).
  - Three docs name three different current contracts (R1-03). v6 is still a draft.
- **Harness code one PoC copies from another.** The `sys.path` chain grows with each PoC:

  | Importer | Reaches the tests of |
  | -------- | -------------------- |
  | PoC-3 `poc03_harness.py` | PoC-2 |
  | PoC-4 `poc04_conftest.py` | PoC-2, PoC-3 |
  | PoC-5 `poc05_conftest.py` | PoC-2, PoC-3, PoC-4 |
  | PoC-6a `conftest.py` | PoC-2 to PoC-5 |
  | PoC-6b and 6c `conftest.py` | PoC-2, PoC-4, PoC-5, PoC-6a |
  | PoC-7 `conftest.py` | PoC-2 to PoC-6a |
  | PoC-7 `demo/demo.py` | PoC-2 to PoC-7 |
  | PoC-4 `load/idem_cost.py`, `median.py` | PoC-4 `load/run_matrix.py` |

  Copies on top of the imports: the kind gate (R0-13), the PoC-5 probes in `poc06b_kind.py` (R5b-10), the Unix-socket server and SSE parsers (R0-15), `chassis_config` in `poc07_sec_harness.py` (R7-21), and three engine tables (R6-04). 06b imports private names from PoC-5 test modules (R6-13). The PoC-7 demo imports test modules (R7-07). `story.md` names `poc02_harness.py` as a hand-off from PoC-2. It is the main gap, and R0-02 closes it.
- **The kind setup is copied, not handed on.** The PoC-4 paragraph says it hands PoC-5 "the kind cluster setup". PoC-5 and PoC-6 instead copy the `kctl`, `log`, and `die` block into seven scripts (R5b-12).
- **Rules an early PoC set that a later PoC breaks:**
  - No `sys.path` insert in `pocs/*/tests` (PoC-0): broken from PoC-3 on (table above).
  - No PoC number in a package, marker, or name (PoC-0): the env gates (R0-14), the chassis test file names (R1-20), and the image tags `:poc05` and `:poc06` (R6-29).
  - Tests run offline with no keys (PoC-0, PoC-1): a bare `uv run pytest` opens sockets (R0-05), and the key strip misses `LANGFUSE_SECRET_KEY`, added in PoC-7 (R0-06).
  - Pin every image and action by hash (PoC-0): the PoC-4 test containers (R4-13), the kagent bases in PoC-6 (R6-28), the PoC-7 stack (R7-03), and the PoC-7 eval tools (R7-18).
  - A fake and every real adapter pass one suite (PoC-1): the gateway's schema checker differs from the fake's (R5b-08). The fake guardrail patterns drift from the server's (R7-19). The fake MCP server copies its data (R5b-09).
  - Errors send `public_message(code)`, never the run's text (PoC-3): native and MCP break it from the start (R3-02). Later code does too: the code runner (R5a-10), the Claude agent's stderr (R6-26), the guardrail reason (R7-30), and FastAPI's 422 body (R3-29).
  - Compose services use the `x-hardening` block (PoC-4): the PoC-7 stack does not (R7-22).
  - Only the chassis container holds a credential (PoC-5): the Claude agent's Bash tool can read `CHASSIS_API_TOKEN` (R6-02), and admission does not check sidecar pods (R5a-01).
  - Every refusal on kind sits next to its allowed control (PoC-5): R5b-20 has no allowed control, and R6-21 returns early.
  - Text that leaves the chassis is redacted (PoC-7): exception text in spans (R7-02) and the Collector's traces pipeline (R7-10).
- **Against the Agent MVP bar** ([000-plan.md](../../planning/poc/000-plan.md)):
  - "Every pipeline stage applies in every lane": the output guard skips streamed answers (R7-04).
  - "Every lane is tested on every commit": the admission and sidecar kind suites run by hand (R5a-03).

## Seen but not filed

These came up in summaries or notes and have no ID. The user decides whether to file them.

- R2 summary: `profiles.py:323` `_check_cloud` accepts `spec.adapters.auth: none` in the `cloud` profile. The public `/v1/run` then runs as the anonymous principal with every scope. Owner: F1's area. A refusal would be a startup refusal, so a proposal, like R4-30.
- R6 dropped note: `model_proxy.py:555` forces the run's route only for correlated calls. An uncorrelated call may pick any route its key lists. A proposal if anything.

## Totals

Findings per area and severity. "Proposals" counts `Status: proposal`. "Deferred" counts `Status: deferred`, all routed to a PoC plan.

| Area | Critical | High | Medium | Low | Total | Proposals | Deferred |
| ---- | -------- | ---- | ------ | --- | ----- | --------- | -------- |
| R0 scaffold | 0 | 4 | 23 | 3 | 30 | 3 | 0 |
| R1 skeleton | 0 | 5 | 20 | 5 | 30 | 5 | 0 |
| R2 engines (with R2b) | 0 | 6 | 21 | 20 | 47 | 3 | 0 |
| R3 interfaces | 0 | 5 | 23 | 2 | 30 | 3 | 0 |
| R4 stateless | 0 | 7 | 19 | 4 | 30 | 2 | 0 |
| R5a sandboxed, secure | 0 | 2 | 10 | 13 | 25 | 12 | 0 |
| R5b sandboxed, quality | 0 | 3 | 26 | 1 | 30 | 1 | 0 |
| R6 bake-off | 0 | 7 | 18 | 5 | 30 | 3 | 3 |
| R7 cross-cutting | 0 | 9 | 19 | 2 | 30 | 6 | 3 |
| Baseline | 0 | 0 | 2 | 1 | 3 | 0 | 0 |
| **All** | **0** | **48** | **181** | **56** | **285** | **38** | **6** |

- 23 findings merge into 10 canonical IDs, so 262 distinct problems remain.
- Fix sessions also turned eight more findings, in whole or in part, into proposals (R2-08, R2-10, R2-11, R3-30, R4-10, R4-27, R6-07, R6-19). They are in [proposals.md](proposals.md), not in the "Proposals" column.
- The dropped LOW notes in the shared notes folder are not counted.

How the open findings split by session:

| Session | Wave | Findings owned |
| ------- | ---- | -------------- |
| F0 to F3 | 1 | R0 to R3, per the briefs |
| F4 | 2 | 24 from R4 (three in part), plus R5b-27 |
| F5 | 2 | 12 from R5a and 22 from R5b |
| F6 | 2 | 19 from R6 |
| F7 | 2 | 18 from R7 |
| F0 follow-up | 3 | 6 merged IDs, 12 from R4 to R7, 6 wave-1 leftovers, and one dropped note |
| F9 | 3 | R2-05, R2-09, R5b-12, and wave-1 leftovers in chassis source |
| F8 | 4 | R2-41, R2-42, R3-17, R3-22, R2-22 part 2, B-1 with R4-14 |
| PoC-6 and PoC-7 plans | none | 12 items |
