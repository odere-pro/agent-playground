# Swap drill (criterion 2)

Date: 2026-10-30

```bash
scripts/check_offline.sh pocs/poc-09-agent-mvp-template/tests/test_poc09_swap.py -q
```

Result: `2 passed in 6.82s`, on a scaffolded `pydanticai` agent and a `langgraph` agent.

- **What was swapped:** telemetry, from `memory` to the real `otel` adapter, and its backend from Langfuse to Jaeger.
- **What changed:** one line of `config/agent.yaml` and one variable, `OTEL_EXPORTER_OTLP_ENDPOINT`.
- **What did not change:** every file under `src/`. The test compares a digest of the tree.
- **Time:** minutes, against the one-day limit.
- **The real backends:** the same swap against a real Langfuse and a real Jaeger passed in PoC-7, `poc07-stack` run 40735435727 (`test_swap_to_direct_langfuse_by_exporter_config_only`). This drill shows the same switch on a scaffolded agent; it does not start those backends again.
