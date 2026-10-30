"""The event helpers the Python workloads share (`workload_a2a.events`). Offline."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from workload_a2a import events
from workload_a2a.events import DEFAULT_TIMEOUT_S, MAX_MESSAGE, chain, error, event, timeout_s

CTXS: list[dict[str, Any]] = [
    {},
    {"budget": {"timeout_ms": 1500}},
    {"budget": {"timeout_ms": 0}},
    {"budget": {}},
    {"budget": "not a dict"},
]


def test_an_event_has_the_schema_version_and_the_type_first() -> None:
    assert event("delta", text="hi") == {"schema_version": "0", "type": "delta", "text": "hi"}
    assert list(event("end", status="ok")) == ["schema_version", "type", "status"]


def test_an_error_cuts_the_message_at_500_characters() -> None:
    assert MAX_MESSAGE == 500
    out = error("timeout", "x" * 600, True)
    assert out == {
        "schema_version": "0",
        "type": "error",
        "code": "timeout",
        "message": "x" * 500,
        "retryable": True,
    }


def test_the_timeout_is_the_budget_in_seconds_else_30() -> None:
    assert DEFAULT_TIMEOUT_S == 30.0
    assert [timeout_s(ctx) for ctx in CTXS] == [30.0, 1.5, 30.0, 30.0, 30.0]


def test_the_chain_walks_causes_contexts_and_groups_once_each() -> None:
    root = ValueError("root")
    cause = KeyError("cause")
    root.__cause__ = cause
    member = TypeError("member")
    group = ExceptionGroup("group", [member, root])
    try:
        raise RuntimeError("top") from group
    except RuntimeError as top:
        top.__context__ = top.__cause__  # the same object twice: yielded once
        found = list(chain(top))
    assert found[0].args == ("top",)
    assert {id(e) for e in found} == {id(e) for e in (found[0], group, root, cause, member)}
    assert len(found) == 5


WORKLOADS = ["echo_openai_agents", "echo_smolagents", "echo_claude_agent", "echo_pydanticai"]


@pytest.mark.parametrize("package", WORKLOADS)
@pytest.mark.parametrize("ctx", CTXS, ids=str)
def test_every_workload_computes_its_timeout_with_the_shared_helper(
    package: str, ctx: dict[str, Any]
) -> None:
    pytest.importorskip(package)
    handle = importlib.import_module(f"{package}.handle")
    assert handle.timeout_s is events.timeout_s
    assert handle.DEFAULT_TIMEOUT_S == events.DEFAULT_TIMEOUT_S
    assert handle.timeout_s(ctx) == events.timeout_s(ctx)
