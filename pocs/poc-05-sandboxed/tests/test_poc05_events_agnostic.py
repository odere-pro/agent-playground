"""PoC-5 exit criterion 1 (offline), the events port: it stays broker-agnostic.

Decision 2026-10-02: the queue adapter must be broker-agnostic, and no broker runs in PoC-5 (the
agent configs keep `events: none`). These checks keep that true on every commit:

- `chassis.ports.events` and `chassis.core` import no broker client, directly or through any
  module they import. The control: the same checks find the client in the Kafka adapter.
- Every `events` adapter in `chassis.profiles.REGISTRY` or in `PROFILE_DEFAULTS`, other than
  `none`, has a test class in the repo that binds `chassis_contracts.events.EventPortContract`
  over its implementation, or is in `EXCEPTIONS` with a reason.
- The PoC-5 agent configs under `deploy/kind/poc05` set `events: none` or leave it out.
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from chassis.profiles import PROFILE_DEFAULTS, REGISTRY

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CHASSIS_SRC = REPO / "packages" / "chassis" / "src" / "chassis"
POC05_AGENTS = REPO / "deploy" / "kind" / "poc05"

BROKER_CLIENTS = (
    "kafka",
    "aiokafka",
    "confluent_kafka",
    "pika",
    "aio_pika",
    "google.cloud.pubsub",
    "google.cloud.pubsub_v1",
    "boto3",
    "nats",
    "dapr",
)
"""Top-level modules (or dotted prefixes) of broker clients. `dapr` is the Dapr Python SDK; the
chassis's own Dapr adapter is `chassis.adapters.dapr` and talks HTTP, so it is not matched.
"""

EXCEPTIONS: dict[str, str] = {}
"""`events` adapter -> why it has no `EventPortContract` binding in the repo's tests. Empty:
`memory` binds offline (`packages/chassis/tests/test_events_contract.py`), and `kafka` and
`dapr` bind in `packages/chassis/tests/integration/` (`network`: they need a running broker,
started by `make test-integration`, which PoC-5 does not run). A new adapter with no binding
fails the test below until it gets one or an entry here.
"""


def _is_broker(module: str) -> bool:
    return any(module == b or module.startswith(f"{b}.") for b in BROKER_CLIENTS)


def _imports(path: Path) -> Iterator[str]:
    """Every absolute module name `path` imports, at any depth in the file."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def _broker_imports(paths: list[Path]) -> dict[str, list[str]]:
    found = {
        str(p.relative_to(REPO)): sorted({m for m in _imports(p) if _is_broker(m)}) for p in paths
    }
    return {p: ms for p, ms in found.items() if ms}


AGNOSTIC_FILES = [
    CHASSIS_SRC / "ports" / "events.py",
    *sorted((CHASSIS_SRC / "core").rglob("*.py")),
]


def test_the_events_port_and_core_import_no_broker_client_in_source() -> None:
    """Exit criterion 1 (offline): no file of `chassis.ports.events` or `chassis.core` names a
    broker client in an import. The control: the same scan finds `aiokafka` in the Kafka
    adapter, so an empty result is not a scan that sees nothing.
    """
    assert len(AGNOSTIC_FILES) > 1, AGNOSTIC_FILES
    control = _broker_imports([CHASSIS_SRC / "adapters" / "kafka" / "events.py"])
    assert any("aiokafka" in ms for ms in control.values()), control
    assert _broker_imports(AGNOSTIC_FILES) == {}


def _loaded_brokers(*modules: str) -> list[str]:
    """Import `modules` in a fresh interpreter and list the broker modules it then holds. A fresh
    one, since this pytest process may already hold a broker client from another test.
    """
    code = (
        "import importlib, json, sys\n"
        f"for m in {list(modules)!r}: importlib.import_module(m)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True
    )
    return [m for m in json.loads(out.stdout) if _is_broker(m)]


def test_the_events_port_and_core_load_no_broker_client_at_run_time() -> None:
    """Exit criterion 1 (offline): importing `chassis.ports.events` and `chassis.core`, with all
    they import in turn, loads no broker client module. The control: importing the Kafka
    adapter the same way does load `aiokafka`.
    """
    control = _loaded_brokers("chassis.adapters.kafka")
    loaded = _loaded_brokers("chassis.ports.events", "chassis.core")
    assert "aiokafka" in control, control
    assert loaded == [], loaded


# --- Every events adapter is bound to the contract suite, or excused ---


def _events_adapters() -> set[str]:
    named = set(REGISTRY["events"])
    named |= {spec.events for spec in PROFILE_DEFAULTS.values() if spec.events is not None}
    return named - {"none"}


