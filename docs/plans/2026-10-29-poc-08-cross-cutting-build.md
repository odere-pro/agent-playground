# PoC-8: what is left

Status: done
Date: 2026-10-29
Source: [008-PoC-8-build-cross-cutting.md](../planning/poc/008-PoC-8-build-cross-cutting.md). Tracking: [PoC-8 README](../../pocs/poc-08-cross-cutting-build/README.md).

The code, the scenario tests, the notes, the offline demo, and the two stack drills are done. PR 39 added the workload-side JSON logs, the Prometheus and Grafana drill, and the real-LiteLLM drill. `notes/backlog-changes.md` is applied to the issues (2026-10-30). This list is only what is still open.

## 1. Repository and CI

- Make `poc08-evals` a required check on `main`, a repository setting and the user's call. `main` has no branch protection today, so PRs 20, 39, and 41 merged before their CI finished. Then tick the Offline evals scope item in the README, the only unticked box.
- Get one green `check` on `main`. The last full green `check` was on the PR 39 branch at `db13d6a`. Every `ci` run on `main` after the fixes was cancelled by the next merge.

## 2. The user's decisions

The open questions in [`notes/backlog-changes.md`](../../pocs/poc-08-cross-cutting-build/notes/backlog-changes.md), carried as "open, user decision" in the issues:

- Does `/metrics` stay open by default? (014 H-7)
- Does the output URL allow-list stay default deny? (022 H-6)
- Does `CloudEvent` accept unknown extensions, so a new one such as `aigenerated` does not break an older consumer? (023 C-5) suggested: yes.
- Keep the `User feedback by label` panel, or leave feedback to Langfuse only? (014 H-7)
- Presidio in the Collector log pipeline for named entities, or accept the regex-only gap for workload logs? (014 H-7)
- Rate limits with caller auth off: local and test profiles only, or an anonymous bucket? (022 H-6)
- The evaluator gate's default mode and sample rate: `sync`/1.0, or the suggested `async`/0.1? (017 H-4, ADR-010)

## 3. Run on the Mac

These need a Hugging Face token or a hosted model key. CI has neither.

- The red-team set on Llama Prompt Guard 2 in place of the pattern stand-in. Today: 14 of 18 on the stand-in.
- Numbers on a hosted model, and a real judge's agreement with the golden set's labels.
- A pass-rate tolerance for the gate, recorded when it first runs on a hosted model. suggested: no tolerance while the model is scripted.

## 4. Build work

- Red-team misses, rerun on Prompt Guard 2: `input-04-spaced-out`, `input-05-french` (022 H-6).
- Red-team misses: `tool-04-base64`, `exfil-03-write-tool-gets-secret` (054 H-16).
- Sampling, ADR-007 item 8 (014 H-7).
- The evaluator gate does not shrink `budget.timeout_ms` across attempts (017 H-4).
- Judge tokens are not charged to `budget.max_tokens` (017 H-4).
- Golden sets for the other agents; only `simplifier` has one (064 D-1).
- A consumer on a chassis older than PoC-8 must read an event with `aigenerated` (023 C-5). This depends on the `CloudEvent` decision.

## 5. Other

- Golden case g08 is labelled `pass`, but the scripted judge fails its rubric, and it sits in neither baseline list. Fix the label or the rubric (037 M-2).
- Legal on disclosure for `minimal` with marking on (023 C-5).

## 6. Close

Done on 2026-10-30. The README is set to done, with the Offline evals scope item flagged.
