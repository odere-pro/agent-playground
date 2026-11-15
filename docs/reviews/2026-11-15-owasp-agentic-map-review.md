# Review of the OWASP agentic map (132 CH-13), 2026-11-15

Reviewer: the `platform-security` agent. Reviewed: the table "The OWASP agentic threats mapped to the controls (CH-13)" in [`docs/specs/security.md`](../specs/security.md), against the code and deploy files on `main` at `cdd7383`. Vault note used: `~/Git/sdlc-vault`, `wiki/security/Threat model.md` (it lists ASI01 to ASI09, as the map says). How this closes was decided by the user on 2026-11-15 (PR 218): this note records the review, and the user signs off below.

Every path the table cites exists on `main`. "Live" means a kind or stack run with a recorded result. "Offline" means a test in the gate or in `make test-pocs-done`. "Prose" means only a doc says it.

## Does the review need 026 CH-4?

No for the review itself. The review reads the map against `main` and records what is real. Nothing in it waits on 026.

Yes for part of the evidence. These rows lean on open 026 boxes, so they stay **provisional** until 026 closes them:

- ASI02: one service's key cannot use another service's tools (026, open).
- ASI03: one service's key cannot use another's models or cache keys; the key has a `duration` and a `seed.sh rekey` (026, both open).
- ASI05: `agent-lib`'s NetworkPolicy, on a CNI that enforces it, blocks the metadata service, the Kubernetes API, outside DNS, and the public port on localhost (026, open). PoC-5 proved this live for its own manifests (`deploy/kind/poc05/`), not for the chart we ship.
- ASI10: revoking one service's key, grants, and label cuts it off alone (026, open).

ASI01 and ASI08 are also provisional on 022 H-6's open boxes: the live Prompt Guard 2 flag, and the rate limit across two pods.

## The review, row by row

