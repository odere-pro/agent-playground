# Local development

## Setup

```bash
make setup
```

Installs Python 3.12 through uv, every workspace package in editable mode, the dev tools, and the git pre-commit hook (which runs `make quick`).

## The gates

| Command | When | What |
| ------- | ---- | ---- |
| `make quick` | before every commit | format check, lint (ruff and import rules), tests of the packages you changed |
| `make check` | before a PR, at an iteration end | everything CI runs: format, lint, types, all tests, planning check, harness lint |
| `make test` | any time | every test, offline: sockets disabled, `*_API_KEY` variables stripped |
| `make test-poc POC=01` | while working on an iteration | that iteration's scenario tests |

## Profiles

`spec.adapters` names the adapter per port; profiles set them all at once. `fake` needs no network and no keys and is the only one day 0 resolves. `local` (Docker Compose) and `cloud` name adapters that later PoCs add; `chassis.profiles.REGISTRY` says which.

## The fake model server

```bash
make fake-model-server      # http://127.0.0.1:8081, packages/fake-model-server/scripts/example.yaml
```

Point any OpenAI-compatible client at it with `base_url=http://127.0.0.1:8081/v1` and any key. The script format is in the package's `CLAUDE.md`.

## Schemas

`make schemas` regenerates `packages/chassis/schemas/*.json` from the Pydantic models. A test fails when the checked-in files drift, so run it after any change to events or the envelope and commit the result.
