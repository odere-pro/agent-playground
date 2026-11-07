# What is left in the PoC track

Every open item from PoC-1 to PoC-9, in one list, grouped by who can do it.
For: Oleksandr (epic owner) and the delivery team. As of 2026-11-06, at `58bbd65` on `main`.
Sources: the "What is left" section of each PoC doc in this folder, each PoC README in `pocs/`, and each PoC's `notes/backlog-changes.md`. This file replaces none of them; they keep the evidence.

## Where each PoC stands

No PoC is left to build. All nine have code, tests, and CI. Four are still `in progress`, and only because they wait on runs or settings that only the user can do.

| PoC | Status | What keeps it open |
| --- | ------ | ------------------ |
| [1](001-PoC-1-walking-skeleton.md), [2](002-PoC-2-two-engines-one-contract.md), [3](003-PoC-3-one-interface-every-client.md) | Done | Nothing |
| [4](004-PoC-4-stateless-scalable.md) | Done, criterion 4 flagged | Throughput at 4 pairs, moved to [122 X-5](../issues/122-X-5-load-tests.md) |
| [5](005-PoC-5-sandboxed.md) | Done, criteria 3 and 8 flagged | MinIO through the chassis; the T10 probe workload. Criterion 1 closed on 2026-11-06: the sidecar hostile suite runs in CI |
| [6a and 6b](006-PoC-6-framework-bake-off.md) | In progress | The hosted big-model run on the Mac (criterion 5) |
| [6c](006c-PoC-6c-pretrained-slm.md) | In progress | The big-model half of the side-by-side numbers, from the same Mac run |
| [7](007-PoC-7-cross-cutting-decisions.md) | In progress | The Mac run, a `SNYK_TOKEN` secret, a look at the Langfuse UI, and applying its backlog changes |
| [8](008-PoC-8-build-cross-cutting.md) | Done, offline evals flagged | `poc08-evals` is not a required check on `main` |
| [9](009-PoC-9-agent-mvp-template.md) | Done, criteria 3 and 6 flagged | Criterion 3 carries the PoC-4 and PoC-5 flags; criterion 6 had no trial by a person |

