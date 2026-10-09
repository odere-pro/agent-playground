"""The echo-claude-agent skeleton: `handle` yields `start`, then `error` (`not_implemented`).

Both events validate against `packages/chassis/schemas/events.v0.json`. No socket, no key, no
chassis import: the schema is read from its file by path. PoC-6 scaffold.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from echo_claude_agent import handle
from echo_claude_agent import handle as _exported  # noqa: F401  (the package exports `handle`)

ROOT = Path(__file__).resolve().parents[4]
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())


async def _run() -> list[dict[str, Any]]:
    return [e async for e in handle({"text": "simplify: Hello."}, {"request_id": "req-1"})]


async def test_the_skeleton_yields_start_then_not_implemented() -> None:
    events = await _run()
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[0]["request_id"] == "req-1"
    assert events[1]["code"] == "not_implemented"
    assert events[1]["retryable"] is False


async def test_the_skeleton_events_validate_against_the_event_schema() -> None:
    for event in await _run():
        jsonschema.validate(event, EVENTS_SCHEMA)
        assert event["schema_version"] == "0"


def test_the_test_hooks_exist_and_are_unset() -> None:
    import importlib

    model_hook: Any = importlib.import_module("echo_claude_agent.handle")
    tool_hook: Any = importlib.import_module("echo_claude_agent.tools")
    assert model_hook.transport is None
    assert tool_hook.transport is None
