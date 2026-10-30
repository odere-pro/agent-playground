# PoC-9: what the first build left open

Date: 2026-10-30

The first build merged five parallel parts: `agentctl` and the template, `agent-testkit`, the template's Compose and CI, the Helm library chart, and the guide. This note lists what is still open and why.

## How the first-commit check runs

```bash
scripts/check_offline.sh pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py -q -rA
```

Each case runs `agentctl new` for one sidecar framework, then the agent's own `make test` with `UV_OFFLINE=1`. On 2026-10-30, on the Mac, all four passed (`python`, `pydanticai`, `langgraph`, `openai-agents`).

## Decisions made while merging

- **The agent is pinned to the platform's versions.** A fresh resolve picked newer releases than the platform tested (`cryptography` 50.0.2 against 50.0.1), and offline the cache does not hold them. `agentctl new` now exports the platform's `uv.lock` as `[tool.uv] constraint-dependencies` and runs `uv lock` before the first commit. `--no-lock` skips it.
- **Platform packages are editable path sources.** `agent-testkit` is a workspace member, so uv sees `workload-a2a` as editable through it. A non-editable source in the agent conflicted with that.
- **agentctl ignores git's hook variables.** Inside a git hook, `GIT_DIR` and `GIT_INDEX_FILE` point at the caller's repository. Copier then treated the template folder as part of it and failed, and the first commit could have landed in the caller's repository. agentctl now drops those variables for Copier, git, and uv (`packages/agentctl/tests/test_agentctl_cli.py::test_inside_a_git_hook_the_callers_repo_is_left_alone`).
- **The template's CI and Compose files live under `template/project/`,** next to the rest of the rendered tree. `copier.yml` sets `_subdirectory: project`.

## Open

1. **Criterion 4, the agent's own CI.** A rendered agent names the platform packages by absolute path. A CI runner has the platform somewhere else, so `uv sync` fails there. suggested: publish `workload-a2a`, `agent-testkit`, and `fake-model-server` to a package index, or use git sources pinned to a tag. Until then the CI cannot pass with no manual edits.
2. **Stand-in suites.** `make load-smoke`, `hostile`, `redteam`, and `eval` in a rendered agent run the platform's suites from `PLATFORM_PATH`, on the platform's engines. They do not test the agent's own cases yet. That needs the suites to ship in `agent-testkit`.
3. **Not run for real yet:** `make image` on a rendered agent, `docker compose up` with each profile, the debugger attach, and a CI run with cosign.
4. **The remote lane.** `smolagents` and `claude-agent` render and pass ruff and their unit tests. Their `make test` was not run: the kit drives the `sidecar` lane only.
5. **The security call to confirm.** The chassis debugger is off by default in the `local` profile, because debugpy has no login and the workload shares the chassis network, where the key lives. `platform-security` should confirm it.
6. **Chassis asks from the kit** are in [2026-10-29-testkit-asks.md](2026-10-29-testkit-asks.md): `chassis serve --uds`, `--profile fake` with no config file, the bound proxy port, and a published chassis package.
7. **Criteria 2, 3, 6, and 8** (the swap drill, the Agent MVP done-when list, a trial by someone new to the code, and the W2 and W5 report) have not started.
