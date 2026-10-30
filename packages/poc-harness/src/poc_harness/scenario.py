"""The scripted scenario every engine runs: prompts, answers, routes, URLs, and the engines.

The fake model server and `ToolLoopModel` (`poc_harness.scripted_model`) answer these prompts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

SIMPLIFIED = "Plain words. Short sentences. Same facts."
"""What the fake model server answers to `simplify: ...` (usage 42 in, 9 out)."""
SIMPLIFY = "simplify: the quick brown fox"
TOOL_PROMPT = "simplify: what does SLM mean? look it up"
AFTER_TOOL = "SLM means a small language model."
"""What `ToolLoopModel` answers once it sees a tool result."""
NO_TOOL = "no glossary_lookup tool was offered"
ROUTE = "big-default"
MODEL_URL = "http://127.0.0.1:8080/v1"
MCP_PATH = "/mcp"
SIDECAR_URL = "http://127.0.0.1:9100"
"""The URL a sidecar's agent card names. The connector reaches it over `uds`; no TCP."""
TYPESCRIPT_URL = "http://127.0.0.1:9000"
"""What the TypeScript echo's agent card names by default; over `uds` it only fills `Host`."""
CHASSIS_PROXY_URL = "http://127.0.0.1:8090"
"""Where a workload's model and MCP calls go (`CHASSIS_MODEL_URL`, `CHASSIS_TOOL_URL`)."""
TIMEOUT_S = 30.0
"""Every async scenario runs under this deadline, so a half-built feature fails, not hangs."""

PYTHON_ENGINES = ("echo_python:handle", "echo_pydanticai:handle", "echo_langgraph:handle")


def engine_params(extra: Mapping[str, str] | None = None) -> list[Any]:
    """The three Python engines as parameters. An engine named in `extra` is strict xfail with
    that reason (for example, a client that drops a header, with the fix).
    """
    reasons = dict(extra or {})
    return [
        pytest.param(
            handle,
            id=handle.split(":")[0],
            marks=[pytest.mark.xfail(strict=True, reason=reasons[handle])]
            if handle in reasons
            else [],
        )
        for handle in PYTHON_ENGINES
    ]
