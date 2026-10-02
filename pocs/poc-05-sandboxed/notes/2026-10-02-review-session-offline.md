# PoC-5 review of the offline session work, 2026-10-02

Read-only `reviewer` pass over `git diff c847a03..d2f492d` (offline tests, CI workflow, contract v4, H11 note, blind-spots note, backlog-changes draft, how-it-works guide). Verdict: **retry**, one blocking finding. Fixes are tracked below each finding.

## Blocking

**B1. `test-remote` selects no remote-lane or code-runner test.** `deploy/kind/poc05/run.sh` runs `-k "remote or code_runner"`. Collected: `4/405`, all `test_poc05_kind_admission.py::test_fixture_outcome_matches_its_header[rule5-remote-shape/...]`, matched only by the word "remote" in the fixture id. The kind job would go green without a workload in the remote lane. `test_poc05_ci_wiring.py` checks only that the string is present, and its trigger rule ignores the second condition in the workflow header (the kind remote tests exist). Fix: select by file path; collect that selection offline and assert it holds at least one remote-lane or code-runner test and no admission test; make the push trigger also need those files.

## Optional

- **O1.** `notes/2026-10-02-blind-spots.md:35` says no PID static test exists; `test_poc05_pids_static.py` does. Cite it.
- **O2.** `docs/contracts/contract-v4.md:253,330` say none of the admission CEL ran on a cluster. The bring-up note (lines 128-129) records typeChecking `{}` and the rule-1 canary on kind. Say only the per-rule kind test has not run.
- **O3.** `docs/guides/poc-05-how-it-works.md:75,246` call the probe pods "not built yet". T10 is dropped (decision 2026-10-02); criterion 8 is "partly shown".
- **O4.** `test_poc05_hostile_offline.py` H08: the refusal comes from the fake tool port. Name it as the stand-in for the gateway's allow-list, as the H29 docstring does.
- **O5.** `test_poc05_hostile_offline.py:383` asserts `>= 400`; the docs say 500 (`model_proxy.py:536`). Assert `== 500` so a change to the open question fails the test.
- **O6.** `test_poc05_ci_wiring.py:70` detects only a quoted x86_64 pin; the aarch64 pin is unquoted. Accept an optional quote.
- **O7.** `blind-spots.md:31` says the idempotency key goes as an argument and in `_meta`. `gateway.py:325` adds the argument only when the tool's schema declares it.
- **O8.** `remote-lane.yml` failure step prints every pod log, LiteLLM's included; LiteLLM's 401 lines echo key fragments (bring-up item 2). Keys are per run, so the risk is low. Exclude LiteLLM's log or redact.
- **O9.** kind and kubectl are checked against a sha256 fetched from the same release at run time. Pin the values in the workflow env, as `install-gvisor.sh` does for gVisor.

## What passed

The 4 new test files: 32 passed. `make test-poc POC=05`: 340 passed, 65 skipped. ruff, `make type` (276 files), `make planning-check`: clean. Every refusal in the new tests (H08, H13, H15/H16, H29 route and budget) asserts its paired control in the same test. No secret in any file or argv. Action pins match `ci.yml`. 22 claims spot-checked against source: 18 true, 4 wrong (O1, O2, O3, B1), 1 overstated (O7).
