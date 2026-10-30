# poc-harness

Test harness code that more than one PoC folder uses. It replaces the `sys.path` inserts that let a later PoC import an earlier PoC's harness. Not product code: nothing in `packages/chassis/src` imports it (`make lint` enforces it).

## Modules

| Module | What it holds |
| ------ | ------------- |
| `paths` | `ROOT`, the repo root. The one place a module finds files outside the package. |
| `engines` | The PoC-2 base: the chassis app builders, outbound routing into ASGI, servers on Unix sockets, the TypeScript echo launcher, a plain JSON-RPC MCP client. |
| `interfaces` | A real chassis on Unix sockets for every engine and lane, and `OutboundRouter` (PoC-3). |
| `sandbox` | The three lanes with scoped tokens and the remote lane over a Unix socket (PoC-5). |
| `kind` | `kubectl` helpers and the `kind-poc05` names (PoC-5, reused by PoC-6b). |
| `sidecar_controls`, `remote_controls` | Helpers for the kind control tests, shared by PoC-5 and PoC-6b. |
| `admission_policy`, `workflows` | Offline readers of the admission policy and the CI workflow files. |
| `bakeoff_tasks` | The bake-off task spec, pass checks, and engine registry (PoC-6). |
| `sidecar_lane` | The PoC-6a lane bindings: backends on Unix sockets, the TypeScript agent preload. |
| `plain_a2a` | A plain A2A stub agent (no chassis), for the remote connector tests. |
| `drills` | One drill gate: a table of drills in, a `pytest_collection_modifyitems` hook out. |

## Use

```python
from poc_harness.engines import chassis_app, running
```

A PoC conftest declares its drills in a few lines:

```python
pytest_collection_modifyitems = drill_gate(
    Path(__file__).resolve().parent,
    Drill("kind", "POC05_KIND", context="kind-poc05", files="test_poc05_kind_*", ...),
)
```

Tests: `uv run pytest packages/poc-harness`.