def _implementation(adapter: str) -> str:
    """The class name behind a registry entry: a class, or a `lazy(...)` factory's target
    (`module:Class.from_env` -> `Class`).
    """
    entry = REGISTRY["events"][adapter]
    if isinstance(entry, str):
        raise AssertionError(f"events: {adapter!r} is not built yet ({entry}); name it here")
    if inspect.isclass(entry):
        return entry.__name__
    target = inspect.getclosurevars(entry).nonlocals
    return str(target["attr"]).split(".")[0]


def _test_files() -> Iterator[Path]:
    for root in (REPO / "packages", REPO / "pocs"):
        for path in root.rglob("test_*.py"):
            if ".venv" not in path.parts and "node_modules" not in path.parts:
                yield path


def _bindings() -> dict[str, list[str]]:
    """Class name -> the `Test*` classes, in the repo's tests, that subclass `EventPortContract`
    and use that class.
    """
    out: dict[str, list[str]] = {}
    for path in _test_files():
        text = path.read_text()
        if "EventPortContract" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if not (isinstance(node, ast.ClassDef) and node.name.startswith("Test")):
                continue
            bases = {ast.unparse(b).split(".")[-1] for b in node.bases}
            if "EventPortContract" not in bases:
                continue
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            used |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            for name in used:
                out.setdefault(name, []).append(f"{path.relative_to(REPO)}::{node.name}")
    return out


BINDINGS = _bindings()


@pytest.mark.parametrize("adapter", sorted(_events_adapters()))
def test_every_events_adapter_binds_the_contract_suite_or_is_excused(adapter: str) -> None:
    """Exit criterion 1 (offline): an `events` adapter other than `none` has a `Test*` class
    that subclasses `EventPortContract` and uses its implementation, or an entry in
    `EXCEPTIONS` with a reason. The control: `InMemoryBus` is found bound offline, so the search
    sees the bindings it is meant to see.
    """
    assert any("test_events_contract.py" in b for b in BINDINGS.get("InMemoryBus", [])), BINDINGS
    implementation = _implementation(adapter)
    reason = EXCEPTIONS.get(adapter, "")
    assert BINDINGS.get(implementation) or reason.strip(), (
        f"events adapter {adapter!r} ({implementation}) has no EventPortContract binding in the "
        "repo's tests and no entry in EXCEPTIONS"
    )


def test_every_exception_names_a_registered_adapter_and_gives_a_reason() -> None:
    """Exit criterion 1 (offline): `EXCEPTIONS` holds no stale or empty entry."""
    adapters = _events_adapters()
    stale = {name: reason for name, reason in EXCEPTIONS.items() if name not in adapters}
    empty = [name for name, reason in EXCEPTIONS.items() if not reason.strip()]
    assert stale == {} and empty == [], (stale, empty)


# --- PoC-5 agent configs: no broker ---


def _agent_configs() -> list[tuple[Path, dict[str, object]]]:
    """Every chassis config under `deploy/kind/poc05`: a YAML document with `spec.adapters` or
    `spec.engine`, whether a file of its own or a ConfigMap value.
    """
    found: list[tuple[Path, dict[str, object]]] = []

    def visit(path: Path, doc: object) -> None:
        if isinstance(doc, dict):
            spec = doc.get("spec")
            if (
                isinstance(spec, dict)
                and ("adapters" in spec or "engine" in spec)
                and "agent" in doc
            ):
                found.append((path, spec))
            if doc.get("kind") == "ConfigMap":
                for value in (doc.get("data") or {}).values():
                    try:
                        visit(path, yaml.safe_load(value))
                    except yaml.YAMLError:
                        continue

    for path in sorted(POC05_AGENTS.rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            visit(path, doc)
    return found


def test_poc05_agent_configs_run_no_broker() -> None:
    """Exit criterion 1 (offline): every PoC-5 chassis config sets `spec.adapters.events` to
    `none` or leaves it out (the profile default, `none` in every profile). The control: at
    least the two agent configs (`echo`, `echo-remote`) are found.
    """
    configs = _agent_configs()
    names = {p.name for p, _ in configs}
    assert {"echo.yaml", "echo-remote.yaml"} <= names, names
    assert {spec.events for spec in PROFILE_DEFAULTS.values()} == {"none"}
    brokers = {
        str(p.relative_to(POC05_AGENTS)): adapters.get("events")
        for p, spec in configs
        if isinstance(adapters := spec.get("adapters") or {}, dict)
        and adapters.get("events", "none") != "none"
    }
    assert brokers == {}, brokers
