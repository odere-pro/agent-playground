# Findings R5b: sandboxed, simple, maintainable, tests, docs

Session: R5 (part b of 2; part a is [findings-R5a-sandboxed-secure.md](findings-R5a-sandboxed-secure.md)). Area: `packages/code-runner/`, `packages/fake-mcp-server/`, the chassis tool gateway (`server/tool_endpoint.py`, `adapters/mcp/gateway.py`), `deploy/kind/poc05/`, `deploy/compose/SECURITY.md`, the two PoC-5 guides, the `poc-05-operate` skill, and `pocs/poc-05-sandboxed/`. PoC: `pocs/poc-05-sandboxed`.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC-5 code is small and careful, but its test support has grown into shared code that sits in the wrong place. PoC-6a and PoC-6b import `poc05_harness.py` and `poc05_kind.py` through `sys.path`, and the harness copies helpers from PoC-2 and from itself. The top risk is the admission evidence. The offline suite checks fixtures against a hand-written Python copy of the CEL rules, not against `policy.yaml`, so a deleted rule stays green in `make check`. The docs are thorough but long, repeat the same rules in six places, and the code-runner README still leads with the shared-pod mode that the per-call sandbox replaced.

R0-20 and R0-21 cover `test_poc05_pids_static.py`, and R0-13 covers the same `sys.path` pattern in `poc06b_conftest.py`. Contract v5 belongs to PoC-6 (v4 is PoC-5's), so it is left to R6.

## Findings

Most severe first. Severity: CRITICAL, HIGH, MEDIUM, LOW. Lens: simple, maintainable, tests, docs.

### R5b-01 [HIGH] [tests] pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py:999
Problem: `broken_rules` is a hand-written Python copy of the CEL rules, and `test_the_rule_model_agrees_with_each_fixture` (line 1096) and `test_every_workload_pod_passes_the_admission_rules` (`test_poc05_hardening_static.py:494`) check fixtures and manifests against that copy, not against `admission/policy.yaml`.
Why it matters: Deleting or weakening a CEL rule (say 7a or 7d) keeps every offline test green; only the kind suite would notice, and CI does not run it (R5a-03).
Fix: Pin each rule's expression text in a fixture and compare it to the loaded policy, or add a mutation test that drops one rule from the loaded policy and expects a failure; say in the docstring that the model is not the policy. Keeps behavior: yes.
Spec: "given `policy.yaml` with rule 7a deleted, when the offline admission suite runs, then at least one test fails."
Status: open

### R5b-02 [HIGH] [maintainable] [shared] pocs/poc-05-sandboxed/tests/poc05_conftest.py:26
Problem: The conftest puts the PoC-2, PoC-3, and PoC-4 test folders on `sys.path` (lines 26 to 32), and PoC-6a and PoC-6b in turn import `poc05_harness` and `poc05_kind` the same way (`test_poc06b_remote_engine_contract.py:45`, `poc06b_kind.py:24`).
Why it matters: This breaks "code that outlives a PoC is in `packages/`" and "no `sys.path` insert in `pocs/*/tests`", and each later PoC must extend the path list by hand (same pattern as R0-13).
Fix: Move the reusable pieces (the in-pod `PROBE`, the kubectl helpers, the remote-lane socket harness) into one installed test-support package under `packages/`, and delete the path loops. Keeps behavior: yes.
Spec: "given a PoC-6b test, when it imports the kubectl probe helpers and the remote-lane harness, then it imports them from the package with no `sys.path` edit in any conftest."
Status: open

### R5b-03 [HIGH] [docs] packages/code-runner/README.md:3
Problem: The first sentences say code-runner "runs in an agent-sandbox `Sandbox` pod", and line 56 says "One long-lived pod serves every call", but PoC-5 deploys the per-call dispatcher, which first appears at line 72; the opening never says how to run it.
Why it matters: The shared-pod design was replaced after the 2026-10-09 security review (`docs/guides/poc-05-how-it-works.md:85`: one call could restart the pod and leave `/tmp` files for the next caller), so a reader of the top may deploy the unsafe mode.
Fix: Rewrite lines 3 and 4 to say each call gets a fresh gVisor sandbox from the warm pool through `code-runner dispatch`, link "Run it" (line 103), and mark "Isolation between calls" (line 54) as the mode without the dispatcher. Keeps behavior: yes.
Spec: "given the README, when a reader reads lines 1 to 5, then they name `code-runner dispatch` and a sandbox per call."
Status: open

### R5b-04 [MEDIUM] [simple] packages/code-runner/src/code_runner/dispatch.py:424
Problem: `create_dispatch_server` repeats `create_server` (`server.py:152`): the same instructions, `ToolAnnotations`, `run_python_tool` signature with its `Annotated` fields, docstring, `/health` route, and `RunnerError` to `ToolError` wrapper.
Why it matters: This breaks "no second helper that does what one already does"; a schema change must be made twice, and LiteLLM could see two tool definitions.
Fix: Add one `register_run_python(mcp, limits, call)` in a shared module and have both factories use it. Keeps behavior: yes.
Spec: "given the in-pod server and the dispatcher, when each lists its tools, then the `run_python` definitions are equal."
Status: open

### R5b-05 [MEDIUM] [simple] packages/code-runner/src/code_runner/server.py:157
Problem: `create_server(runner=run_python)` and the `Runner` Protocol (lines 39 to 42) are an unused option; no caller in `src`, tests, or `pocs/` passes `runner=`.
Why it matters: This breaks "no unused option" and "no abstraction with one caller", and adds a seam to a security-critical tool.
Fix: Delete `Runner` and the `runner` parameter, and call `run_python` directly. Keeps behavior: yes.
Spec: "given `create_server()`, when `run_python` is called, then it runs through `code_runner.runner.run_python` as before."
Status: open

### R5b-06 [MEDIUM] [simple] packages/chassis/src/chassis/server/tool_endpoint.py:126
Problem: `mount_tool_endpoint` is 53 lines and nests six levels (`def lifespan`, `async with`, `try`, `async with`, `try`, `for`), with two closures inside the lifespan.
Why it matters: This breaks "functions under 50 lines" and "nesting at most 4 levels" in the start and stop order, the fragile part.
Fix: Pull the lifespan body into a module-level `@asynccontextmanager _serve_tools(host, endpoints)` and make `run_of` and `run_key_for` plain functions of `host.state`. Keeps behavior: yes.
Spec: "given a host app with two mounted listeners, when its lifespan runs, then the tools port starts once, `/mcp` answers 503 before and after, and each endpoint's inner app is set during."
Status: open

### R5b-07 [MEDIUM] [maintainable] packages/chassis/src/chassis/server/tool_endpoint.py:76
Problem: `_start_tools` and `_stop_tools` probe the port with `getattr` and `hasattr` for `refresh`, `start`, `aclose`, and `on_refresh_failed`, and assign with `# type: ignore[attr-defined]` (line 87); `ToolPort` (`ports/tool.py:58`) does not declare this lifecycle.
Why it matters: A rename in `McpGatewayTools` (`gateway.py:277`) turns the check false with no error, and the ignore hides the contract.
Fix: Add a `runtime_checkable` `ManagedToolPort` Protocol in `ports/tool.py`, check it with `isinstance`, and drop the ignore. Keeps behavior: yes.
Spec: "given a tools port whose first refresh raises, when the endpoint starts, then `chassis.tools.refresh_failed` is counted once and `/mcp` opens with an empty list."
Status: open

### R5b-08 [MEDIUM] [simple] packages/chassis/src/chassis/adapters/mcp/gateway.py:98
Problem: `_JSON_TYPES`, `_matches`, and `_argument_problem` (lines 87 to 128) are a second JSON Schema checker beside `check_arguments` in `fakes/tool.py:24`, and they differ on `anyOf`, bool versus int, nested properties, and `enum`.
Why it matters: This breaks "no second helper"; the fake and the real adapter can accept different arguments while the shared contract suite passes.
Fix: Keep one checker in a neutral module (`core` or `ports`, no SDK) and have both import it. Keeps behavior: no (nested and `enum` checks start to run in the adapter).
Spec: "given a schema with a nested property of the wrong type, when the fake and the gateway adapter get the same arguments, then both refuse with `bad_arguments`."
Status: proposal

### R5b-09 [MEDIUM] [maintainable] packages/fake-mcp-server/src/fake_mcp_server/server.py:30
Problem: `GLOSSARY` and `ACRONYMS` (lines 30 to 51) are byte copies of `chassis/fakes/tool.py:158` and `:194`, joined only by comments; no test compares them.
Why it matters: The import rule forces the copy, but this breaks "duplicated config is listed, not silently copied", and an edit on one side makes the two bindings of the tool contract disagree.
Fix: Add a drift test in `packages/chassis/tests/test_tool_gateway_contract.py` asserting both pairs are equal. Keeps behavior: yes.
Spec: "given a term added to one `GLOSSARY` only, when the chassis tests run, then the drift test fails naming the key."
Status: open

### R5b-10 [MEDIUM] [simple] [shared] pocs/poc-05-sandboxed/tests/poc05_kind.py:290
Problem: `node_sh` (line 290) and `node_run` (line 305) have byte-identical bodies, and the exec-probe-parse logic in `probe` (line 276) is copied as `probe_node` and `probe_python` in `poc06b_kind.py:222` and `:243`; `tcp()` (line 400) builds a check dict and opens no connection, while the probe script's own `tcp` connects.
Why it matters: This breaks "no second helper" and "helpers have domain names"; "reads only" in `node_sh` is a claim nothing enforces, and a parse fix must land in three places.
Fix: Keep one `node_sh`, add one `exec_probe(namespace, pod, container, argv, checks)` for all three probes, and rename `tcp` to `tcp_check`. Keeps behavior: yes.
Spec: "given a check list, when `exec_probe` runs it with any interpreter, then one result per check comes back in order, and a non-zero exit fails with the stderr tail."
Status: open

### R5b-11 [MEDIUM] [maintainable] [shared] pocs/poc-05-sandboxed/tests/poc05_harness.py:163
Problem: `_serving`, `_socket_folder`, and `app_on_socket` (lines 163 to 218) serve an ASGI app on a Unix socket, which `on_unix_socket` in `poc02_harness.py:485` already does; `_bundle` (line 448) also copies `bundle_for` (`poc02_harness.py:458`).
Why it matters: This breaks "no second helper"; two start-up waits and two cleanup paths must stay in step.
Fix: Put one async and one sync wrapper around a single helper in the package from R5b-02. Keeps behavior: yes.
Spec: "given an app, when it is served on a Unix socket through either wrapper, then the path is under 104 bytes, the app answers, and the folder is removed on exit."
Status: open

### R5b-12 [MEDIUM] [maintainable] deploy/kind/poc05/run.sh:67
Problem: The same `kctl`, `log`, and `die` block and the `kind-poc05` and namespace constants are copied into `kind/run.sh:28`, `poc05/run.sh:67`, `platform/seed.sh:43`, `install-gvisor.sh:34`, `poc06/seed.sh:50`, `poc06/hosted.sh:77`, `poc06/run.sh:97`, and `poc05_kind.py:33`.
Why it matters: This breaks "config duplicated across `deploy/` is listed, not silently copied"; a change to the context or the log format needs seven edits.
Fix: Add `deploy/kind/lib.sh` (context, namespaces, `kctl`, `log`, `die` with a prefix variable) and `source` it from each script. Keeps behavior: yes.
Spec: "given each script, when `lib.sh` is sourced, then `kctl` pins its context and `die` exits 1 with the script's prefix."
Status: open

### R5b-13 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py:220
Problem: `test_policy_has_every_rule_and_the_params_check` has no docstring and loops over `validations`, checking only `reason == "Forbidden"` and that the messages line up.
Why it matters: README criterion 7 cites it as offline evidence that an untrusted workload or a third-party image is blocked, but it proves only that message strings exist.
Fix: Give it a one-line docstring naming criterion 7 and what it covers, parametrize the loop, and point the README at the test from R5b-01. Keeps behavior: yes.
Spec: "given the policy, when rule 2 is removed, then the criterion 7 offline test fails and names rule 2."
Status: open

### R5b-14 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py:241
Problem: `test_h30_remote_reaches_no_other_pod` asserts inside `for ... in zip(checks, refused, strict=False)`, and `refused` is one item shorter than `checks`.
Why it matters: One failing target hides the rest, and a short probe result gives a loop that checks nothing and passes.
Fix: Parametrize by target with a module-scope fixture that probes once, keep the 8091 control as its own paired assertion, and use `strict=True`. Keeps behavior: yes.
Spec: "given a remote pod and six live targets, when it connects to each, then each connect times out and 8091 connects."
Status: open

### R5b-15 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py:401
Problem: `test_python_child_burst_leaves_the_dispatcher_up` wraps its asserts in `if burst.is_error ... else ...`, so `sandbox_lost` and a normal result both pass.
Why it matters: README criterion 8 cites it as the fork-burst evidence, and the branch means the test does not say which outcome the design promises.
Fix: Split it into "the burst is isolated" and "the dispatcher is unchanged", and drop the branch. Keeps behavior: yes.
Spec: "given a call that forks about 31 children, when it ends, then the dispatcher pod uid and restart count are unchanged and another call works."
Status: open

### R5b-16 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_seed_static.py:214
Problem: `test_platform_images_are_pinned` has `for`, `if`, `elif`, and `else` around its asserts, and `UNPINNED_UNTIL_T19` (line 35) quietly exempts `postgres:17.6-alpine`.
Why it matters: The README maps "third-party images by digest" to this test, the exemption is invisible, and a failure names only the first bad image.
Fix: Parametrize over the images and make postgres a `pytest.param(..., marks=xfail(strict=True))`. Keeps behavior: yes.
Spec: "given each third-party platform image, when its tag is read, then it ends in `@sha256` (postgres is a strict xfail until T19)."
Status: open

### R5b-17 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_hardening_static.py:241
Problem: `test_every_container_is_hardened` has no docstring and checks nine behaviors in one `for c in pod.containers()` loop, and `test_pod_has_no_token_and_no_host_access` (line 205) skips the dispatcher with an `if ... continue`.
Why it matters: README scope item 1 cites this one test for five controls, a failure names one assert at a time, and the dispatcher exemption is a hidden string compare.
Fix: Split it into parametrized tests over `(pod, container)` pairs, such as `test_container_drops_all_capabilities`, each with a one-line docstring, and take the exemption from the fixture. Keeps behavior: yes.
Spec: "given each container in every PoC-5 pod, when its securityContext is read, then capabilities drop is `[ALL]` and none are added."
Status: open

### R5b-18 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py:42
Problem: None of the 27 tests in this file has a docstring; only the module docstring names criterion 7, and the cited tests at line 105 and line 234 name no criterion.
Why it matters: `pocs/CLAUDE.md` asks a scenario test to name its exit criterion, and the README cites these tests as evidence.
Fix: Add a one-line docstring to each cited test ("Criterion 7: ..."). Keeps behavior: yes.
Spec: "given `spec.trust: untrusted` in the sidecar lane, when the config loads, then it is refused."
Status: open

### R5b-19 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py:465
Problem: `test_existing_coverage_is_still_there` parses other test files with `ast` and checks that names exist, and README criterion 1 cites it as offline evidence for "the hostile suites run in CI".
Why it matters: It proves no behavior; a renamed test that does nothing keeps it green.
Fix: Point criterion 1 at the hostile tests that run the behavior (the `test_h08`, `test_h13`, `test_h17`, `test_h29` families) and keep the name guard out of the README evidence. Keeps behavior: yes.
Spec: "given criterion 1 in the README, when a reader opens each cited test, then each one exercises a refusal next to its allowed control."
Status: open

### R5b-20 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py:182
Problem: `test_nobody_but_the_admin_reads_secrets` and `test_submitter_cannot_open_network_edges` (line 187) loop over namespaces around asserts and have no allowed control.
Why it matters: The PoC-5 `CLAUDE.md` says a refusal check always has a paired allowed control; a `can_i` that always says "no" passes both.
Fix: Parametrize by namespace and resource, and add one allowed assert each (cluster-admin may read secrets; the platform role may create networkpolicies). Keeps behavior: yes.
Spec: "given the submitter, when it asks `can-i get secrets` in each workload namespace, then no, and cluster-admin gets yes."
Status: open

### R5b-21 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py:144
Problem: `test_fixture_outcome_matches_its_header` has no docstring and branches on `if fixture.expect == "admitted"` (line 146), so one test covers two opposite behaviors.
Why it matters: README criterion 7 says "refused with its message, next to an admitted twin", but the kind test never checks the pairing, and its failure id does not say which side broke.
Fix: Split it into `test_rejected_fixture_is_refused_with_its_rule_message` and `test_admitted_twin_is_admitted`, each parametrized, with a criterion 7 docstring, and update the README lines that cite it. Keeps behavior: yes.
Spec: "given a rejected fixture and its twin, when each is applied with `--dry-run=server`, then the first is refused with its message and the twin is admitted."
Status: open

### R5b-22 [MEDIUM] [tests] [shared] pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py:925
Problem: Two test files break the 800-line rule: `test_poc05_admission_static.py` (1131 lines) holds the policy, RBAC, fixture, and rule-model code, and `test_poc05_netpol_static.py` (955 lines) holds a model builder, checks, and mutation tests; `hardening_static` imports `_Params` and `broken_rules` from the admission test file, and five files each define their own manifest loader (`hardening_static.py:56`, `admission_static.py:103`, and others).
Why it matters: The rule model is a library inside a test file, test files import each other, and the loaders can drift.
Fix: Move the admission model, the netpol model, and one `load_manifests` / `containers_of` / `secret_names_of` set into harness modules, and split the tests by topic. Keeps behavior: yes.
Spec: "given the admission rule model in a harness module, when the hardening and admission tests import it, then no test file imports another test file."
Status: open

### R5b-23 [MEDIUM] [docs] pocs/poc-05-sandboxed/README.md:7
Problem: The README has no `Builds on:` and no `Hands to:` line, and its first sentences after the header give flag status, not what PoC-5 is, why, or how to run it (the run command is at line 49).
Why it matters: This breaks two rubric rules; a new reader cannot place PoC-5 in the chain without the planning doc.
Fix: Add `Builds on: PoC-2 (the a2a-sdk client, the sidecar lane) and PoC-4 (contract v3, StatePort, EventPort)` and `Hands to: PoC-6 (6b runs engines in this remote lane)`, then one sentence each for what, why, and `make test-poc POC=05`. Keeps behavior: yes.
Spec: "given the README, when `grep -c '^Builds on:\|^Hands to:'` runs, then it prints 2, and lines 1 to 10 hold `make test-poc POC=05`."
Status: open

### R5b-24 [MEDIUM] [docs] pocs/poc-05-sandboxed/README.md:37
Problem: Criterion 3 is one 80-word sentence chained with `·` and colons; lines 7, 35, and 42 are 80 to 125 words; and the CI evidence string (run 37942450871, `22 passed, 1 xfailed in 171.79s`) is pasted in both line 28 and line 35.
Why it matters: This breaks "sentences under about 20 words", "one idea per sentence", and "a doc says a thing once"; the flag reasons are lost in test ids.
Fix: Give each bullet a short claim and a short flag reason, move test ids to a nested evidence list, and keep the CI run text in one place. Keeps behavior: yes.
Spec: "given the README, when `grep -c '22 passed, 1 xfailed in 171.79s'` runs, then it prints 1, and no sentence in lines 7 to 44 outside code spans is over 35 words."
Status: open

### R5b-25 [MEDIUM] [docs] docs/guides/poc-05-how-it-works.md:472
Problem: The guide says "Criterion 1's kind half is not in CI" and the remote-lane workflow "runs by hand only", but `.github/workflows/remote-lane.yml` runs on push and pull request, and the README (line 35) cites a green run.
Why it matters: The PoC-5 explainer tells readers CI does not gate the remote lane when it does.
Fix: Replace the paragraph with one sentence: the remote-lane kind suite runs in CI on push and pull request, the sidecar kind suite runs by hand; link README criterion 1. Keeps behavior: yes.
Spec: "given the guide, when `grep -n 'Not in CI yet\|runs by hand only'` runs, then it prints nothing."
Status: open

### R5b-26 [MEDIUM] [docs] .claude/skills/poc-05-operate/SKILL.md:52
Problem: The skill gives "Last good: `90 passed, 2 skipped`" (line 52) and "`17 passed`" (line 58), but the close runs show `150 passed, 2 skipped, 2 xfailed` and `24 passed, 2 xfailed` (`notes/2026-10-09-close-runs.md:166`, `:187`), and line 54 says "what CI will run".
Why it matters: An operator who compares a fresh run against these numbers will think tests are missing or a run regressed.
Fix: Link the close-runs note for the last good numbers instead of copying them, and change "will run" to "runs". Keeps behavior: yes.
Spec: "given the skill, when `grep -n '90 passed\|17 passed\|CI will run'` runs, then it prints nothing."
Status: open

### R5b-27 [MEDIUM] [docs] deploy/compose/SECURITY.md:16
Problem: Section 1 says the master-key-as-chassis-key debt "must be paid by PoC-5", and the checklist (line 130) still asks for it, but Compose still uses the master key (`deploy/compose/.env.example:7`) and section 8, "Exceptions recorded by PoC-5" (lines 100 to 107), does not list it.
Why it matters: A reviewer reads a deadline that has passed, with no exception and no owner.
Fix: In section 8, record that the debt is paid on kind (one virtual key per service) and open on Compose, with an owner, and link it from line 16. Keeps behavior: yes.
Spec: "given SECURITY.md, when a reader reads section 8, then it names the master key on Compose with an owner."
Status: open

### R5b-28 [MEDIUM] [docs] pocs/poc-05-sandboxed/notes/2026-10-02-mac-kickoff.md:7
Problem: The note says "T10, the in-pod probe workload, is dropped", and `KICKOFF.md:3` says "T10 is decided (dropped)", but `notes/2026-10-09-t10-probe-exception.md:7` changed it to "work in progress, owner the user".
Why it matters: The PoC-5 `CLAUDE.md` (lines 3 and 7) sends agents to the mac-kickoff note for decisions, so they read one that no longer holds.
Fix: Add an "Update, 2026-10-09" line under each title that links the T10 note, as `notes/2026-10-02-h11-queue-exception.md:3` does. Keeps behavior: yes.
Spec: "given the mac-kickoff note and `KICKOFF.md`, when a reader reads their first five lines, then each links `2026-10-09-t10-probe-exception.md`."
Status: open

### R5b-29 [MEDIUM] [docs] pocs/poc-05-sandboxed/CLAUDE.md:18
Problem: "One agent at a time on Docker or kind" is in six docs (`deploy/CLAUDE.md:11`, this file, `SKILL.md:15`, `poc-05-runbooks.md:287`, `deploy/README.md:107`, `demo/README.md:9`), and "every kubectl passes `--context kind-poc05`" in five; line 28 also says `make kind-poc05` works "once the target exists" though it is at `Makefile:59`, while the skill uses `run.sh up`.
Why it matters: This breaks "a doc says a thing once"; the copies already differ in wording, and readers pick different bring-up commands.
Fix: Let `deploy/CLAUDE.md` own both rules and the skill own bring-up; replace the copies with one link line and drop "once the target exists". Keeps behavior: yes.
Spec: "given the repo, when `grep -rln 'one agent at a time' deploy pocs/poc-05-sandboxed docs/guides .claude/skills/poc-05-operate` runs, then it lists only `deploy/CLAUDE.md`."
Status: open

### R5b-30 [LOW] [docs] pocs/poc-05-sandboxed/README.md:18
Problem: The README uses ids it never defines or links: H32 and the `test_h01` to `test_h30` names, "038 X-1a" (line 39), and "security review F10" (lines 7 and 37); it has no link to the threat model.
Why it matters: The README is the human entry point, and the H ids are defined only in `notes/2026-10-02-threat-model.md`, which only the agent-facing `CLAUDE.md` links.
Fix: Add one line under the header that links the threat model (H ids), the security review (F ids), and says NNN X-n are backlog ids. Keeps behavior: yes.
Spec: "given the README, when `grep -c 2026-10-02-threat-model.md` runs, then it prints at least 1."
Status: open