The Agent MVP done-when list in [000-plan.md](000-plan.md#agent-mvp-done-when) is audited in the [PoC-9 done-when audit](../../../pocs/poc-09-agent-mvp-template/notes/2026-10-30-done-when.md): 14 of 17 met. Rows 6, 7, and 9 are the PoC-4 and PoC-5 flags below.

## 1. Runs on the user's Mac

The provider key and Hugging Face access stay on the Mac, so only the user can do these. One Mac session clears most of the open PoCs.

- [ ] **The hosted big-model run.** `make poc06-mac ARGS="--only hosted --only kind"`. It closes 6a and 6b criterion 5 (token overhead against plain Python per engine) and the big-model half of 6c criterion 2. Then: record the numbers in the [scorecard](../../../pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md), check the default in [ADR-006](../adr/006-agent-engines-default-supported-lanes.md), and add the result to 058 CH-8 and 008 H-14.
- [ ] **Optional: SLM latency with Metal (6c).** `make poc06-mac ARGS="--only slm"`. The CI numbers are CPU only.
- [ ] **The PoC-7 run.** The red-team set with Llama Prompt Guard 2 in place of the pattern stand-in (today 12 of 18 blocked); LiteLLM's real `presidio` hook; the encoder against the hosted judge (agreement, cost, and latency per gated call). Put the numbers in the PoC-7 notes, [ADR-008](../adr/008-security-identity-guardrails-defense-in-depth.md), and [ADR-010](../adr/010-evals-offline-tool-online-gate.md). This also clears the Prompt Guard 2 wait in PoC-8. There is no one command for it yet; see section 4.
- [x] **PoC-9 checks, none an exit criterion.** `demo.sh --live`, a debugger attach in the `fake` profile, and the `local` profile with a running LiteLLM ran on 2026-11-05 ([machine checks](../../../pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md)). The attach found a wrong workload path mapping in the template, now fixed.

## 2. Repository settings

The user's call, on GitHub.

- [ ] **Add the `SNYK_TOKEN` repo secret (PoC-7).** Then `poc07-agent-scan` runs a real Snyk Agent Scan, not only the inspect step.
- [ ] **Make `poc08-evals` a required check on `main` (PoC-8).** `main` has no branch protection today (`gh api .../branches/main/protection/required_status_checks` answers 404 `Branch not protected`). Until then the offline eval gate reports but does not block a merge.

## 3. Decisions for the user

Each one is carried as "open, user decision" in the issue it belongs to.

- [ ] **PoC-8 open questions** ([backlog changes](../../../pocs/poc-08-cross-cutting-build/notes/backlog-changes.md#open-questions-for-the-user)):
  - Q1: `/metrics` open by default, or behind a scope?
  - Q2: the output URL allow-list: keep default deny?
  - Q3: `CloudEvent` `extra="forbid"`: accept and drop unknown extensions such as `aigenerated`?
  - Q4: keep the feedback panel on the dashboard, or leave feedback to Langfuse only?
  - Q6: Presidio in the Collector log pipeline, or accept the regex-only gap for workload logs?
  - Q7: rate limits with auth off: fine for local and test only, or an anonymous bucket?
- [ ] **PoC-4 items for the owner** ([backlog changes](../../../pocs/poc-04-stateless-scalable/notes/backlog-changes.md#not-applied-for-the-owner)): the full `EventPortContract` bar for 060 H-23; the PoC-4 result and drain rule in 024 CH-3; the chassis CPU at 10 RPS as an input to ADR-001's Revisit rule; the OpenAPI limits fixed at start (051 H-13); the idempotency key in `deploy/compose/SECURITY.md` against contract v3; the vendored CloudEvents schema diff.
- [ ] **PoC-5 items for the owner** ([backlog changes](../../../pocs/poc-05-sandboxed/notes/backlog-changes.md#not-applied-for-the-owner)): new issues for the TypeScript inbound token and CNI flow logs; the hand-kept `trustedRepositories` (025 H-10); H31, which waits on 004 G-2.

## 4. Engineering work

Code an engineer or an agent can do now. Each item names the PoC criterion it moves.

- [x] **A CI job for the sidecar hostile suite (PoC-5 criterion 1).** Done 2026-11-06: `run.sh test-remote` in `remote-lane.yml` runs `test_poc05_kind_sidecar_controls.py`, `test_poc05_kind_hardreq1.py` (with the broker, after a `run.sh kafka` step), `test_poc05_kind_tool_gateway.py`, and `test_poc05_kind_admission.py`; the run is named in [the PoC-5 README](../../../pocs/poc-05-sandboxed/README.md), criterion 1.
- [ ] **MinIO through the chassis (PoC-5 criterion 3).** Give a chassis a scoped MinIO key, then show the allowed call through it. MinIO already refuses the workload.
- [ ] **The T10 probe workload (PoC-5 criterion 8).** Owned by the user. It closes when [the T10 runbook](../../guides/poc-05-runbooks.md#testing-the-probe-workload-t10-after-it-is-built) passes.
- [ ] **The `kagent-adk` scorecard cells (6b exit criterion 3).** The image, RSS, latency, cold start, streaming, and token cells still say "not measured: kind, from CI". `poc06-kind.yml` passes its contract and hostile checks, but it has no step that measures image size, RSS, or tokens. Add that step, then fill the cells. Kind latency under load is already in [the load note](../../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-10-remote-load-on-kind.md).
- [ ] **One command for the PoC-7 Mac run.** suggested: a `poc07` step in `scripts/poc06_mac.sh`, or its own script, that starts `guardrail-server --classifier prompt-guard`, the real LiteLLM `presidio` hook, and the judge and encoder comparison.
- [ ] **A test that every workload image accepts the scale stack's command (6a follow-up).** The TypeScript agent refused `--drain-timeout-s` for a while, and no offline test caught it.
- [ ] **The rare 502 under load (6a follow-up).** About 1 in 4,600 calls at 4 pairs. Find out if the chassis or Traefik answers it.
- [ ] **Named-entity masking in the Collector log pipeline (PoC-8).** Waits on Q6 in section 3. Today it masks only emails and phone numbers.
- [ ] **Throughput at 4 pairs (PoC-4 criterion 4).** Rerun the load matrix on a Linux host or with pinned CPUs. Tracked in [122 X-5](../issues/122-X-5-load-tests.md).

## 5. Planning and docs

Done with the `planning-sync` skill, then `make planning-sync planning-check`.

- [ ] **Apply the PoC-7 backlog changes.** [The file](../../../pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md) is still a draft from 2026-10-28. No issue has a "Status after PoC-7" bullet yet. ADR-007 to ADR-010 are accepted, so it can be applied now.
- [ ] **Apply the PoC-6c lessons.** The "to apply" list in [the 6c backlog changes](../../../pocs/poc-06c-pretrained-slm/notes/backlog-changes.md#what-the-ci-run-teaches-to-apply) (063 S-8, 036 S-7, 013 CH-2) has no "Status after PoC-6c" bullet in any issue.
- [ ] **Close 6a, 6b, 6c, and 7** after the Mac run: tick the boxes with evidence, set `Status: done`, and trim their planning docs to "What is left: nothing".
- [ ] **Optional: a trial by someone new to the code (PoC-9 criterion 6).** [The steps](../../../pocs/poc-09-agent-mvp-template/notes/2026-10-30-new-user-trial.md).

## Moved to the backlog

Not PoC work any more. Each is in its issue.

- Throughput at 4 pairs: 122 X-5.
- Publish `workload-a2a`, `agent-testkit`, `fake-model-server`, and a pinned chassis image: 025 H-10.
- `agentctl`: `--trust`, the `orchestrator` and `data` kinds, `spec.modules`, the `governance` block, and a chart in the generated repo: 059 H-15.
- On a cluster: traffic spread through the Service, the remote lane, and NetworkPolicy enforcement: 024 CH-3.
- A fake model the `fake` profile can script per case; the chassis CLI asks (`serve --uds`, `--profile fake` with no config, the bound proxy port); the kit's remote lane: listed in [PoC-9](009-PoC-9-agent-mvp-template.md#moved-to-the-backlog).

## After the PoC track

From [000-plan.md](000-plan.md#after-the-mvp): update the chassis waves W2 and W5 and issues CH-1 to CH-8 with the PoC results, then continue the backlog from the router and SLM waves.
