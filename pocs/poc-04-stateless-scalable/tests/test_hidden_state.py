"""PoC-4 exit criterion 8, in the README's words:

"Engines that keep hidden state are listed, with a way to move the state out or a note to reject them."

This file covers the first clause: it finds the hidden state an engine keeps, by behavior and by
inspection. The list and the way out for each engine are in `notes/2026-10-01-hidden-state.md`.

For each engine (echo-python, echo-pydanticai, echo-langgraph, echo-typescript) in the `sidecar`
lane, two replicas, each with its own workload instance. Run A carries a marker and goes to
replica 0. Then run B goes to replica 0 (the reused workload) and to replica 1 (a fresh one).
Nothing from A may show in B: not in B's answer, not in what B sends the model, and the reused
workload answers B as the fresh one does. The 057 H-19 kept-data check, with the fake model. The
runs happen once per engine, in the `runs` fixture; each test checks one channel.

What each engine could keep, and what is checked:

- **The A2A template server** (`workload_a2a`) keeps tasks in an `InMemoryTaskStore`. It prunes a
  task once it is terminal; checked: no task is left after the runs.
- **Module globals.** `echo_python.tools.list_failures` and `echo_langgraph.tools.list_failures`
  are process-wide counters of failed MCP tool listings: hidden state, but no request data, and
  they move only on a failure. Checked: no module global of a workload package changes over A and
  B. (The Python workloads run in this process, so a fresh workload instance shares module
  globals with the reused one; this check is what covers them.)
- **LangGraph** keeps a run's messages only with a checkpointer (or a store) on the compiled
  graph. Checked: `echo_langgraph` compiles its graph per call with neither.
- **PydanticAI** keeps a conversation only when the caller passes `message_history`. Checked:
  `echo_pydanticai` builds its `Agent` per call and passes no history.
- **echo-typescript** runs in its own Node process per workload; only the behavior is checked.
- **Files.** Nothing is written into the workload's package folder. The real check of "nothing
  outside /tmp" is the Compose drill (`docker diff` on read-only roots, `test_compose_scale.py`).

The list, with ways out, is `notes/2026-10-01-hidden-state.md`.
Offline: Unix sockets only. The TypeScript cell skips when its `node_modules` is absent.
"""  # noqa: E501

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from poc04_harness import SIMPLIFIED, TYPESCRIPT, HarnessModel, call, replicas_on_unix_sockets
from poc_harness.interfaces import ENGINES, OutboundRouter
from poc_harness.outbound import patch_outbound

ROOT = Path(__file__).resolve().parents[3]
WORKLOADS = ROOT / "packages/workloads"
FOLDERS = {
    "echo_python": "echo-python",
    "echo_pydanticai": "echo-pydanticai",
    "echo_langgraph": "echo-langgraph",
    TYPESCRIPT: "echo-typescript",
}
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}
PACKAGES = ("echo_python", "echo_pydanticai", "echo_langgraph", "workload_a2a")
RUN_IDS = ("request_id", "trace_id", "idempotency_key", "metrics")
"""Per-run fields of an envelope: minted per call, and the timings."""
PRUNE_WAIT_S = 3.0
"""The A2A server prunes a terminal task in a task of its own, just after the answer."""
KEPT_TYPES = (dict, list, set, bytearray, int, float, str, bytes, tuple)


def _files(folder: Path) -> dict[str, int]:
    """Every file under `folder` (build and cache folders aside) and its mtime."""
    found: dict[str, int] = {}
    for path in folder.rglob("*"):
        if SKIP_DIRS.intersection(path.relative_to(folder).parts) or not path.is_file():
            continue
        found[str(path.relative_to(folder))] = path.stat().st_mtime_ns
    return found


def _globals() -> dict[str, str]:
    """`module.name -> repr(value)` for every plain-data module global of the workload
    packages loaded in this process (functions, classes, and modules aside)."""
    seen: dict[str, str] = {}
    for name, module in list(sys.modules.items()):
        if module is None or name.split(".")[0] not in PACKAGES:
            continue
        for attr, value in vars(module).items():
            if attr.startswith("__") or isinstance(value, bool):
                continue
            if isinstance(value, KEPT_TYPES):
                seen[f"{name}.{attr}"] = repr(value)
    return seen


