# Testing

The test layers of the PoC plan, and where each lives today.

| Layer | What it checks | Where | Runs |
| ----- | -------------- | ----- | ---- |
| Unit | core, fakes, adapters, business logic with fakes | `packages/*/tests` | every commit, offline |
| Port contract | fakes and real adapters behave the same | `packages/contract-suites`, bound in each package's tests | every commit (fakes); nightly and on adapter change (real) |
| Inbound contract | every protocol maps to the same canonical request | PoC-3 | every commit, offline |
| Lane contract | every case gives the same result over A2A on localhost and in memory; a fake workload through `remote` | PoC-2, PoC-5 | every commit (ADR-001 hard requirement 2) |
| Component | the whole chassis in-process, every interface, fakes | PoC-1 walking skeleton | every commit, offline |
| Scenario | one test per PoC exit criterion | `pocs/poc-NN-*/tests` | every commit |
| Integration | real dependencies in containers | `packages/chassis/tests/integration/` (Valkey, MinIO, Kafka, Dapr suite bindings) and the `network` tests in `pocs/poc-04-stateless-scalable/tests/`; `make test-integration` | every merge; needs Docker |
| End to end | the Compose stack, `local` profile | PoC-1 onward | every merge, nightly |
| Evals | quality on the golden set | PoC-7 | on business logic, prompt, or model change |

## Offline is enforced

`make test` runs [scripts/check_offline.sh](../../scripts/check_offline.sh). This section owns the list of stripped variables.

- Sockets are off: pytest runs with `--disable-socket --allow-unix-socket` (pytest-socket).
- Markers are strict: `--strict-markers` fails a test that uses an unregistered marker.
- Keys are stripped: the script unsets every variable whose name ends in `_API_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD`, `_SECRET_KEY`, `_PUBLIC_KEY`, `_MASTER_KEY`, `_ACCESS_KEY`, `_PROVIDER_KEY`, `_VIRTUAL_KEY`, or `_LITELLM_KEY`.
- A test that needs a socket is marked `network` and is not part of any gate.
- HTTP tests use the httpx ASGI transport, which needs no socket.

The pre-commit hook ([scripts/git-hooks/pre-commit](../../scripts/git-hooks/pre-commit)) runs `make lint-staged` first: codespell, shellcheck, yamllint, and detect-secrets on the staged files ([scripts/lint_staged.sh](../../scripts/lint_staged.sh)). Then it runs `make quick`. `make lint-fix` repairs formatting, typos, and moved `.secrets.baseline` lines. In `make lint-extra`, [scripts/check_secrets.sh](../../scripts/check_secrets.sh) fails on a new secret only, not on moved baseline lines.

## Test shape

- Name: `test_<subject>_<does what>_when_<condition>`. The `when_` part is optional. The name reads as a sentence.
- Docstring: one plain line that says the behavior.
- Body: arrange, act, assert, split by blank lines.
- One behavior per test.
- Use `pytest.mark.parametrize` instead of a loop.
- No `if` or `for` around an assert.
- Helpers live in a harness module, with names from the domain, not from the test.
- A PoC scenario test's docstring starts with its exit criterion, quoted word for word from the PoC README. More lines are optional.

## Contract suites

Bind, do not copy:

```python
class TestMyModelAdapter(ModelPortContract):
    @pytest.fixture
    def model_port(self): ...
```

Required fixtures raise `NotImplementedError`; optional cases skip with a hint until overridden. See [adding-a-port.md](adding-a-port.md).

## Integration tests

`make test-integration` runs [scripts/check_integration.sh](../../scripts/check_integration.sh). It runs `pytest -m network` on `packages/chassis/tests/integration` and `pocs/poc-04-stateless-scalable/tests`, with sockets on.

- It strips a narrower list: names ending in `_API_KEY`, `_TOKEN`, `_SECRET`, or `_PASSWORD`. The live Langfuse binding reads `LANGFUSE_*_KEY` from the shell, so those stay.
- Real adapters bind the same suites as the fakes. The services start from the helpers in `chassis_contracts.containers` (testcontainers).
- A test skips when Docker is not reachable.
- Extra pytest arguments go through `ARGS`, for example `make test-integration ARGS="-k valkey"`.

## Markers

The registered list is in [pyproject.toml](../../pyproject.toml) (`[tool.pytest.ini_options]`). The contract plugin registers `contract`.

- `slow`: a subprocess or import-lint test. It still runs in `make test`.
- `network`: needs a real socket. Never in `make test`; `make test-integration` runs it.
- `requires_env(variable, *, context=None, hint="")`: a drill. Also `network`. The `poc_harness.plugin` pytest plugin reads it; the named marks are in `poc_harness.drills`.
- `contract`: every case of a contract suite.

A drill runs only when its gate variable is `1`. With `requires_env`, the variable on and a `context` kubectl lacks is an error, not a skip, so a kind run cannot pass having checked nothing.

| Variable | Gates |
| -------- | ----- |
| `POC04_STACK` | PoC-4 Compose drills |
| `POC04_KIND` | PoC-4 kind drills |
| `POC05_KIND` | PoC-5 kind suites |
| `POC06_KIND` | PoC-6 kind tests |
| `POC07_STACK` | PoC-7 real-service stack tests |

PoC-8 adds no gate variable.
