"""PoC-4 exit criterion 8: "Any hidden state in the chassis or a workload is found and listed."

For each engine (echo-python, echo-pydanticai, echo-langgraph, echo-typescript) in the `sidecar`
lane, two replicas, each with its own workload instance. Run A carries a marker and goes to
replica 0. Then run B goes to replica 0 (the reused workload) and to replica 1 (a fresh one).
Nothing from A may show in B: not in B's answer, not in what B sends the model, and the reused
workload answers B as the fresh one does. The 057 H-19 kept-data check, with the fake model.

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

The list, with ways out, is `notes/2026-10-xx-hidden-state.md` (the docs package writes it).
Offline: Unix sockets only. The TypeScript cell skips when its `node_modules` is absent.
"""

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
from poc03_harness import ENGINES, OutboundRouter, patch_outbound
from poc04_harness import SIMPLIFIED, TYPESCRIPT, HarnessModel, call, replicas_on_unix_sockets

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


async def _until_pruned(live: set[str]) -> None:
    for _ in range(int(PRUNE_WAIT_S / 0.02)):
        if not live:
            return
        await asyncio.sleep(0.02)


@pytest.fixture
def router() -> Iterator[OutboundRouter]:
    routed = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, routed.send)
    try:
        yield routed
    finally:
        patch.undo()
    assert routed.unrouted == [], routed.unrouted


def _answer(body: dict[str, Any]) -> Any:
    """What B answered, without its per-run ids."""
    return {k: v for k, v in body.items() if k not in RUN_IDS}


@pytest.mark.parametrize("engine", list(ENGINES))
async def test_run_b_sees_nothing_of_run_a_on_a_reused_workload(
    engine: str, router: OutboundRouter
) -> None:
    """Exit criterion 8, per engine: after run A (with a marker) on replica 0, run B answers the
    same on the reused workload (replica 0) and on a fresh one (replica 1); no model call of B
    holds the marker; no A2A task is kept; no workload module global moved; no file was written
    in the workload's folder."""
    marker = f"MARKER-{uuid.uuid4().hex[:12]}"
    folder = WORKLOADS / FOLDERS[engine]
    files_before = _files(folder)
    model = HarnessModel()
    with replicas_on_unix_sockets(2, engine, "sidecar", model=model, router=router) as replicas:
        globals_before = _globals()
        a = await call(replicas[0], "native", f"simplify: remember {marker}", key=None)
        assert a.status == 200 and a.body["status"] == "ok", a
        after_a = len(model.calls)
        assert any(marker in text for text in model.user_texts()[:after_a]), "A never ran"
        b_reused = await call(replicas[0], "native", "simplify: what did I say?", key=None)
        after_reused = len(model.calls)
        b_fresh = await call(replicas[1], "native", "simplify: what did I say?", key=None)
        texts = model.user_texts()
        globals_after = _globals()
        for workload in replicas.workloads:
            if workload is not None and workload.task_store is not None:
                assert workload.task_store.ever, "the task store saw no task"
                await _until_pruned(workload.task_store.live)
                assert workload.task_store.live == set(), "the A2A server kept a finished task"
    assert b_reused.status == b_fresh.status == 200, (b_reused, b_fresh)
    assert b_reused.body["output"]["text"] == SIMPLIFIED, b_reused.body
    assert _answer(b_reused.body) == _answer(b_fresh.body)
    assert marker not in str(b_reused.body) and marker not in str(b_fresh.body)
    reused_calls, fresh_calls = texts[after_a:after_reused], texts[after_reused:]
    assert reused_calls and fresh_calls, "B made no model call"
    assert not any(marker in text for text in reused_calls + fresh_calls), "A leaked into B"
    assert reused_calls == fresh_calls, "the reused workload sent the model something else"
    assert globals_after == globals_before, {
        k: (globals_before.get(k), v)
        for k, v in globals_after.items()
        if globals_before.get(k) != v
    }
    assert _files(folder) == files_before, "the workload wrote into its package folder"


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
