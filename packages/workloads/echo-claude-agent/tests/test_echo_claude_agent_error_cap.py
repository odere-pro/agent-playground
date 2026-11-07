"""A huge upstream error keeps its code and retryable flag (contract v8, C).

The mapping keeps the full text on purpose: `handle` scrubs the token from it, then cuts it to
`workload_a2a.events.MAX_MESSAGE`, so no part of the token is left at the edge of a cut.
"""

from __future__ import annotations

from claude_agent_sdk import ResultMessage
from echo_claude_agent.mapping import result_error

HUGE = "x" * 20_000


def _failed(status: int) -> ResultMessage:
    return ResultMessage(
        subtype="error_during_execution",
        duration_ms=1,
        duration_api_ms=1,
        is_error=True,
        num_turns=1,
        session_id="s-1",
        result=HUGE,
        api_error_status=status,
    )


def test_a_20000_char_upstream_error_keeps_its_code() -> None:
    overloaded = result_error(_failed(529))
    assert (overloaded["code"], overloaded["retryable"]) == ("http_529", True)
    error = result_error(_failed(400))
    assert (error["code"], error["retryable"]) == ("http_400", False)
    assert error["message"] == HUGE
