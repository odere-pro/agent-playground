# packages/contract-suites

One pytest contract suite per chassis port. A fake and every real adapter bind the same class, so a fake that passes behaves like the real thing.

## How to bind

```python
from chassis_contracts import ModelPortContract

class TestMyAdapter(ModelPortContract):
    @pytest.fixture
    def model_port(self): ...
    @pytest.fixture
    def tool_call_case(self): ...     # optional case fixtures; skipped when not overridden
```

Contract classes are named `*Contract` so pytest does not collect them on their own. Fixtures the binder must provide raise `NotImplementedError`; optional cases `pytest.skip` with a hint.

## Rules

- A suite tests the promise of the port, not an adapter's internals: streaming and complete agree, errors surface as the port's error type, ordering and cancel hold, versions change on change.
- Do not copy cases into an adapter's tests. Extend the suite, and every adapter gets the case.
- `pytest` fixture names `request`, `config`, and `cache` are reserved; use `run_request` and the like.
- The plugin (`plugin.py`) only registers the `contract` marker. Keep it that way.

## Test

`uv run pytest packages/contract-suites` runs the suites against the chassis fakes.
