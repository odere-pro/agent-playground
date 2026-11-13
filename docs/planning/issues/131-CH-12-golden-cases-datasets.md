---
title: "CH-12: Golden cases in Langfuse and LangSmith datasets"
labels: ["story", "priority:P1", "phase:2-simplifier-slm", "area:lifecycle", "size:S", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 131
epic_id: CH-12
depends_on: ["037 M-2", "129 CH-10"]
blocks: []
epic_refs: [E.5, principles]
---

## Why

[ADR-010](../adr/010-evals-offline-tool-online-gate.md) keeps the golden cases and results in Langfuse datasets (item 2). Its note of 2026-11-06 makes LangSmith datasets a candidate store, under the trial's rule: the same verdicts on the 20 golden cases on two engines. `eval_kit.langfuse_datasets` is tested against a stub only, and no workflow calls it. The [product plan](../../plans/2026-11-06-agent-container-product.md) puts both datasets in phase D.

## What

- The 20 golden cases and one run per engine land in a LangSmith dataset through `fake-langsmith`.
- The Langfuse dataset gets the stack test ADR-010 item 2 waits on.
- A Makefile target or a workflow runs the upload; the dataset name comes from config.
- 070 D-4's export design is not changed.

## Out of scope

- A dataset store of our own (069 D-7).
- Export formats (070 D-4).

## Acceptance criteria

- [ ] The 20 cases and one run per engine land in a LangSmith dataset through `fake-langsmith`.
  - Open: `fake-langsmith` does not exist yet (129 CH-10). `STORES` in `eval_kit.langfuse_datasets` takes `langsmith` when it lands.
- [ ] The verdicts match on two engines (the ADR-010 note of 2026-11-06).
- [ ] A stack test covers the Langfuse dataset and its runs (`eval_kit.langfuse_datasets`).
  - Open: needs the Langfuse v4 stack (`deploy/compose/poc07`, Docker). Offline, `test_eval_kit_langfuse_datasets.py` covers it against an ASGI stub only.
- [x] A workflow or a Makefile target calls the upload. · Delivered by: `packages/eval-kit/tests/test_eval_kit_dataset_config.py::test_eval_kit_make_eval_datasets_calls_the_upload_with_the_config_env` (`make eval-datasets` runs `python -m eval_kit.langfuse_datasets --env $(EVAL_DATASET_ENV)`)
- [x] The dataset name comes from config, as in 037 M-2 box 6. · Delivered by: `packages/eval-kit/tests/test_eval_kit_dataset_config.py::test_eval_kit_dataset_name_and_store_come_from_the_committed_config`, `::test_eval_kit_another_dataset_needs_a_config_change_only`, `::test_eval_kit_main_reads_the_dataset_name_from_config` (`evals/datasets.json`, `eval-datasets.v0`)
- [x] The YAML in `packages/eval-kit/src/eval_kit/golden/` stays the only source of cases, and each environment writes results to one store, named in config (gap review, 2026-11-07). · Delivered by: `packages/eval-kit/tests/test_eval_kit_dataset_config.py::test_eval_kit_dataset_items_come_from_the_golden_yaml_only`, `::test_eval_kit_each_environment_names_exactly_one_known_store`, `::test_eval_kit_an_unknown_environment_is_refused`

## Dependencies

- Depends on: [037 M-2](037-M-2-eval-gate-ci.md), [129 CH-10](129-CH-10-langsmith-trace-backend.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [Principles](../slm-agent-platform-epic-v3.md#principles)
- Backlog plan: [000-plan.md](000-plan.md)
