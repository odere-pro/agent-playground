"""Model-script dialects: one generated script, rewritten for the engine that reads it. Offline."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

import pytest
from bakeoff.dialects import CLAUDE_TOOL_PREFIX, for_engine
from bakeoff.golden import ToolCallSpec, load_gate_cases
from bakeoff.golden.generate import model_script, tool_result
from bakeoff.golden.logic import LOGICS, apply_logic
from bakeoff.registry import ENGINES, get_engine
from fake_model_server import Script

CODE = re.compile(r"<code>\n(.*)\n</code>", re.S)


def test_bakeoff_dialects_every_engine_names_a_known_dialect() -> None:
    assert {e.dialect for e in ENGINES} <= {"openai", "claude", "smolagents"}
    assert get_engine("echo-claude-agent").dialect == "claude"
    assert get_engine("echo-smolagents").dialect == "smolagents"
    assert get_engine("echo-python").dialect == "openai"


def test_bakeoff_dialects_openai_is_unchanged() -> None:
    script = model_script(load_gate_cases())
    assert for_engine(get_engine("echo-langgraph"), script) == script


def test_bakeoff_dialects_claude_prefixes_every_tool_name() -> None:
    script = model_script(load_gate_cases())
    claude = for_engine(get_engine("echo-claude-agent"), script)
    calls = [r["tool_call"] for r in claude["rules"] if r.get("tool_call")]
    assert calls and all(c["name"].startswith(CLAUDE_TOOL_PREFIX) for c in calls)
    assert script["rules"][0].get("tool_call") is None or not script["rules"][0]["tool_call"][
        "name"
    ].startswith(CLAUDE_TOOL_PREFIX), "the input script is not changed"


def _run_code_agent(script: Script, task: str) -> tuple[list[dict[str, Any]], str]:
    """Drive a script the way the smolagents CodeAgent does: the task, then each tool's printed
    result as the next user message, until `final_answer`."""
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "You are an expert assistant who can solve any task using code blobs.",
        },
        {"role": "user", "content": f"New task:\n{task}"},
    ]
    calls: list[dict[str, Any]] = []
    for _ in range(6):
        reply = script.pick(messages).reply
        code = CODE.search(reply)
        assert code, reply
        call = ast.parse(code.group(1)).body[0].value  # type: ignore[attr-defined]
        if call.func.id == "final_answer":
            return calls, ast.literal_eval(call.args[0])
        inner = call.args[0]
        args = {k.arg: ast.literal_eval(k.value) for k in inner.keywords}
        calls.append({"name": inner.func.id, "arguments": args})
        result = tool_result(ToolCallSpec(inner.func.id, args))
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": f"Observation:\nExecution logs:\n{json.dumps(result)}\n"},
        ]
    raise AssertionError("the script loops")


@pytest.mark.parametrize("logic", LOGICS)
def test_bakeoff_dialects_smolagents_answers_every_gate_case_in_code(logic: str) -> None:
    cases = apply_logic(load_gate_cases(), logic)
    raw = for_engine(get_engine("echo-smolagents"), model_script(cases))
    script = Script.model_validate(raw)
    assert all(not r.get("after_tool") for r in raw["rules"])
    for case in cases:
        calls, answer = _run_code_agent(script, case.input)
        assert calls == [c.as_dict() for c in case.model_calls], case.id
        assert answer == case.reference, case.id
    _, default = _run_code_agent(script, "something unknown")
    assert default == "ok"
