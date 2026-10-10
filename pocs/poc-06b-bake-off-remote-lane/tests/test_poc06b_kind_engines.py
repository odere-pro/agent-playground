"""PoC-6b on kind, exit criteria 1 and 4: every PoC-6 engine runs the three benchmark tasks (smoke,
simplifier, lookup) through its chassis's `POST /v1/run` in the PoC-5 cluster, in its lane, and
passes `poc06_harness`'s checks.

The six deployments (`poc06b_kind.ENGINES`): `echo-openai-agents` and `echo-typescript` as sidecar
pods; `echo-smolagents`, `echo-claude-agent`, `echo-typescript`, and `kagent-adk` as remotes on
gVisor behind their own chassis. The model is the fake model server on the PoC-6 script, behind
LiteLLM, and the tools are the fake MCP server's two read-only tools behind the LiteLLM MCP
gateway, so a run crosses every hop a real one does. Each call goes from inside the chassis
container to its pod IP, with the image's Python.

Two adaptations to the hop through the gateway, both in `poc06b_kind.gateway_view` and recorded in
its docstring: the tool name carries the gateway's `fake_tools-` prefix, and a text-only result is
read as the object it holds. Nothing else about an event is changed.

The Claude CLI offers the MCP tools as `mcp__chassis__fake_tools-<name>`. The fake model server
answers a scripted call by the one offered name that ends with the scripted name
(`packages/fake-model-server`, `_resolve`), and the workload strips `mcp__chassis__` from its
events, so Claude runs the same rules as every other engine. (Kind run 4 had it as an xfail.)

Known gap:
- `kagent-adk` lookup is checked on the answer text only. In plain-A2A mode the chassis cannot see
  tool calls ("what the chassis cannot see"), so a `tool_call` event there is a failure of the
  limit, not a pass.

A kind test: marked `kind` (and `network`), skipped unless `POC06_KIND=1` (`poc06b_conftest.py`).
Run: `deploy/kind/poc06/run.sh test`.
"""

from __future__ import annotations

import pytest
from poc06_harness import CHECKS, LOOKUP, Task
from poc06b_kind import (
    ENGINES,
    TASKS,
    KindEngine,
    answer_only_verdict,
    gateway_view,
    run_stream,
)

pytestmark = pytest.mark.kind

CASES = [
    pytest.param(engine, name, id=f"{engine.id}-{name}")
    for engine in ENGINES
    for name in ("smoke", "simplifier", "lookup")
]


def task_named(name: str) -> Task:
    (task,) = [t for t in TASKS if t.name == name]
    return task


@pytest.mark.parametrize(("engine", "name"), CASES)
def test_the_engine_passes_the_task_through_its_chassis(engine: KindEngine, name: str) -> None:
    """Exit criteria 1 and 4: smoke (`end ok` after a `delta`, the whole answer `Hello.`), the
    simplifier (every fact kept), and the lookup (`glossary_lookup{term: SLM}` then
    `acronym_expand{acronym: RAG}`, both results non-null, the answer holds both facts), each
    checked by `poc06_harness`. A failure prints the raw events."""
    task = task_named(name)
    result = run_stream(engine, engine.text(task))
    assert result.status == 200, (result.status, result.head)
    events = gateway_view(result.events)
    if engine.plain and task is LOOKUP:
        verdict = answer_only_verdict(task, events)
    else:
        verdict = CHECKS[name](events)
    assert verdict.passed, (verdict.problems, result.events)
    if engine.plain:
        assert not [e for e in result.events if e.get("type") == "tool_call"], (
            "plain-A2A mode shows no tool call; the chassis cannot see the remote's tools"
        )
    if result.envelope:
        assert result.envelope.get("status") == "ok", result.envelope
