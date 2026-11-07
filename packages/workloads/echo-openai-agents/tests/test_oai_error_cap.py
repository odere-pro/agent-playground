"""A huge upstream error body is cut, and keeps its code and retryable flag (contract v8, C)."""

from __future__ import annotations

import httpx2
import openai
from echo_openai_agents.mapping import error_event

HUGE = "x" * 20_000
ERROR_MESSAGE_MAX = 8192
"""Contract v8, C: the v0 schema's bound on `error.message`. `workload_a2a.events` cuts lower."""


def test_a_20000_char_upstream_error_is_cut_and_keeps_its_code() -> None:
    request = httpx2.Request("POST", "http://chassis/v1/chat/completions")
    response = httpx2.Response(429, request=request)
    error = error_event(openai.APIStatusError(HUGE, response=response, body={"message": HUGE}))
    assert (error["code"], error["retryable"]) == ("http_429", False)
    assert 0 < len(error["message"]) <= ERROR_MESSAGE_MAX