PYTHON_ENGINES = [engine for engine, handle in ENGINES.items() if handle is not None]
"""The engines whose workload runs the Python A2A template server, with its task store."""


async def _until_pruned(live: set[str], label: str) -> None:
    """Wait up to `PRUNE_WAIT_S` for `live` to empty; fail with `label` if it does not."""
    for _ in range(int(PRUNE_WAIT_S / 0.02)):
        if not live:
            return
        await asyncio.sleep(0.02)
    pytest.fail(f"{label}: the A2A server kept a finished task after {PRUNE_WAIT_S} s: {live}")


class Runs:
    """What runs A and B left behind on one engine, recorded once for every test."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.a: Any = None
        self.b_reused: Any = None
        self.b_fresh: Any = None
        self.a_texts: list[str] = []
        self.reused_texts: list[str] = []
        self.fresh_texts: list[str] = []
        self.globals_before: dict[str, str] = {}
        self.globals_after: dict[str, str] = {}
        self.tasks_seen: list[bool] = []
        self.tasks_kept: list[set[str]] = []
        self.files_before: dict[str, int] = {}
        self.files_after: dict[str, int] = {}


async def _run_a_then_b(replicas: Any, model: HarnessModel, runs: Runs) -> None:
    """Run A with the marker on replica 0, then run B on replica 0 and on replica 1. Record what
    each channel holds, then wait for each A2A task store to prune its finished tasks."""
    runs.globals_before = _globals()
    runs.a = await call(replicas[0], "native", f"simplify: remember {runs.marker}", key=None)
    after_a = len(model.calls)
    runs.b_reused = await call(replicas[0], "native", "simplify: what did I say?", key=None)
    after_reused = len(model.calls)
    runs.b_fresh = await call(replicas[1], "native", "simplify: what did I say?", key=None)
    texts = model.user_texts()
    runs.a_texts = texts[:after_a]
    runs.reused_texts = texts[after_a:after_reused]
    runs.fresh_texts = texts[after_reused:]
    runs.globals_after = _globals()
    stores = [w.task_store for w in replicas.workloads if w is not None and w.task_store]
    runs.tasks_seen = [bool(store.ever) for store in stores]
    await asyncio.gather(
        *(_until_pruned(store.live, f"replica {i}") for i, store in enumerate(stores))
    )
    runs.tasks_kept = [set(store.live) for store in stores]


@pytest.fixture(scope="module", params=list(ENGINES))
def runs(request: pytest.FixtureRequest) -> Iterator[Runs]:
    """Runs A and B on one engine, once for every test of that engine."""
    engine: str = request.param
    folder = WORKLOADS / FOLDERS[engine]
    recorded = Runs(f"MARKER-{uuid.uuid4().hex[:12]}")
    recorded.files_before = _files(folder)
    router = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, router.send)
    model = HarnessModel()
    try:
        with replicas_on_unix_sockets(2, engine, "sidecar", model=model, router=router) as replicas:
            asyncio.run(_run_a_then_b(replicas, model, recorded))
    finally:
        patch.undo()
    recorded.files_after = _files(folder)
    yield recorded
    assert router.unrouted == [], router.unrouted


def _answer(body: dict[str, Any]) -> Any:
    """What a run answered, without its per-run ids."""
    return {k: v for k, v in body.items() if k not in RUN_IDS}


def test_run_a_reaches_the_model_with_its_marker_when_sent_to_replica_0(runs: Runs) -> None:
    """Exit criterion 8, the setup the other checks rely on: run A answers ok and its marker is
    in what the workload sends the model."""
    a = runs.a

    assert a.status == 200 and a.body["status"] == "ok", a
    assert any(runs.marker in text for text in runs.a_texts), "A never ran"


def test_run_b_answers_the_same_when_sent_to_the_reused_or_a_fresh_workload(runs: Runs) -> None:
    """Exit criterion 8, the answer: run B answers the same on the reused workload as on a fresh
    one, and it is the plain simplified text."""
    reused, fresh = runs.b_reused, runs.b_fresh

    assert (reused.status, fresh.status) == (200, 200), (reused, fresh)
    assert reused.body["output"]["text"] == SIMPLIFIED, reused.body
    assert _answer(reused.body) == _answer(fresh.body)


def test_run_b_answer_holds_no_marker_when_run_a_carried_one(runs: Runs) -> None:
    """Exit criterion 8, the answer: neither B answer holds A's marker."""
    bodies = [str(runs.b_reused.body), str(runs.b_fresh.body)]

    assert [body for body in bodies if runs.marker in body] == [], "A leaked into B's answer"


