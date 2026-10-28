# Backlog changes when PoC-6c closes

Status: **Drafted 2026-10-09; the SLM numbers are not in.** Applied through skill `planning-sync` as "Status after PoC-6" bullets at the end of `## What`. No acceptance box was ticked, and no order, size, or dependency changed.

Evidence: [Mac command debt](2026-10-09-mac-command-debt.md), `tests/test_poc06c_mac_static.py`, `deploy/compose/README.md` ("PoC-6: the one Mac command").

## Applied

- **003 G-1b**: `big-default` hosted and `local-small` to llama.cpp are configured; the name stays.
- **036 S-7**: PoC-6c uses llama.cpp, not vLLM; vLLM on a GPU stays here.
- **063 S-8**: the base-model baseline, with no number yet.
- **026 CH-4**: the Mac command uses the LiteLLM master key as the chassis key. Accepted debt; the fix is a scoped key per run.

## Not applied: waits on the Mac run

- Every SLM number: success, tool-call success, fallback share, token overhead, latency, rejected request features, the scale run.
- The SLM section of ADR-006, and the check whether PydanticAI stays the default.
