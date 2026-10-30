# agentctl

The PoC-9 agent factory. It scaffolds a new agent from the service template.

```bash
uv run agentctl new invoice-reader --kind transformer                       # pydanticai, sidecar
uv run agentctl new coder --kind tool --framework smolagents --connector remote
cd invoice-reader && make setup test run
```

| Option | Values | Default |
| ------ | ------ | ------- |
| `name` | a DNS label: lower case letters, digits, dashes | required |
| `--kind` | `transformer`, `evaluator`, `tool` | required |
| `--framework` | `python`, `pydanticai`, `langgraph`, `openai-agents`, `smolagents`, `claude-agent` | `pydanticai` (ADR-006) |
| `--connector` | `sidecar`, `remote` | `sidecar` |
| `--dest` | the parent folder | `.` |
| `--platform-path` | the agent-platform checkout | the one agentctl runs from |

The trust rule (ADR-001 item 5): `smolagents` and `claude-agent` are untrusted and run only with `--connector remote`. The other four may use either lane. A refused argument exits 2 with the reason.

## What it writes

`DEST/<name>/`, a git repository on `main` with one commit and no remote:

- `src/<package>/handle.py` and the framework's `mapping.py` (plus `tools.py`, `telemetry.py`, `agent.py` where the echo workload has them): the example simplifier.
- `config/agent.yaml` (`local` profile, `spec.engine.connector`, `spec.trust`) and `config/fake.yaml` (the test kit's chassis).
- `pyproject.toml` with exact pins, path sources into the platform checkout for `workload-a2a`, `agent-testkit`, and `fake-model-server` (suggested: a package index later), and `[tool.agent-testkit]`.
- `tests/`: unit tests of `handle`, and the kit's lane contract over A2A.
- A hardened `Dockerfile`, a `Makefile` (`setup`, `test`, `lint`, `run`, `image`), `README.md`, `CLAUDE.md`, and `.copier-answers.yml` for `copier update`.

The template is plain Copier: `uvx copier copy packages/agentctl/src/agentctl/template <dest>` asks the same questions.
