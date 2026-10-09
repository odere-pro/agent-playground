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

Known gaps, each a recorded `xfail`, not a dropped test:
- `echo-claude-agent` lookup. The Claude CLI prefixes MCP tool names with `mcp__chassis__` and adds
  trailing system turns, so the fake model's scripted tool call does not name a tool the CLI
  offered. A fix to the script is coming separately; `strict=False`, so it shows when it works.
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

CLAUDE_LOOKUP = (
    "The Claude CLI offers MCP tools as mcp__chassis__<name> and adds trailing system turns; the "
    "fake model's scripted tool call names a tool the CLI did not offer. A script fix is coming "
    "separately."
)

CASES = [
    pytest.param(
        engine,
        name,
        id=f"{engine.id}-{name}",
        marks=(
            [pytest.mark.xfail(strict=False, reason=CLAUDE_LOOKUP)]
            if engine.name == "echo-claude-agent" and name == "lookup"
            else []
        ),
    )
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
