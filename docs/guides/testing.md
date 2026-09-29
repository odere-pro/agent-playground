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
| Integration | real dependencies in containers | PoC-4 onward, testcontainers | every merge |
| End to end | the Compose stack, `local` profile | PoC-1 onward | every merge, nightly |
| Evals | quality on the golden set | PoC-7 | on business logic, prompt, or model change |

## Offline is enforced

`scripts/check_offline.sh` runs pytest with `--disable-socket --allow-unix-socket` (pytest-socket) and unsets every `*_API_KEY`, `*_TOKEN`, `*_SECRET`, and `*_PASSWORD` variable. A test that needs a socket is marked `network` and is not part of any gate. HTTP tests use the httpx ASGI transport, which needs no socket.

## Contract suites

Bind, do not copy:

```python
class TestMyModelAdapter(ModelPortContract):
    @pytest.fixture
    def model_port(self): ...
```

Required fixtures raise `NotImplementedError`; optional cases skip with a hint until overridden. See [adding-a-port.md](adding-a-port.md).

## Markers

`slow` for subprocess or import-lint tests (still in `make test`), `network` for tests that need a socket (never in `make test`), `contract` on every suite case.
