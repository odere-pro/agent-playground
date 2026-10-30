# PoC-9: what the first build left open

Date: 2026-10-30

The first build merged five parallel parts: `agentctl` and the template, `agent-testkit`, the template's Compose and CI, the Helm library chart, and the guide. This note lists what is still open and why.

## How the first-commit check runs

```bash
scripts/check_offline.sh pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py -q -rA
```

Each case runs `agentctl new` for one sidecar framework, then the agent's own `make test` with `UV_OFFLINE=1`. On 2026-10-30, on the Mac, all four passed (`python`, `pydanticai`, `langgraph`, `openai-agents`).

## Decisions

- **The agent is pinned to the platform's versions.** A fresh resolve picked newer releases than the platform tested (`cryptography` 50.0.2 against 50.0.1), and offline the cache does not hold them. `agentctl new` now exports the platform's `uv.lock` as `[tool.uv] constraint-dependencies` and runs `uv lock` before the first commit. `--no-lock` skips it.
- **Platform packages are editable path sources under a `.platform` link.** `make` links it from `PLATFORM_PATH`, so `pyproject.toml` and `uv.lock` hold no path of one machine. A clone works anywhere.
- **The agent's suites are its own.** `agent_testkit.suites` replaced the stand-ins. Engines that need a real model declare `error` as their offline end state, with the reason in the template.
- **agentctl ignores git's hook variables.** Inside a git hook, `GIT_DIR` and `GIT_INDEX_FILE` point at the caller's repository. Copier then treated the template folder as part of it and failed, and the first commit could have landed in the caller's repository. agentctl now drops those variables for Copier, git, and uv (`packages/agentctl/tests/test_agentctl_cli.py::test_inside_a_git_hook_the_callers_repo_is_left_alone`).
- **The template's CI and Compose files live under `template/project/`,** next to the rest of the rendered tree. `copier.yml` sets `_subdirectory: project`.

## Open

The first-build list above is closed, except these:

1. **Criterion 6.** A trial by someone new to the code: [the steps](2026-10-30-new-user-trial.md).
2. **A hosted CI run.** The agent is on GitLab, `odere.pro/poc09-demo-agent`, private. Pipeline 1 found a real bug: a job named `image` is GitLab's global keyword; fixed, with a test. Pipeline 2 passes config and stops at `Identity verification is required in order to run CI jobs`, an account step only the user can do. No job has run on a hosted runner yet.
3. **The live demo.** `demo/demo.sh --live`: a call from the host through the OpenAI and MCP interfaces, and a debugger attach. `curl` is not allowed in the PoC-9 session.
4. **The `local` Compose profile.** It needs a running LiteLLM.
5. **Chassis asks from the kit:** [2026-10-29-testkit-asks.md](2026-10-29-testkit-asks.md).
