# Local development

## Setup

```bash
make setup
```

Installs Python 3.12 through uv, every workspace package in editable mode, the dev tools, and the git pre-commit hook. What the hook runs is in [testing.md](testing.md#offline-is-enforced).

## The gates

| Command | When | What |
| ------- | ---- | ---- |
| `make quick` | before every commit | format check, lint (ruff and import rules), `planning-check`, `harness-lint`, and the non-PoC tests of what changed ([scripts/quick_select.py](../../scripts/quick_select.py)); no mypy, no PoC tests |
| `make check` | before a PR, at an iteration end | the CI `check` job: format, lint, mypy, the gate's tests (`make test-gate`), planning check, harness lint |
| `make test` | any time | every test, offline ([testing.md](testing.md#offline-is-enforced)) |
| `make test-poc POC=01` | while working on an iteration | that iteration's scenario tests |
| `make test-pocs-done` | before a change that could touch a finished PoC | the finished PoCs' scenario tests, which CI does not run ([testing.md](testing.md#what-ci-runs)) |
| `make gate-paths` | any time | which test folders the gate runs and which run only locally |

A green `make check` covers only the CI `check` job. The other CI jobs, and when each runs, are in [testing.md](testing.md#what-ci-runs):

- In `.github/workflows/ci.yml`: `lint-extra` (`make lint-extra`).
- `typescript.yml` (`make ts-check`) and `images.yml` (builds each container image), on their paths.
- On their paths: `poc08-evals` (`make evals`, `make redteam`), `poc09-helm`, and `poc09-scaffold`.
- By hand only: `remote-lane`, `poc06-kind`, `poc06-load`, `poc06-slm`, `poc07-agent-scan`, `poc07-evals`, `poc07-stack`, `poc08-litellm`, and `poc08-stack`.

## Parallel work

Run a second line of work in its own git worktree. The rules, including one worktree on Docker at a time, are in skill `git-flow` ([.claude/skills/git-flow/SKILL.md](../../.claude/skills/git-flow/SKILL.md), "Parallel work in worktrees").

## Profiles

`spec.adapters` names the adapter per port. A profile sets them all at once.

- `fake` needs no network and no keys.
- `local` (Docker Compose) and `cloud` name the real adapters. Each needs its service and settings.
- `chassis.profiles.REGISTRY` maps each adapter name to its class.

## The fake model server

```bash
make fake-model-server      # http://127.0.0.1:8081, packages/fake-model-server/scripts/example.yaml
```

Point any OpenAI-compatible client at it with `base_url=http://127.0.0.1:8081/v1` and any key. The script format is in the package's `CLAUDE.md`.

## Schemas

`make schemas` regenerates `packages/chassis/schemas/*.json` from the Pydantic models. A test fails when the checked-in files drift, so run it after any change to events or the envelope and commit the result.
