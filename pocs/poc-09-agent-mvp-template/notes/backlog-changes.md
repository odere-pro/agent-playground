# Backlog changes from PoC-9

Apply with the `planning-sync` skill when PoC-9 closes.

- **025 H-10 (template repo):** the template lives in `packages/agentctl/src/agentctl/template/`, a Copier template. Add: publish the platform packages.
- **059 H-15 (agent factory CLI):** `agentctl new` is built. Add: `--no-lock`, and pinning to the platform's `uv.lock`.
- **024 CH-3 (Helm library chart):** `deploy/helm/agent-lib` is built, rendered in `poc09-helm`. Add: the remote lane renders as two pods.
- **058 CH-8 (framework event mappings):** one mapping per supported framework ships in the template, with a drift test against the echo workloads.
- **122 X-5 (load tests):** the agent's `make load-smoke` is a short concurrent run. It does not replace the rerun at 4 pairs.
- **New, suggested:** chassis CLI asks, in [2026-10-29-testkit-asks.md](2026-10-29-testkit-asks.md).