| ID | Control real? | Where | Evidence kind | Gap left |
| -- | ------------- | ----- | ------------- | -------- |
| ASI01 | Yes. Stages on by default, `fail_closed` by default; URL allow-list default deny. Model-based; not the boundary | `packages/chassis/src/chassis/server/config.py` (`GuardrailsSpec`), `server/guard.py`, `server/output_filter.py` | Offline: the stage tests; the red-team runner's offline parts run a guardrail stub, not the classifier. No live classifier run | The real Prompt Guard 2 flag is 022's open box, not named in the row. `cloud` allows `guardrails: none` (`profiles.py` refuses only `fake`, `memory`, `static`) |
| ASI02 | Yes. The hard limit is LiteLLM's per-key grant; the chassis adds no list of its own (`gateway.py` header) | `packages/chassis/src/chassis/adapters/mcp/gateway.py`; `deploy/kind/poc05/platform/seed.sh` | Offline (fakes) and live (kind tool gateway, remote-lane run 42299094368) | Cross-service tools are 026's open box; the row says "None". The grant evidence (`test_poc05_seed_static.py`, `test_poc05_kind_hardreq1.py::test_mcp_gateway_refuses_the_workload_without_the_chassis_key`) is not cited |
| ASI03 | Yes. Chassis-only keys, OIDC, per-remote token, no SA token | `chassis/server/security.py`, `server/remote_auth.py`, `deploy/kind/poc05/` | Offline and live (PoC-5 criteria 3, 4) | Provisional on 026: cross-service use, key expiry and rekey. The loopback proxy keys calls by trace id alone (026 "What"); in `cloud` the uncorrelated cap is 0, so a run must be in flight. SA token test `test_poc05_kind_sidecar_controls.py::test_h02_no_service_account_token_in_the_workload` not cited |
| ASI04 | Partly. Base images by digest (Dockerfile `ARG`s), actions by SHA, `uv.lock` with hashes, Agent Scan config. Cosign signs in the template CI only | `deploy/agent-scan/`, `packages/agentctl/src/agentctl/template/project/.github/workflows/ci.yml.jinja` | Offline static checks; Agent Scan never run for real | Nothing verifies a signature at admission or deploy (080 R-7 says so; 056 CH-7's open box owns it). The chart defaults fail rule 8, image by digest (026, open). Neither is in the row |
| ASI05 | Yes on kind for PoC-5's manifests. The chart's NetworkPolicy is render-tested only | `packages/code-runner`, `deploy/helm/agent-lib/templates/_networkpolicy.tpl`, `deploy/kind/poc05/` | Offline and live (PoC-5) | The row says "None". Open: the dispatcher bearer test is not run on kind (PoC-5 README, the unnumbered box after criterion 3); the in-pod hostile workload T10 is not built (055 CH-6 open box; PoC-5 criterion 8 flagged); `agent-lib` on an enforcing CNI (026) |
| ASI06 | Partly. Memory refused in `cloud` with auth off; content not screened | `chassis/server/config.py` (`MemorySpec`, line 692) | Offline (`test_memory_config.py`) | As the row says: 140 CH-20 box (memory screening), 125 L-1, 113 O-9 |
| ASI07 | Yes. Remote token, one key per direction | `chassis/server/remote_auth.py` | Offline (`test_remote_two_keys.py`) and live (PoC-5 criterion 6) | The row is stale: "one key per direction: 055 CH-6" is delivered (055, two-key box ticked, PR 216). mTLS (038 X-1a) and 098 O-10 stay |
| ASI08 | Yes in the chassis. The fixed limit is the key's `max_budget: 1.0`, `budget_duration: 1d`, `rpm_limit: 600` | `deploy/kind/poc05/platform/seed.sh` lines 199 to 200; `deploy/compose/poc08-litellm/stack.sh` lines 97 to 100; `chassis/server/rate_limit.py` | Offline for the chassis; the key limits are prose (no offline test on `seed.sh` or `stack.sh`) | The row omits the fixed key limits. The rate limit across two pods is 022's open box. 107 O-6 stays |
| ASI09 | Yes, hygiene | `chassis/adapters/ai_marking.py` | Offline | As the row says: 109 O-8, 110 C-6 |
| ASI10 | Yes on kind and in the chart render | `deploy/helm/agent-lib/templates/_admission.tpl`, `deploy/kind/poc05/admission/` | Offline (static, render) and live (kind admission) | Provisional on 026: the revoke step. Registry gate and trust levels (077 C-1, 080 R-7), the audit record (072 C-3) stay |

## Findings by severity

No CRITICAL finding. No row claims a control that is absent from the code.

### HIGH

1. **ASI04: signatures are made, never checked.** The rules ask for signed images. Nothing verifies a signature at admission or deploy. The chart defaults also fail rule 8 (image by digest). Both have owners (056 CH-7, 026 CH-4). The row lists neither. Closes when: the row names both, and 056's box lands with a kind test that refuses an unsigned image.
2. **ASI05: "None" is not true yet.** Three live boxes are open: the dispatcher bearer on kind, the T10 in-pod hostile workload (owned by the user), and `agent-lib`'s NetworkPolicy on an enforcing CNI. The PoC-5 live proof covers `deploy/kind/poc05/` manifests, not the chart. Closes when: the row lists the three, and `test_poc05_kind_code_runner.py::test_dispatcher_needs_its_bearer_and_litellm_sends_it` and the 026 netpol box pass on kind with `agent-lib`.

### MEDIUM

3. ASI02 and ASI10 say "None" or omit open 026 boxes (cross-service tools, revoke).
4. ASI03 omits key expiry and rekey (026), and the trace-id-only loopback proxy (001 DEC-1, suggested per-run credential).
5. ASI08 omits the fixed key budget and rate limit, and no offline test asserts them in `seed.sh` or `stack.sh`. suggested: one static test that every minted key has `max_budget`, `budget_duration`, and `rpm_limit`.
6. ASI01 counts a stub as evidence and omits 022's open Prompt Guard 2 box. Model-based, so not the boundary.

### LOW

7. ASI07's two-key gap is stale; it is delivered.
8. Actions are SHA-pinned today, but no test keeps them so (`actionlint` does not check pins). suggested: a test that every `uses:` ends in a 40-character SHA.
9. "Images pinned by tag" understates it: the bases are pinned by digest, and `test_poc08_litellm_static.py::test_dockerfile_bases_are_pinned_by_digest` shows it.
10. A write tool's `idempotency_key` rests on the server's `readOnlyHint` (`gateway.py`, line 174; no hint fails safe). A lying server skips it. It is replay hygiene, not misuse control.
11. `cloud` accepts `guardrails: none`. suggested: refuse it in `cloud`, as `auth: none` is.

## Hostile-suite cases that apply

From `pocs/poc-05-sandboxed/README.md`, "Exit criteria": criterion 4 (ASI03), criterion 5 (ASI05, metadata and API, PoC-5 manifests), criterion 6 (ASI07), criterion 7 (ASI10), criterion 8 flagged with T10 (ASI05), and the open dispatcher-bearer box (ASI05).

## Sign-off

The user signs here. This closes the last box of 132 CH-13.

- Name:
- Date:
- Decision (accept / accept with conditions / reject):
- Conditions, if any:
