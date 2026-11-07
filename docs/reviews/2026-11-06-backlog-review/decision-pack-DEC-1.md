# Decision pack: 001 DEC-1, trimmed to what phases A to F need

Session: C2. Date: 2026-11-06.
Inputs: `001 DEC-1` ([the issue](../../planning/issues/001-DEC-1-resolve-open-decisions.md)), the `## DEC-1 evidence` section of [findings-W01-decisions-router.md](findings-W01-decisions-router.md), the open items in the product plan ([2026-11-06-agent-container-product.md](../../plans/2026-11-06-agent-container-product.md), "Open, for the user"), the ADRs' open questions, and the findings that send a decision to DEC-1.
Companion: [phase-map.md](phase-map.md), which places each issue named below in its phase.

## How to read

- One section per decision a product phase needs. Each has: Question, Options, What the PoCs learned, `suggested:`, Unblocks, Where to record.
- A box of `001 DEC-1` is named by its number in the issue, after W01's split of box 8 into 8a and 8b. A decision that is not a box today is marked **proposed box**: a finding sends it to DEC-1, and C1's `fix-plan.md` adds the box.
- Every value the epic does not give is marked `suggested:`. The user decides; this pack only lays out the choice.
- Decisions no phase needs are at the end: `later` (open, not urgent), parked with the SLM track (not decided), and already met.

## The pack at a glance

| # | Decision | Phase | suggested: | Where to record |
| - | -------- | ----- | ---------- | --------------- |
| 1 | The north star (box 6, rewritten) | E | good tasks per week | the product plan, [metrics](../../specs/metrics.md), and a dated comment on DEC-1 |
| 2 | The primary trace view per environment (new box) | D | LangSmith in `cloud`, Langfuse in `local`; the score goes to the primary only | an amendment to ADR-007 (question 4) and ADR-009 (question 5) |
| 3 | The golden-set store and the other reuse picks (box 8b) | D | accept Langfuse; trial LangSmith datasets in phase D; defer lakeFS or DVC and the registry base | a comment on DEC-1; ADR-010 already names the candidate |
| 4 | Owners (box 7) | A, B, E, F | the user fills the table | a table in DEC-1 |
| 5 | The gate's and the rate limiter's outage rules | B, E | **decided 2026-11-06: fail open everywhere, as built** (P-1, P-2) | ADR-010 item 4 and 022 H-6 `## What` |
| 6 | An absolute `facts_kept` floor for a hosted model (proposed box) | B | set it from the Mac run's numbers, not before | ADR-010, then 037 M-2 box 3 |
| 7 | `/metrics` open by default (PoC-8 Q1, proposed box) | E | behind a scope in `cloud`, open in `local` and `fake` | an amendment to ADR-008 |
| 8 | Where the week of real numbers runs (proposed box) | E | one long-running Compose stack on a host the user controls | a comment on DEC-1, then the phase E weekly record |
| 9 | The ADR-001 Revisit reading for the sidecar's cost (proposed box) | A | accept "more than twice the upper estimate" as the trigger | ADR-001 "Revisit", and 122 X-5 |
| 10 | Named-entity masking in workload logs (PoC-8 Q6, proposed box) | E | accept the regex-only gap for phase E; record it | [security](../../specs/security.md), and a comment on DEC-1 |
| 11 | The feedback panel on the dashboard (PoC-8 Q4, proposed box) | E | keep a counts panel; scores live in the primary trace view | a comment on DEC-1, then 046 A-5 |
| 12 | Box 1: the decisions recorded with owner and date | E | closes when its parts close | DEC-1 itself |

## 1. The north star (box 6, rewritten)

- **Question.** Which number is the container's north star: "good tasks per week" or "agents in production on the chassis"? And which draft targets go with its inputs?
- **Options.**
  - Good tasks per week: runs that end `ok`, within budget, and pass the evaluator when scored. It counts output. The chassis already emits every input ([metrics](../../specs/metrics.md)).
  - Agents in production on the chassis: a count of registry manifests. It counts adoption. It needs a registry count that does not exist; the registry is `later` (W7).
  - The epic's targets (big-model spend −30%, `facts_kept` ≥ 0.95, fallback < 10%, registry top-3 ≥ 90%, run recovery 100%) are SLM and later-wave targets. They are not options for the north star.
