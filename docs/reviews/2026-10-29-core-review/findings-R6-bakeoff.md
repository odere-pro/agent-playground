# Findings R6: framework bake-off

Session: R6. Area: `packages/bakeoff/`, `packages/workloads/echo-openai-agents/`, `echo-smolagents/`, `echo-claude-agent/`, `deploy/kind/poc06/`, `deploy/compose/poc06/`, `deploy/compose/docker-compose.poc06.yaml`, `deploy/compose/litellm/`, `scripts/poc06_mac.sh`, `.github/workflows/poc06-*.yml`, `docs/guides/poc-06-how-it-works.md`, `docs/contracts/contract-v6.md`, `docs/planning/adr/006-agent-engines-default-supported-lanes.md`. PoC: `pocs/poc-06a-bake-off-sidecar-lane/`, `pocs/poc-06b-bake-off-remote-lane/`, `pocs/poc-06c-pretrained-slm/`.
Rubric: [rubric.md](rubric.md).

## Summary

The bake-off code is in good shape on the hard rules. Ruff is clean. Every PoC-6 pod sets `automountServiceAccountToken: false`. The remote namespaces are default-deny. The provider key never reaches a workload pod, and every action and pulled image is pinned. The top risks are drift and one secret path. The tests and the bake-off kit measure two different task texts. Engine trust is kept in three tables. Shared test code reaches PoC-7 only through `sys.path`. The Claude agent's Bash tool can read `CHASSIS_API_TOKEN` and put it into unscrubbed events. The docs disagree on what is still open, so PoC-6 items below are routed to [its plan](../../plans/2026-10-09-poc-06-bake-off.md), not to a fix session.

Already reported elsewhere, not repeated: the kind gate copied from PoC-5 (R0-13), `poc06_mac.sh` size (R0-17), the `poc06-slm.yml` digest path (R0-28), and `make test-poc POC=06` collecting nothing (B-3). The missing `Builds on:` and `Hands to:` lines are repo-wide (R1, R2); R6-08 covers only the PoC-6 lines. Dropped LOW items and one cross-area note are in the shared notes folder (`R6-dropped-low.md`).

Checks run (read-only):

```
$ UV_NO_SYNC=1 uv run ruff check packages/bakeoff packages/workloads/echo-openai-agents packages/workloads/echo-smolagents packages/workloads/echo-claude-agent pocs/poc-06a-bake-off-sidecar-lane pocs/poc-06b-bake-off-remote-lane pocs/poc-06c-pretrained-slm
All checks passed!
$ grep -c "^Builds on:\|^Hands to:" pocs/poc-06*/README.md
pocs/poc-06a-bake-off-sidecar-lane/README.md:0
pocs/poc-06b-bake-off-remote-lane/README.md:0
pocs/poc-06c-pretrained-slm/README.md:0
```

## Findings

Most severe first. 30 findings: 7 HIGH, 18 MEDIUM, 5 LOW.