def test_run_b_model_calls_hold_no_marker_when_run_a_carried_one(runs: Runs) -> None:
    """Exit criterion 8, the model input: B calls the model on both workloads, and no call of B
    holds A's marker."""
    b_texts = runs.reused_texts + runs.fresh_texts

    assert runs.reused_texts and runs.fresh_texts, "B made no model call"
    assert [text for text in b_texts if runs.marker in text] == [], "A leaked into B"


def test_reused_workload_sends_the_model_what_a_fresh_one_does_when_run_b_follows_run_a(
    runs: Runs,
) -> None:
    """Exit criterion 8, the model input: what the reused workload sends the model for B is what
    the fresh one sends."""
    assert runs.reused_texts == runs.fresh_texts, (
        "the reused workload sent the model something else"
    )


@pytest.mark.parametrize("runs", PYTHON_ENGINES, indirect=True)
def test_no_a2a_task_is_kept_when_runs_a_and_b_have_ended(runs: Runs) -> None:
    """Exit criterion 8, the task store: each replica's A2A server saw a task and kept none once
    it ended. (echo-typescript prunes in its own process; `test/prune.test.ts` checks it.)"""
    assert runs.tasks_seen == [True, True], "a task store saw no task"
    assert runs.tasks_kept == [set(), set()], "the A2A server kept a finished task"


def test_no_workload_module_global_changes_when_runs_a_and_b_run(runs: Runs) -> None:
    """Exit criterion 8, module globals: no plain-data global of a workload package moved."""
    moved = {
        k: (runs.globals_before.get(k), v)
        for k, v in runs.globals_after.items()
        if runs.globals_before.get(k) != v
    }

    assert runs.globals_after == runs.globals_before, moved


def test_no_file_is_written_in_the_workload_folder_when_runs_a_and_b_run(runs: Runs) -> None:
    """Exit criterion 8, files: the workload wrote nothing into its package folder."""
    assert runs.files_after == runs.files_before, "the workload wrote into its package folder"


def test_langgraph_compiles_its_graph_without_a_checkpointer_or_store() -> None:
    """Exit criterion 8 (LangGraph): a checkpointer or a store would keep a run's messages across
    calls. `echo_langgraph` builds and compiles its graph per call, with neither."""
    handle = importlib.import_module("echo_langgraph.handle")
    graph = handle._graph(_NoToolsModel(), [])
    assert graph.checkpointer is None
    assert graph.store is None
    assert "_graph(" in inspect.getsource(handle.handle), "the graph is no longer built per call"


def test_pydanticai_builds_its_agent_per_call_and_passes_no_history() -> None:
    """Exit criterion 8 (PydanticAI): an `Agent` keeps no conversation by itself; one is carried
    over only through `message_history`. `echo_pydanticai` builds its `Agent` inside `handle`
    and never passes a history."""
    handle = importlib.import_module("echo_pydanticai.handle")
    source = inspect.getsource(handle)
    assert "message_history" not in source
    assert "_agent(" in inspect.getsource(handle.handle), "the agent is no longer built per call"


class _NoToolsModel:
    """Stands in for `ChatOpenAI` at compile time: with no tools, `_graph` only stores it."""

    async def ainvoke(self, messages: Any) -> Any:
        raise AssertionError("not called at compile time")