- **What the PoCs learned.** No real traffic exists, so no target has a measured base. PoC-6c measured a 0% fallback share on scripted cases (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`). The gate's recorded pass rate is 0.780 (`evals/gate/baseline.json`, PoC-8). The formula's inputs exist: `chassis_run_duration_seconds{status}` and `evaluator_calls{outcome}` (`packages/chassis/src/chassis/metrics.py`).
- **`suggested:`** good tasks per week. It needs no new data source, and phase E can show it with what is built. Draft targets: none until the first real week; then the first week is the baseline. The epic's savings targets stay drafts, parked with 005 G-3.
- **Unblocks.** Phase E: 046 A-5 (the north-star row), 117 X-3 (SLO rows), 120 X-7 (cost per good task), and the weekly note (117 X-3 box 7).
- **Where to record.** The product plan's "Open, for the user", item 1; [metrics](../../specs/metrics.md) drops "the alternative" line; a dated comment on DEC-1 with owner and reason.

## 2. The primary trace view per environment (new box)

- **Question.** Is LangSmith or Langfuse the primary trace view in each environment? When both run, does the score go to one or to both?
- **Options.**
  - LangSmith in `cloud`, Langfuse in `local` and self-hosted runs (ADR-007 question 4, first reading).
  - Both in every environment, with one score owner.
  - Langfuse first everywhere, LangSmith as a second exporter only.
- **What the PoCs learned.** Langfuse v4 runs in the PoC-7 stack (`deploy/compose/poc07/stack.sh`) and is the only `FeedbackPort` backend (`packages/chassis/src/chassis/adapters/langfuse`, ADR-009). LangSmith is in no code or config; ADR-007 item 10 and ADR-009 item 3a decide it is added behind the Collector and the chassis. The Collector fans out to every listed exporter (`deploy/compose/poc07/otel-collector.yaml`), so the trace side costs one exporter. The score side does not: ADR-009 question 5 notes that two writes can leave a half-written state the single `feedback_id` cannot repair.
- **`suggested:`** LangSmith is the primary view in `cloud`; Langfuse in `local`, `fake`, and self-hosted runs. The Collector may export traces to both. The score goes to the primary view only (ADR-009 question 5's own suggestion).
- **Unblocks.** Phase D: 129 CH-10 (the exporter, `fake-langsmith`, the stack tests; its last box writes this decision down) and 130 CH-11 (the `langsmith` feedback adapter; its last box tests where the score goes). Phase E: which link 046 A-5 shows next to its panels.
- **Where to record.** An amendment to ADR-007 (open question 4) and ADR-009 (open question 5); ADR-007 says a change to an open question needs a new ADR or an edit there. Then a one-line link from DEC-1.

## 3. The golden-set store and the other reuse picks (box 8b)

- **Question.** Accept or replace the reuse analysis's picks: Langfuse as the golden-set store, lakeFS or DVC for dataset versions, and the registry base (our own on Postgres, or ToolHive).
- **Options.** As listed in `docs/planning/poc/010-reuse-analysis.md`. The product plan adds LangSmith datasets as a candidate store (ADR-010 note).
- **What the PoCs learned.** Langfuse datasets hold the golden cases, tested against a stub (`packages/eval-kit/src/eval_kit/langfuse_datasets.py`, `packages/eval-kit/tests/test_eval_kit_langfuse_datasets.py`; ADR-010 item 2). The cases are in `packages/eval-kit/src/eval_kit/golden/` (`cases.yaml` 20, `cases-gate.yaml` 30). lakeFS, DVC, and ToolHive were never tried. W6-15 adds a lakeFS license point (BSL 1.1).
- **`suggested:`** accept Langfuse as the store. Run LangSmith datasets on the 20 golden cases as phase D's trial under ADR-010's trial rule. Defer lakeFS or DVC to 069 D-7 and the registry base to 076 R-1, both `later`.
- **Unblocks.** Phase D: the datasets trial, 131 CH-12. Box 8b itself.
- **Where to record.** A dated comment on DEC-1 that ticks box 8b; the trial result goes into ADR-010.

## 4. Owners (box 7)

- **Question.** Who owns each dependency a phase needs?
- **Options.** Named people. W01-11 cuts the epic's list: GPU access and real training outputs are parked with the SLM track.
- **What the PoCs learned.** No owner is named in any issue or ADR. The ADRs name the epic owner as decider. The PoCs added owner needs the epic does not list:

  | Need | Phase | Why |
  | ---- | ----- | --- |
  | A Linux host with pinned CPUs for the 4-pair rerun | A | 122 X-5; Docker Desktop could not resolve throughput (011 section 4) |
  | The T10 probe workload | A | 055 CH-6 box 2; owned by the user today (011 section 4) |
  | The Mac run (provider key, Hugging Face access) | B, E | 002 G-1, 003 G-1b, 028 S-3, 042 A-2, 022 H-6 box 8 (011 section 1) |
  | The golden-set reviewer (the 50 hand labels, and ADR-009's reviewer role) | B, E | 028 S-3 box 5, the agreement number |
  | A person new to the code for the template trial | F | 059 H-15, PoC-9 criterion 6 |
  | Legal and compliance | A | 023 C-5 box 4, the AI Act Art. 50(2) client guide, due 2026-12-02; 023 moved to phase A on 2026-11-06 |
  | The cloud account | `later` | 038 X-1a |
- **`suggested:`** the user fills the table. Name the legal owner first: the client guide is due 2026-12-02.
- **Unblocks.** Phase A (122 X-5, 055 CH-6, 023 C-5), phase B (the Mac run, the agreement labels), phase F (the trial).
- **Where to record.** A table in DEC-1 box 7: need, owner, date.

## 5. The gate's and the rate limiter's outage rules (decided 2026-11-06)

**Decided by the user on 2026-11-06: fail open everywhere, as built. No code change.** Recorded in `fix-plan.md`, "User decisions, 2026-11-06", as proposals P-1 and P-2 ([proposals.md](proposals.md)). This section stays as the record; it is no longer open.

- **Question.** When the evaluator is down, does the gate let the answer out (fail open) or count a failed attempt (fail closed)? When the rate-limit store is down, are calls let through or refused?
- **Options that were on the table.** Fail open everywhere; `fail_closed` in `cloud` and `fail_open` in `local` and `fake`; for the gate, an outage counted as a failed attempt (042 A-2's old text).
- **What the PoCs learned.** As built, an evaluator outage fails open and is counted as `chassis.gate_failed_open` (`packages/chassis/tests/test_evaluator_gate.py::test_an_evaluator_outage_fails_open`; ADR-010 item 4). The rate limiter is set to `rate_limit_on_unavailable: fail_open`. The fail-open rate is already a counter metric with a "below 10%" bar ([metrics](../../specs/metrics.md)).
- **Decision.** Fail open everywhere. This pack had suggested `fail_closed` in `cloud`; the user chose to keep the built behavior and to watch it with alerts instead.
- **What it changes.** 017 H-4's sentence reads "An evaluator outage fails open as built (ADR-010 item 4), decided 2026-11-06." 042 A-2 box 5 is rewritten to the fail-open rule and ticked with the test above. 022 H-6 gets no fail-closed box, and one `## What` sentence: "A store outage lets calls through unlimited, decided 2026-11-06 (P-2)." 117 X-3 (phase E) gains two alert boxes: `gate_failed_open / gate_runs` above suggested: 1% over 15 minutes, and the rate-limit store unavailable.
- **Where it is recorded.** ADR-010 item 4 (its "OPEN (user)" note closes) and 022 H-6 `## What`, in the apply PRs.

The other gate defaults are not open: the product plan sets "async at 0.1 on the judge route" for phase B, and ADR-010's rule confirms or changes them from the Mac run's numbers.

## 6. An absolute `facts_kept` floor for a hosted model (proposed box)

- **Question.** Does the offline eval gate fail a hosted model below a fixed `facts_kept` floor, even with no earlier run to compare?
- **Options.** A fixed floor (the epic's draft is 0.95); a floor set from the first hosted run; no floor, only "no worse than the baseline".
- **What the PoCs learned.** `evalgate.py` compares each target to its recorded pass rate (0.780 in `evals/gate/baseline.json`); no 0.95 exists in code. A scripted model cannot reach 0.95 on the 10 fail-labeled cases, so 037 M-2 box 3 is unreachable as written (W3-07). The workflow is `.github/workflows/poc08-evals.yml`.
- **`suggested:`** set the floor from the Mac run's hosted numbers, not before. Until then, keep `suggested: no tolerance on the pass rate while the model is scripted` and the rewritten box 3 ("a pass rate below the baseline floor fails").
- **Unblocks.** Phase B: 037 M-2 box 3, and the judge defaults ADR-010 confirms.
- **Where to record.** ADR-010 (the offline gate), then 037 M-2 box 3.

## 7. `/metrics` open by default (PoC-8 Q1, proposed box)

- **Question.** Is `/metrics` open on the public port, or behind a scope?
- **Options.** Open everywhere; behind a scope everywhere; behind a scope in `cloud` only.
- **What the PoCs learned.** `/metrics` moved to the public port in the PoC-8 build (ADR-008, item 2 note). The product plan lists "`/metrics` open by default" as a P2 gap for phase E. The question is PoC-8's Q1 ([PoC-8 backlog changes](../../../pocs/poc-08-cross-cutting-build/notes/backlog-changes.md#open-questions-for-the-user)). 022 H-6 box 1 splits into an OIDC box and a caller API key box around it (W2b-09).
- **`suggested:`** behind a scope in `cloud`; open in `local` and `fake`, where the Compose Prometheus scrapes it.
- **Unblocks.** Phase E: 022 H-6 box 1; how 117 X-3's Prometheus scrapes in each profile.
- **Where to record.** An amendment to ADR-008. This is the same decision as proposal P-4 in [proposals.md](proposals.md); decide it once. The two suggestions differ: this item suggests a scope in `cloud`, and P-4 suggests option 2, no token and a NetworkPolicy that lets only the scraper in. The user decides both in one go.

## 8. Where the week of real numbers runs (proposed box)

- **Question.** Phase E is done when the dashboard shows "one week of real numbers". Where does that week run, with what traffic?
- **Options.**
  - One long-running Compose stack (the PoC-7 and PoC-8 stacks) on a host the user controls, with the provider key in the chassis only.
  - The user's Mac, over a week.
  - Wait for the first cloud (038 X-1a, `later`).
- **What the PoCs learned.** Every stack so far runs for minutes in CI or on the Mac. The PoC-8 stack mints a scoped virtual key (`deploy/compose/poc08-litellm/stack.sh`). The router's own home question (box 10) is parked, because only the SLM baseline needed it; this is the same question for the product's numbers. No phase A to F has a cloud.
- **`suggested:`** one long-running Compose stack on a host the user controls, with one agent from `agentctl new` serving real tasks for seven days. It needs no cloud and reuses what phase E builds.
- **Unblocks.** Phase E's done-when; 117 X-3's "one week of real SLO numbers" box; 120 X-7's first-week baseline; 117 X-3 box 7, the weekly note.
- **Where to record.** A dated comment on DEC-1, then the weekly record itself.

## 9. The ADR-001 Revisit reading for the sidecar's cost (proposed box)

- **Question.** What measured sidecar cost triggers ADR-001's Revisit rule ("the sidecar costs too much")?
- **Options.** "More than twice the upper estimate" (122's `suggested:`); a fixed number per pod; no number, a judgment after the rerun.
- **What the PoCs learned.** PoC-4 measured the chassis at 10 RPS on one Docker Desktop pair: 0.116 to 0.181 vCPU across paths and runs (`pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md`). The PoC-4 note reads the rule as "at or above the top of 0.05–0.1 vCPU, not above twice" (W1012-20). 122 X-5 box 3 sets chart requests from p95 and checks the trigger.
- **`suggested:`** accept "more than twice the upper estimate" as the trigger, read from the Linux rerun's p95.
- **Unblocks.** Phase A: 122 X-5 box 3.
- **Where to record.** ADR-001 "Revisit" (one sentence with the number), then 122 X-5.

## 10. Named-entity masking in workload logs (PoC-8 Q6, proposed box)

- **Question.** Does the Collector's log pipeline mask names (Presidio), or does the product accept the regex-only gap for workload logs?
- **Options.** Presidio in the Collector log pipeline; accept the gap and record it.
- **What the PoCs learned.** The Collector masks emails and phone numbers only (`packages/chassis/tests/test_collector_log_redaction.py`; 011 section 4). The chassis's own redaction is in `packages/chassis/src/chassis/redaction.py`. LiteLLM's real `presidio` hook waits on the PoC-7 Mac run.
- **`suggested:`** accept the regex-only gap for phase E, record it in the security spec, and revisit after the PoC-7 Mac run.
- **Unblocks.** Phase E: 022 H-6 box 6. `Later`: 040 D-0 box 3 and 071 D-8.
- **Where to record.** [security](../../specs/security.md) (the gap, dated), and a comment on DEC-1.

## 11. The feedback panel on the dashboard (PoC-8 Q4, proposed box)

- **Question.** Does the dashboard keep a feedback panel, or is feedback left to the trace view only?
- **Options.** Keep the panel; drop it and link to the trace view.
- **What the PoCs learned.** The chassis counts feedback (`feedback_failed{code}`) and writes the score to Langfuse through `FeedbackPort` (ADR-009). The question is PoC-8's Q4. It depends on decision 2: the score lives in the primary trace view.
- **`suggested:`** keep a counts panel (submitted and failed) on the container dashboard; the scores themselves live in the primary trace view, linked from the panel.
- **Unblocks.** Phase E: 046 A-5's panel list.
- **Where to record.** A comment on DEC-1, then 046 A-5.

## 12. Box 1: the decisions recorded with owner and date

- **Question.** Are the decisions each recorded with an owner, a date, and a reason?
- **What the PoCs learned.** ADR-001 is accepted and linked. No other DEC-1 box names an owner or a date.
- **`suggested:`** close box 1 when boxes 2, 3, 4, and 6 close, as W01 suggests; it adds no work of its own. Boxes 2 to 4 are `later` (below), so box 1 closes in phase E only if the user records "later, reason" for them.
- **Unblocks.** Phase E: 001 DEC-1 itself.
- **Where to record.** DEC-1.

## Open, but no phase A to F needs them (`later`)

From W01's evidence. They stay open boxes in DEC-1 with these `suggested:` values.

| Box | Question | What the PoCs learned | suggested: | Home |
| --- | -------- | --------------------- | ---------- | ---- |
| 2 | The broker: NATS JetStream or Kafka | The Kafka adapter passes `EventPortContract`; Kafka runs on kind with SASL/SCRAM (`deploy/kind/poc05/platform/kafka.yaml`); NATS never ran; ADR-004 item 8 sets the bar for a NATS adapter (`pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md`) | Kafka; NATS stays a swap behind the port | 019 H-17, 020 X-8, 060 H-23 |
| 3 | The first cloud: AWS or GCP | No cloud evidence. The cluster shape is proven on kind: gVisor RuntimeClass, a NetworkPolicy CNI, ValidatingAdmissionPolicy, native sidecars on 1.33 or later (`pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md`). Remote-lane auth is a bearer token per remote (ADR-005 item 1); managed runtimes are out of scope (ADR-006) | the cloud the company's services already run in, if it gives those four | 038 X-1a, 116 X-1b (managed runtimes are out of scope in 055 CH-6, ADR-006) |
| 4 | The public A2A endpoint in version 1 | The public port serves `/v1/run`, OpenAI, Anthropic, MCP, `/manifest`, `/v1/feedback`, `/metrics` (contract v7). A2A is served only to workloads (PoC-3, PoC-6, contract v5) | P2 unless a named external consumer exists; 061 H-5 unchanged | 061 H-5 |

Also open for the user, with no phase waiting on them (the product plan's P3 row "the user", and the owner list):

- ADR-008 open questions: caller API keys, `user.id` hashing (004 G-2 keeps its `suggested:` marker, W01-21), rate limits with auth off (PoC-8 Q7), and the output URL allow-list default (PoC-8 Q2).
- ADR-009 open questions: subject binding, the reviewer role (065 D-2, 110 C-6), and comment redaction.
- PoC-8 Q3: `CloudEvent` `extra="forbid"` and unknown extensions such as `aigenerated` (023 C-5, 112 O-16).
- ADR-001 follow-up (a), services that write their own store (040 D-0, 064 D-1, 072 C-3, 076 R-1, 113 O-9), and follow-up (b), the Temporal worker in the chassis (099 O-1 onward, W89-02). Both need an ADR before their first box; both are `later`.

## Parked with the SLM track, not decided

These boxes and parts of boxes serve only the parked SLM track. They stay open and unticked, marked parked. Nobody decides them now.

| Box | What | Why parked |
| --- | ---- | ---------- |
| 5 | A first banned-word list, linked from 027 S-1 | only 027 S-1, the SLM rewrite rules, needs it (W01-10); per-case `banned:` words in `packages/eval-kit/src/eval_kit/golden/cases.yaml` are eval data, not the list |
| 6, the epic's half | The savings targets: big-model spend −30%, `facts_kept` ≥ 0.95 for the simplifier, registry top-3, run recovery | they wait on 005 G-3, the parked baseline; decision 1 replaces the box's live half |
| 7, two rows | Owners for GPU access and for real outputs as training data | the SLM training chain is parked (W01-11) |
| 10 | The router's first home, so the baseline counts real traffic | only 005 G-3 needed it; for the product, the router lives in each deploy stack (`deploy/compose/litellm/`, `deploy/compose/poc08-litellm/stack.sh`, `deploy/kind/poc05/platform/`) |

## Already met

W01 ticks two boxes. C1 applies the ticks.

- Box 8a, the Dapr decision: a broker client in the chassis behind `EventPort`, not Dapr pub/sub. ADR-004, accepted 2026-10-08; `pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md`.
- Box 9, the choices ADR-001 leaves to planning: ValidatingAdmissionPolicy, not Kyverno (ADR-005 item 4); gVisor for `remote` pods; the non-Python workload is `echo-typescript`; the code-execution tool is `packages/code-runner` with a fresh sandbox per call (PoC-5); guardrails per route, so no LiteLLM Enterprise (ADR-008 item 4). Decided by build.
