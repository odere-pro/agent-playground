"""A huge upstream error body is cut, and keeps its code and retryable flag (contract v8, C)."""

from __future__ import annotations

from echo_pydanticai.mapping import error_event
from pydantic_ai.exceptions import ModelHTTPError

HUGE = "x" * 20_000
ERROR_MESSAGE_MAX = 8192
"""Contract v8, C: the v0 schema's bound on `error.message`. The mapping cuts lower."""


def test_a_20000_char_upstream_error_is_cut_and_keeps_its_code() -> None:
    error = error_event(ModelHTTPError(502, "route", body={"detail": HUGE}))
    assert (error["code"], error["retryable"]) == ("http_502", True)
    assert 0 < len(error["message"]) <= ERROR_MESSAGE_MAX
