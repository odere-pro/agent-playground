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

## Beyond the ports

- `inbound.py` `InboundAdapterContract`: the same logical request through every inbound adapter gives the same canonical `Request`, and the answer maps back into the format. Pure: no app, no socket. Bound three times (native, OpenAI, Anthropic) in `packages/chassis/tests/test_inbound_contract.py`.
- `interface.py` `InterfaceContract`: every public interface answers every engine the same way, in every lane, end to end, with the official SDK clients. Not exported from `__init__` on purpose: it imports the `openai`, `anthropic`, and `fastmcp` clients. Import it as `chassis_contracts.interface`. Bound in `pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py`.
- PoC-4 port suites: `state.py` `StatePortContract` (`state_port`; `key_prefix` is a fresh uuid per test so a shared Valkey never collides; optional `broken_state_port`), `events.py` `EventPortContract` (`event_port`, a fresh `topic`, `deliver_timeout_s`; optional `broken_event_port`; every published event is checked against the vendored `data/cloudevents-1.0.schema.json`), `config.py` (notification may be eventual within `notify_timeout_s`; `unsubscribe` stops it), `engine.py` (a `probe()` case). The fakes bind them in `packages/chassis/tests/`, the real adapters in `packages/chassis/tests/integration/`.
- `containers/` testcontainers helpers for those real-adapter bindings: `valkey_container`, `minio_container` and `make_bucket`, `kafka_container`, `dapr_with_kafka`. Used only by `network` tests (`make test-integration`, Docker); never imported by the chassis; import `testcontainers.community.*`.

## Rules

- A suite tests the promise of the port, not an adapter's internals: streaming and complete agree, errors surface as the port's error type, ordering and cancel hold, versions change on change.
- Do not copy cases into an adapter's tests. Extend the suite, and every adapter gets the case.
- `pytest` fixture names `request`, `config`, and `cache` are reserved; use `run_request` and the like.
- The plugin (`plugin.py`) only registers the `contract` marker. Keep it that way.
- Model calls replay from cassettes through `recording.CassetteTransport`, set as one client's transport, never vcrpy's global patch (it buffers SSE). Replay (`--record-mode=none`, also the default) never calls `inner`; assert `assert_no_misses()` at teardown. `make record-cassettes` re-records offline. vcrpy is pinned to 8.3.0 because `recording.py` calls the private `Cassette._save`.

## Test

`uv run pytest packages/contract-suites` runs the suites against the chassis fakes.
