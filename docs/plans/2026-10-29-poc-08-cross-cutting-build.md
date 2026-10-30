# PoC-8: what is left

Status: done
Date: 2026-10-29
Source: [008-PoC-8-build-cross-cutting.md](../planning/poc/008-PoC-8-build-cross-cutting.md). Tracking: [PoC-8 README](../../pocs/poc-08-cross-cutting-build/README.md).

The code, the scenario tests, the notes, the offline demo, and the two stack drills are done. PR 39 added the workload-side JSON logs, the Prometheus and Grafana drill, and the real-LiteLLM drill. `notes/backlog-changes.md` is applied to the issues (2026-10-30). This list is only what is still open.

## 1. The CI runs

Done. `check`, `poc08-evals`, `poc08-stack`, and `poc08-litellm` passed on `db13d6a`; the run ids are in the README. `poc08-evals` as a required check on `main` is still a repository setting, the user's call.

## 2. The user's decisions

The open questions in [`notes/backlog-changes.md`](../../pocs/poc-08-cross-cutting-build/notes/backlog-changes.md), carried as "open, user decision" in the issues:

- Does `/metrics` stay open by default? (014 H-7)
- Does the output URL allow-list stay default deny? (022 H-6)
- Does `CloudEvent` accept unknown extensions, so a new one such as `aigenerated` does not break an older consumer? (023 C-5)
- Keep the `User feedback by label` panel, or leave feedback to Langfuse only? (014 H-7)
- Presidio in the Collector log pipeline for named entities, or accept the regex-only gap for workload logs? (014 H-7)
- Rate limits with caller auth off: local and test profiles only, or an anonymous bucket? (022 H-6)

## 3. Run on the Mac

These need a Hugging Face token or a hosted model key. CI has neither.

- The red-team set on Llama Prompt Guard 2 in place of the pattern stand-in. Today: 14 of 18 on the stand-in.
- Numbers on a hosted model, and a real judge's agreement with the golden set's labels.

## 4. Close

Done on 2026-10-30. The README is set to done, with the Offline evals scope item flagged.
