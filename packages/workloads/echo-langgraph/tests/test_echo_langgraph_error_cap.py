"""A huge upstream error body is cut, and keeps its code and retryable flag (contract v8, C)."""

from __future__ import annotations

import httpx2
import openai
from echo_langgraph.mapping import ERROR_MESSAGE_MAX, error_event

HUGE = "x" * 20_000
URL = "http://chassis/v1/chat/completions"


def test_a_20000_char_upstream_error_is_cut_and_keeps_its_code() -> None:
    response = httpx2.Response(503, request=httpx2.Request("POST", URL))
    error = error_event(openai.APIStatusError(HUGE, response=response, body=None))
    assert ERROR_MESSAGE_MAX == 8192
    assert (error["code"], error["retryable"]) == ("http_503", True)
    assert 0 < len(error["message"]) <= ERROR_MESSAGE_MAX
