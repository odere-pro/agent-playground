# packages/workloads/hostile

The probe workload for the PoC-5 hostile suites: one fixed check per threat-model id, standard library only, on the `workload-a2a` template. Skeleton from PoC-5 task T01; task T10 owns everything here and replaces this file.

- Design: `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 5.
- Never imports `chassis` or `chassis_contracts`; `make lint` (import-linter) enforces it.
- Tests start with the package name (`test_hostile_*`), so basenames stay unique in the repo.
- `pyproject.toml` and `uv.lock` belong to T01 in PoC-5: ask the orchestrator for a dependency change.