### R6-01 [HIGH] [maintainable] [shared] pocs/poc-06a-bake-off-sidecar-lane/tests/poc06_harness.py:56
Problem: The tests and the bake-off kit use two task lists with different prompts. The harness asks "lookup: What does SLM mean, and what does RAG stand for?"; `packages/bakeoff/src/bakeoff/tasks.py:51` asks "lookup: Define SLM and expand RAG.". The simplifier texts differ too (`poc06_harness.py:58-61` vs `tasks.py:47-49`).
Why it matters: The offline tests and the scorecard can both pass while they measure different things. The fake model script matches only the prefixes, so the gap shows only on a real model.
Fix: Keep one `Task` source (text, facts, tool steps) in `bakeoff.tasks` and import it in the harness. Keeps behavior: no (one side's prompt changes; check the recorded scorecards still hold).
Spec: "given the harness and `bakeoff.tasks`, when both are loaded, then each task name maps to the same text."
Status: proposal

### R6-02 [HIGH] [secure] packages/workloads/echo-claude-agent/src/echo_claude_agent/handle.py:218
Problem: `_scrub` removes the token only from the final `error` event. The CLI env carries the token as `ANTHROPIC_AUTH_TOKEN` (`:128`), Bash is an allowed tool with `permission_mode="dontAsk"` (`:68`, `:154`), and every other event is yielded as is (`:207`).
Why it matters: A prompt injection that runs `env` puts the per-remote token into a `tool_call` result and a `delta`. From there it reaches the chassis log and the model context, which on `big-default` goes to the hosted provider.
Fix: Pass every outgoing event through `_scrub`, including `tool_call` arguments and results and each `delta`. Keeps behavior: yes.
Spec: "given `CHASSIS_API_TOKEN=t0k` and a scripted model that calls Bash `env`, when `handle` runs, then no event contains `t0k`."
Status: open

### R6-03 [HIGH] [maintainable] [shared] pocs/poc-06a-bake-off-sidecar-lane/tests/conftest.py:20
Problem: `poc06_harness.py` and `poc06a_harness.py` are shared test code, imported by 06b (`poc06b_kind.py:25`), 06c, and PoC-7 (`poc07_evals_harness.py:26`). Each folder reaches them through a copied `sys.path.insert` loop (06a `:20`, 06b `:22`, 06c `:19`). 06c also copies five fixtures from 06a (`test_poc06c_route_switch.py:50-66` and `:172-183` vs `06a/tests/conftest.py:31-50` and `test_poc06a_tasks.py:30-43`).
Why it matters: This breaks "no `sys.path` insert in `pocs/*/tests`" and "code that outlives a PoC is in `packages/`". A later PoC depends on a PoC-6a test folder by path.
Fix: Move the two harnesses and the shared fixtures into a test package under `packages/` (next to `chassis_contracts`) as a pytest plugin, and drop the path loops. Keeps behavior: yes.
Spec: "given a clean checkout, when the PoC-6 and PoC-7 tests are collected, then no `pocs/*/tests/conftest.py` calls `sys.path.insert` and the pass counts are unchanged."
Status: open

### R6-04 [HIGH] [maintainable] [shared] pocs/poc-06a-bake-off-sidecar-lane/tests/poc06_harness.py:181
Problem: Three engine registries exist: `poc06_harness.ENGINES` (`:181-243`), `bakeoff/registry.py:46-140`, and `poc06b_kind.py:34-110`. Each stores trust and lane. The harness field `hooks_extra` (`:178`) has no reader.
Why it matters: Adding or re-trusting an engine means three edits, and nothing checks that the trust columns agree. The trust rule is a security boundary.
Fix: Derive trust and lane in the harness and the kind table from `bakeoff.registry`; keep only test hook names and pod names as extra columns; delete `hooks_extra`. Keeps behavior: yes.
Spec: "given the three tables, when they are loaded, then each shared engine has the same trust and lane because two of them read the registry."
Status: open

### R6-05 [HIGH] [docs] docs/guides/poc-06-how-it-works.md:151
Problem: The guide contradicts itself. Line 3 says section 8 ran in CI (run 38010290978, 74 passed). Line 151 says "The kind half has not run", and line 128 says the box "stays open until CI is green".
Why it matters: A reader cannot tell whether the kind evidence exists, and the 6b README says it does.
Fix: Delete the stale sentences at lines 128 and 151 and the "integration branch" clauses at 91 and 153, and fix the "Section 9" pointer on line 3. Keeps behavior: yes.
Spec: "given the edit, when `grep -n 'has not run\|stays open until CI\|integration branch' docs/guides/poc-06-how-it-works.md` runs, then it prints nothing."
Status: open

### R6-06 [HIGH] [tests] pocs/poc-06b-bake-off-remote-lane/README.md:33
Problem: 6b has no criteria test file. Open criteria 5 and 7 have no stub, and the tests that cite a criterion use a number and a paraphrase (`test_poc06b_plain_a2a_contract.py:1`, `test_poc06b_kind_engines.py:1`). The 31 offline tests in `test_poc06b_hosted_static.py` back criterion 5 but the README does not cite them.
Why it matters: `pocs/CLAUDE.md` asks each scenario test to name its criterion word for word, and 6a already does this.
Fix: Add `test_poc06b_criteria.py` with one test per criterion, reason-bearing stubs for 5 and 7, and cite the hosted static file under criterion 5. Keeps behavior: yes (adds tests only).
Spec: "given the 6b README has eight criteria, when the criteria file is collected, then eight `test_criterion_N_*` tests exist and each docstring starts with the README sentence."
Status: deferred (open exit criteria; for the PoC-6 plan)

### R6-07 [HIGH] [tests] pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_criteria.py:123
Problem: Three criterion stubs say "not done" while the README says done. Criterion 3 (`:123-126`) says the scorecard is "W4 / plan A5", criterion 7 (`:237-239`) says the ADR draft is W4, and criterion 8 (`:242-244`) points at 6b task B6, which is frozen.
Why it matters: The test named for a criterion reports the wrong status to anyone reading test output.
Fix: Reword each skip reason to name the test or note that holds the evidence. Keeps behavior: yes (making criterion 3 call the scorecard check would be a proposal).
Spec: "given the scorecard note exists, when `test_criterion_3_*` runs, then its skip reason names the test that holds the evidence."
Status: deferred (open exit criteria; for the PoC-6 plan)

### R6-08 [MEDIUM] [docs] pocs/poc-06a-bake-off-sidecar-lane/README.md:4
Problem: None of the 6a, 6b, 6c READMEs has a `Builds on:` or `Hands to:` line. The 6c README has no link to 6a or 6b. The planning doc lost the "Previous / Next" line that `007-…md:64` still has.
Why it matters: The PoC-5 to PoC-6 to PoC-7 chain, and how 6a, 6b, 6c relate, cannot be read from the READMEs.
Fix: 6a: "Builds on: PoC-3, PoC-4, PoC-5. Hands to: 6b, 6c, PoC-7." 6b: "Builds on: PoC-5 and 6a. Hands to: PoC-7 (contract v5, ADR-006)." 6c: "Builds on: 6a. Hands to: ADR-006's SLM section." Keeps behavior: yes.
Spec: "given the edit, when `grep -c '^Builds on:\|^Hands to:' pocs/poc-06*/README.md` runs, then each file prints 2."
Status: open

### R6-09 [MEDIUM] [docs] docs/planning/adr/006-agent-engines-default-supported-lanes.md:15
Problem: Status claims disagree. ADR-006 (lines 15, 73, 196) and the 6b README (lines 17, 19, 31) say the `kagent-adk` kind result "waits on CI", but the 6b README (lines 3, 30) says run 38010290978 passed it. The plan (`docs/plans/2026-10-09-poc-06-bake-off.md:85`, `:97`, `:74`) is stale and does not track 6c. 6b criterion 4 (README:32) cites the Mac run for load that 6a criterion 4 says ran in CI.
Why it matters: The open work is not written in one place, and the empty `kagent-adk` scorecard cells are on no "what is left" list.
Fix: Add the missing `kagent-adk` cells to the planning doc's "What is left", correct the ADR status and the plan, and point the plan at the planning doc's list. Keeps behavior: yes (no box or evidence changes).
Spec: "given the edit, when `grep -n 'events.v1.json\|list all 8 criteria today' docs/plans/2026-10-09-poc-06-bake-off.md` runs, then it prints nothing, and the planning doc names the `kagent-adk` cells once."
Status: deferred (open exit criteria; for the PoC-6 plan)

### R6-10 [MEDIUM] [docs] docs/guides/poc-06-how-it-works.md:27
Problem: The guide and `packages/bakeoff/README.md:47` cite the trust rule as "ADR-001 item 8". The trust rule is item 5 (`ADR-001:98`); item 8 is about managed runtimes.
Why it matters: The wrong number sends a reader to the wrong rule on a security boundary.
Fix: Change "item 8" to "item 5" in both files. Keeps behavior: yes.
Spec: "given the edit, when `grep -rn 'ADR-001 item 8' docs/guides packages/bakeoff` runs, then it prints nothing."
Status: open

### R6-11 [MEDIUM] [secure] packages/workloads/echo-claude-agent/src/echo_claude_agent/handle.py:225
Problem: A remote Sandbox pod is long-lived, and every run in it shares one uid, one `/tmp` emptyDir (`remote-claude-agent.yaml:121-127`), and one process tree. smolagents runs model-written code inside the A2A server process (`echo_smolagents/handle.py:7`).
Why it matters: One hostile run can leave a file or a background process for the next run, or read a concurrent run's files. The PoC-5 code runner closes this with one sandbox per call; these engines do not.
Fix: Use one Sandbox per run, or serve one request at a time and recycle the pod after each run. Keeps behavior: no.
Spec: "given a remote-claude-agent pod, when run 1's Bash writes `/tmp/marker` and starts `sleep 600 &`, then run 2 sees no marker and no stray process."
Status: proposal

### R6-12 [MEDIUM] [secure] deploy/compose/poc06/litellm-hosted.yaml:31
Problem: On the Mac hosted step, the chassis's `LITELLM_API_KEY` is the LiteLLM master key (`scripts/poc06_mac.sh:584-585`, `packages/bakeoff/src/bakeoff/config.py:134`). It has no route scope and no budget, and `deploy/compose/poc06/litellm.config.yaml` sets no `max_budget`.
Why it matters: ADR-001 puts the spend limit in a scoped LiteLLM key. Here only the repeat count and the provider bound the real provider key's spend.
Fix: Add a proxy-wide `max_budget` (suggested: a few USD) under `litellm_settings`, and name it in the hosted note. Keeps behavior: yes.
Spec: "given `litellm.config.yaml`, when the static test loads it, then `litellm_settings.max_budget` is a positive number."
Status: open

### R6-13 [MEDIUM] [maintainable] [shared] pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_static.py:47
Problem: 06b test modules import other PoCs' test modules, including private names: `_Params` (`:47`), `_runs`, `_steps`, `_triggers`, `_workflow` (`:48`), a fixture with `# noqa: F401` (`test_poc06b_kind_sidecar_controls.py:44`), and `traceparent` (`test_poc06b_kind_remote_controls.py:66`). PoC-5 defines `traceparent` twice with two return types (`poc05_harness.py:107`, `test_poc05_kind_remote_controls.py:69`).
Why it matters: Renaming a private helper in a PoC-5 test file breaks PoC-6b.
Fix: Move the shared helpers into a harness module with public domain names (for example `workflow_steps`), and give the two `traceparent` helpers different names. Keeps behavior: yes.
Spec: "given the move, when PoC-5 and PoC-6b are collected, then no `pocs/*/tests` module imports a `_` name from another PoC's `test_*` module, and 6b still collects 236 passed and 86 skipped."
Status: open

### R6-14 [MEDIUM] [maintainable] [shared] packages/bakeoff/src/bakeoff/evaltrial.py:1
Problem: The PoC-7 eval trial (`evaltrial.py` 261 lines, `evalreport.py` 178, `langfuse_datasets.py` 209, `golden/` about 640) is about half of the PoC-6 benchmark package. `evals/deepeval/test_golden.py:30` reads `packages/bakeoff/src/bakeoff/golden/cases.yaml`.
Why it matters: The bake-off kit and the eval gate share a package only because they started in nearby PoCs, and `packages/bakeoff/CLAUDE.md` describes only the bake-off.
Fix: Move the four modules into their own package (for example `packages/evals`) that imports `bakeoff.stack` and `bakeoff.registry`. Keeps behavior: yes.
Spec: "given the move, when the golden tests and `evals/deepeval/test_golden.py` run, then they pass and `bakeoff` imports no eval module."
Status: open

### R6-15 [MEDIUM] [simple] packages/workloads/echo-openai-agents/src/echo_openai_agents/handle.py:92
Problem: `timeout_s` is copied in four workloads (`echo_claude_agent/handle.py:96`, `echo_smolagents/handle.py:66`, `echo_pydanticai/handle.py:60`). `event(kind, **fields)` is copied in three `mapping.py` files (`echo_openai_agents:42`, `echo_smolagents:42`, `echo_claude_agent:41`), with the `_chain` and `_error` helpers.
Why it matters: A change to the budget rule or the event shape needs four edits, and every workload already depends on `workload-a2a`.
Fix: Put `event`, `timeout_s`, and the error helpers in `workload_a2a`, and import them in each workload. Keeps behavior: yes.
Spec: "given the same `ctx` with and without `budget.timeout_ms`, when each workload computes its timeout, then all return `workload_a2a.timeout_s(ctx)`."
Status: open

### R6-16 [MEDIUM] [simple] packages/bakeoff/src/bakeoff/stack.py:117
Problem: `running_stack` is 90 lines. It checks the lane, computes URLs, spawns the workload, spawns the chassis, and cleans up.
Why it matters: It breaks the 50-line rule, and the cleanup order is hard to see.
Fix: Extract `_endpoints`, `_spawn_workload`, and `_spawn_chassis`, and keep the `try`/`finally` in the generator. Keeps behavior: yes.
Spec: "given each engine and lane in `test_bakeoff_matrix.py`, when the stack starts, then argv and env are unchanged and `running_stack` is under 50 lines."
Status: open

### R6-17 [MEDIUM] [maintainable] deploy/kind/poc06/agents/agent-openai-agents.yaml:1
Problem: `agent-openai-agents.yaml` and `agent-typescript.yaml` are 208 lines each and differ in about 42. The three `chassis-*-remote.yaml` files (180 lines each) differ only in names and `clusterIP`. No doc lists these copies.
Why it matters: One hardening change to the pod spec needs five identical edits, and drift is silent.
Fix: Build one base with kustomize patches, and list any copy that stays. Keeps behavior: yes, if the rendered output is unchanged.
Spec: "given the refactor, when `kubectl kustomize deploy/kind/poc06/agents` runs, then the output equals the output before."
Status: open

### R6-18 [MEDIUM] [tests] pocs/poc-06c-pretrained-slm/tests/test_poc06c_criteria.py:182
Problem: Criterion 1 (`:182-194`) promises "only `spec.model.route` changed" but asserts `bakeoff_problems(data) == []` and prints pass counts with no assert; the README's "135 of 135 runs passed" is not gated. Criterion 2 (`:211`) always skips, so it will not turn on when the hosted results land; 6a criteria 5 to 8 (`test_poc06a_criteria.py:226-244`) do the same.
Why it matters: The evidence in the README is stronger than the asserts behind it, and an unconditional stub is easy to forget.
Fix: Assert the counts or reword the README claim; skip criterion 2 only while the hosted results file is absent, as `need_results` does. Keeps behavior: no (changes what the criterion tests assert).
Spec: "given a committed hosted-run results file, when the 6c criteria run, then the side-by-side test runs instead of skipping."
Status: proposal (open exit criteria; for the PoC-6 plan)

### R6-19 [MEDIUM] [tests] pocs/poc-06c-pretrained-slm/tests/test_poc06c_mac_static.py:297
Problem: Several "static" tests match exact text in a shell script or a note, and their names do not say so. `test_the_script_keeps_the_key_rules` (`:297-321`) regex-matches one line of `poc06_mac.sh`; `test_the_record_names_each_exception` (`test_poc06b_kind_static.py:1018`) greps a note for six phrases.
Why it matters: A harmless reformat breaks them, and the failure does not name the rule that was lost.
Fix: Rename to `..._script_text_...` or `..._note_says_...`, and check `--dry-run` output where it shows the behavior, as the neighboring tests do. Keeps behavior: yes (the rename; changing the match is a proposal).
Spec: "given `poc06_mac.sh` is reformatted with no behavior change, when the static tests run, then the key-rule test still passes."
Status: open

### R6-20 [MEDIUM] [tests] pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_static.py:250
Problem: 65 of 197 PoC-6 tests have a `for` around an assert and 24 have an `if`. Examples: `test_every_container_is_hardened` (`:250-271`) branches on `runtimeClassName`; `test_chassis_config_loads_and_matches_the_pod` (`:399-430`) holds a sidecar and a remote case.
Why it matters: A failure names the whole test, not the pod or the container that broke.
Fix: Parametrize by pod and container, and split the sidecar and remote cases. Keeps behavior: yes.
Spec: "given each container is its own case, when one container drops `readOnlyRootFilesystem`, then exactly one named case fails."
Status: open

### R6-21 [MEDIUM] [tests] pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_remote_controls.py:483
Problem: `test_a_pod_with_a_missing_profile_never_runs` is 78 lines with a 30-line manifest literal, an early `return` when admission refuses the pod, and a polling loop with `time.sleep(3)`. `test_only_the_route_changes_and_every_task_passes_both_times` (`test_poc06c_route_switch.py:187`, 47 lines, about 16 asserts, no docstring) tests three behaviors in one body.
Why it matters: The seccomp test is a fail-closed control; a reader cannot see its arrange, act, and assert, and one test id covers several failures.
Fix: Move the manifest and the poll into named helpers in `poc06b_kind.py`, split "refused at admission" from "never Running", and split the route-switch test into three. Keeps behavior: yes.
Spec: "given a live app on `big-default`, when its store entry is set to `local-small`, then `changed_paths` is `['spec.model.route', 'version']` in its own test."
Status: open

### R6-22 [MEDIUM] [tests] packages/workloads/echo-claude-agent/tests/test_echo_claude_agent_handle.py:193
Problem: Names do not read as specs: `test_smoke_task` (`:166`), `test_lookup_task` (`:193`), `test_http_status_error` (`:364`), `test_options_and_env` (`:270`, 36 lines, about 20 asserts). 110 of 197 PoC-6 tests have no docstring, including 30 of 31 in `test_poc06b_hosted_static.py`.
Why it matters: The 6b README (line 15) cites `test_lookup_task` as evidence, and the name says nothing about what passed.
Fix: Rename to `test_handle_passes_the_lookup_task_when_the_cli_replays_its_script` and similar, split the options test, and update the README line in the same change. Keeps behavior: yes.
Spec: "given the rename, when the README is grepped for `test_lookup_task`, then nothing matches and every cited name resolves."
Status: open

### R6-23 [MEDIUM] [docs] docs/guides/poc-06-how-it-works.md:134
Problem: Guide section 9 lists four Mac steps and leaves out `kind`, which `scripts/poc06_mac.sh:181` has and the planning doc tells the user to run. Sections 2 to 4 repeat `packages/bakeoff/README.md` (the engine table, the process diagram, the skip reasons). The task spec appears four times. ADR-006 repeats the 6c notes' tables and states its reopen rule six times (lines 17, 61, 99, 165, 188, 194).
Why it matters: The copies drift, and the guide's copy already dropped the one step the user must run.
Fix: Replace section 9 with a link to `deploy/compose/README.md`, link the bakeoff README for the matrix, and keep the ADR's reopen rule once under Revisit with links to the notes. Keeps behavior: yes.
Spec: "given the edit, when `grep -c 'stands only if' docs/planning/adr/006-*.md` runs, then it prints 1, and guide section 9 has no step table."
Status: open

### R6-24 [MEDIUM] [docs] pocs/poc-06b-bake-off-remote-lane/CLAUDE.md:6
Problem: 6b "Touches" lists "the `remote` cloud auth adapter", which the 6b README (line 16) and ADR-006 (line 82) put out of scope. 6a and 6b `CLAUDE.md:5` say "the exit criteria are in `README.md`", but criteria 6, 8, and the final 7 are only in the 6b README. 6c "Done means" asks for a demo in `demo/` and line 6 names "the SLM rows of the scorecard"; neither exists.
Why it matters: An agent following these files may start ruled-out work, miss two criteria, or chase a record that 6c never planned.
Fix: Drop the cloud auth adapter line, say which README holds which criteria, and record in the PoC-6 plan whether 6c's record is `demo/` or the notes. Keeps behavior: yes.
Spec: "given the edit, when `grep -n 'cloud auth adapter\|SLM rows of the scorecard' pocs/poc-06*/CLAUDE.md` runs, then it prints nothing."
Status: open

### R6-25 [MEDIUM] [docs] pocs/poc-06b-bake-off-remote-lane/README.md:3
Problem: In all three READMEs the first three lines are a 36- to 51-word Status line, a planning link, and a time box. The run command first appears at line 47 (6a), 38 (6b), and 35 (6c). 93 sentences in the PoC-6 docs run over 25 words, for example `contract-v6.md:229` (71 words).
Why it matters: This breaks the README rule (what, why, how in three sentences) and the sentence-length rule, and the status repeats the planning doc.
Fix: Open with what, why, and `make test-poc POC=06x`; cut Status to one value and a link; split long sentences at each semicolon. Keeps behavior: yes.
Spec: "given the edit, when the first three sentences of each README are read, then they name what, why, and a run command."
Status: open

### R6-26 [LOW] [secure] packages/workloads/echo-claude-agent/src/echo_claude_agent/handle.py:212
Problem: The last three CLI stderr lines are appended to the `cli_error` message after `_error` cut it to 500 characters (`mapping.py:167`). Only the token is scrubbed.
Why it matters: Temp paths, the chassis ClusterIP, and CLI stack text reach the caller with no length limit.
Fix: Append first, then cut to `MAX_MESSAGE`, and keep the full stderr in the workload log. Keeps behavior: yes.
Spec: "given a CLI that dies with three long stderr lines, when `handle` runs, then the `error` message is at most 500 characters."
Status: open

### R6-27 [LOW] [secure] deploy/kind/poc06/hosted.sh:315
Problem: `up` writes the provider Secret and the internet egress policy (`:324`, `:331`) with no trap to undo them if a later step fails. `scrub` (`:85`) matches only `sk-`, `Bearer`, and 64-hex keys.
Why it matters: A failed standalone `hosted.sh up` leaves a provider key and 443 egress in the cluster, and a key in another format passes the log tail at `:111`.
Fix: Set an ERR/EXIT trap in `up` that runs `down` unless `up` finishes, and redact the exact key value as `poc06_mac.sh:270` does. Keeps behavior: yes.
Spec: "given `set_all_keys` fails, when `hosted.sh up` exits non-zero, then Secret `litellm-provider` and NetworkPolicy `litellm-hosted-egress` are gone."
Status: open

### R6-28 [LOW] [secure] deploy/kind/poc06/run.sh:46
Problem: `build_kagent` builds kagent's Dockerfile, whose bases (`debian:bookworm-slim`, uv) are pulled by tag. `poc06-kind.yml` runs it on every push and pull request.
Why it matters: A moved tag changes what runs in the remote lane. The impact is small (gVisor, no secret in the job).
Fix: Pass digest-pinned bases as build args and record them next to `kagent/source.commit`. Keeps behavior: yes.
Spec: "given `run.sh`, when the static test reads `build_kagent`, then every base image has `@sha256:`."
Status: open

### R6-29 [LOW] [maintainable] deploy/kind/poc06/run.sh:64
Problem: Local image tags carry PoC numbers: `kagent-adk:poc06` (`:64`), `echo-*:poc06` (`:75-77`), and PoC-6 pods run `chassis:poc05` and `echo-typescript:poc05` (`agent-typescript.yaml:131`, `chassis-*-remote.yaml:48`).
Why it matters: Images that outlive a PoC carry an old PoC tag.
Fix: Set one tag variable in `run.sh` (for example `:kind`) and use it in the manifests and tests. Keeps behavior: yes.
Spec: "given the rename, when `grep -rn ':poc0[56]' deploy/kind/poc06` runs, then it prints nothing and the static tests pass."
Status: open

### R6-30 [LOW] [simple] packages/workloads/echo-claude-agent/src/echo_claude_agent/handle.py:164
Problem: Four functions are over 50 lines: `handle` in the claude agent (59) and in openai-agents (`handle.py:136`, 59), `evaltrial.run_one` (`evaltrial.py:168`, 62), and `report.render_markdown` (`report.py:42`, 59). The claude `handle` nests five levels.
Why it matters: They break the 50-line and 4-level rules.
Fix: Extract the options setup and the stream loop from each `handle`, the report writing from `run_one`, and one helper per section from `render_markdown`. Keeps behavior: yes.
Spec: "given the existing workload and bakeoff tests, when they run after the split, then the events and reports are identical."
Status: open
