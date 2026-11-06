"""One generated fake-model script, rewritten for the engine that reads it (`Engine.dialect`).

The golden set and the red-team set are generated once, as OpenAI tool calls (`openai`). Two
engines read a script differently, as their own PoC-6 scripts show:

- `claude` (`echo-claude-agent`, `bakeoff-claude.yaml`): the Claude CLI names an MCP tool
  `mcp__chassis__<name>`, so each `tool_call` name gets that prefix. The workload strips it from
  its events, so the eval checks see the plain name.
- `smolagents` (`echo-smolagents`, `smolagents.yaml`): a CodeAgent wants Python in a `<code>`
  block and ends with `final_answer(...)`. A tool call becomes `print(<tool>(<args>))`, an answer
  `final_answer("<text>")`. The tool's printed result comes back as the next user message, so an
  `after_tool` rule becomes a plain rule on that text. Task rules go first: a task input never
  appears in a tool result, but a short tool-result key (an unknown term) can appear in a task.

The transform is pure and deterministic; the stack writes its output next to the run's other
temporary files.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from bakeoff.registry import Engine

__all__ = ["CLAUDE_TOOL_PREFIX", "for_engine"]

CLAUDE_TOOL_PREFIX = "mcp__chassis__"


def _claude(script: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(script)
    for rule in out["rules"]:
        call = rule.get("tool_call")
        if call:
            call["name"] = CLAUDE_TOOL_PREFIX + call["name"]
    return out


def _code(rule: dict[str, Any]) -> str:
    call = rule.get("tool_call")
    if call:
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in call["arguments"].items())
        return f"Thought: Call {call['name']}.\n<code>\nprint({call['name']}({args}))\n</code>"
    return f"Thought: Answer.\n<code>\nfinal_answer({json.dumps(rule['reply'])})\n</code>"


def _smolagents(script: dict[str, Any]) -> dict[str, Any]:
    tasks = [r for r in script["rules"] if not r.get("after_tool")]
    observations = [r for r in script["rules"] if r.get("after_tool")]
    rules = [
        {k: v for k, v in (("match", r.get("match")), ("reply", _code(r))) if v is not None}
        for r in [*tasks, *observations]
    ]
    default = _code({"reply": script.get("default_reply", "ok")})
    return {"model": script.get("model", "fake-model"), "rules": rules, "default_reply": default}


def for_engine(engine: Engine, script: dict[str, Any]) -> dict[str, Any]:
    """`script` (OpenAI dialect) in `engine`'s dialect. The input is not changed."""
    if engine.dialect == "claude":
        return _claude(script)
    if engine.dialect == "smolagents":
        return _smolagents(script)
    return script
