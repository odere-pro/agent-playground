# agentctl

The PoC-9 agent factory. It scaffolds a new agent from the service template.

```bash
# Once, in the platform checkout: make setup (installs agentctl).
uv run agentctl new invoice-reader --kind transformer                       # langgraph, sidecar
uv run agentctl new coder --kind tool --framework smolagents --connector remote
cd invoice-reader && make setup test run
```

| Option | Values | Default |
| ------ | ------ | ------- |
| `name` | a DNS label: lower case letters, digits, dashes | required |
| `--kind` | `transformer`, `evaluator`, `tool` | required |
| `--framework` | `python`, `pydanticai`, `langgraph`, `langchain` (`create_agent`), `openai-agents`, `smolagents`, `claude-agent` | `langgraph` (ADR-006, amended 2026-11-06) |
| `--trust` | `trusted`, `untrusted` (suggested: the values, P-15) | the framework's (`rules.trust_of`) |
| `--connector` | `sidecar`, `remote` | `sidecar`; `remote` with `--trust untrusted` |
| `--key-route` | a route the service's LiteLLM key lists; repeat for each | `big-default` (suggested:) |
| `--dest` | the parent folder | `.` |
| `--platform-path` | the agent-platform checkout | the one agentctl runs from |
| `--no-lock` | skip the platform pin and `uv.lock` | off |

The trust rule (ADR-001 item 5): `smolagents` and `claude-agent` are untrusted and run only with `--connector remote`. The other five may use either lane. `--trust untrusted` runs a trusted framework as untrusted: the `remote` lane, so the chart puts the workload in its own pod under gVisor with default-deny egress. `--trust untrusted --connector sidecar` is refused, and so is `--trust trusted` on an untrusted framework. A refused argument exits 2 with the reason and writes nothing.

The route check (012 H-3): each service has one LiteLLM key, minted at deploy, that lists the routes the service may use. agentctl refuses a generated route the key does not list; it does not rewrite the route. agentctl cannot read the real key offline, so `--key-route` states its list. With no `--key-route` the list is the route the template already names, `big-default`, so the check passes.

## What it writes

`DEST/<name>/`, a git repository on `main` with one commit and no remote:

- `src/<package>/handle.py` and the framework's `mapping.py` (plus `tools.py`, `telemetry.py`, `agent.py` where the echo workload has them): the example simplifier.
- `config/agent.yaml` (`local` profile, `spec.engine.connector`, `spec.trust`) and `config/fake.yaml` (the test kit's chassis).
- `pyproject.toml` with exact pins, path sources into the platform checkout for `workload-a2a`, `agent-testkit`, and `fake-model-server` (suggested: a package index later), and `[tool.agent-testkit]`.
- `tests/`: unit tests of `handle`, and the kit's lane contract over A2A.
- A hardened `Dockerfile`, a `Makefile` (`setup`, `test`, `lint`, `run`, `image`), `README.md`, `CLAUDE.md`, and `.copier-answers.yml` for `copier update`.

The template is plain Copier: `uvx copier copy packages/agentctl/src/agentctl/template <dest>` asks the same questions.
