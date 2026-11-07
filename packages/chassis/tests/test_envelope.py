from __future__ import annotations

from typing import Any

import pytest
from chassis.core.envelope import Response, Versions
from pydantic import ValidationError

RUN_SECRET_VALUE = "s3cr3t-value"  # pragma: allowlist secret (a test value)

EPIC_EXAMPLE: dict[str, Any] = {
    "request_id": "uuid",
    "trace_id": "uuid",
    "idempotency_key": "uuid",
    "agent": "simplifier",
    "agent_version": "1.0.0",
    "output": {},
    "metrics": {},
    "status": "ok",
    "context_ref": None,
}


def test_epic_example_response_is_accepted() -> None:
    response = Response(**EPIC_EXAMPLE, versions=Versions(chassis="0.1.0"))
    assert response.status == "ok"
    assert response.versions.chassis == "0.1.0"


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Response(**{**EPIC_EXAMPLE, "status": "maybe"}, versions=Versions(chassis="0.1.0"))


def test_versions_is_required() -> None:
    with pytest.raises(ValidationError):
        Response(**EPIC_EXAMPLE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("idempotency_key", "a\nb", id="key-newline"),
        pytest.param("idempotency_key", "\ud800", id="key-lone-surrogate"),
        pytest.param("idempotency_key", "k" * 256, id="key-256-chars"),
        pytest.param("idempotency_key", "", id="key-empty"),
        pytest.param("request_id", "r" * 129, id="request-id-129-chars"),
        pytest.param("trace_id", "a b", id="trace-id-space"),
    ],
)
def test_request_refuses_bad_ids(field: str, value: str) -> None:
    from chassis.core.envelope import Request, TaskInput

    base: dict[str, Any] = {
        "request_id": "r1",
        "trace_id": "t1",
        "idempotency_key": "k1",
        "agent": "a",
        "agent_version": "1",
        "input": TaskInput(text="x"),
    }
    with pytest.raises(ValidationError):
        Request(**{**base, field: value})


def test_request_accepts_ids_at_the_bounds() -> None:
    from chassis.core.envelope import Request, TaskInput

    request = Request(
        request_id="r" * 128,
        trace_id="0af7651916cd43dd8448eb211c80319c",
        idempotency_key="order#42/a~" + "k" * 244,
        agent="a",
        agent_version="1",
        input=TaskInput(text="x"),
    )
    assert len(request.idempotency_key) == 255


def test_context_run_secret_is_optional_and_hidden_from_repr() -> None:
    from chassis.core.envelope import Context

    ctx = Context(
        request_id="r",
        trace_id="t",
        idempotency_key="k",
        agent="a",
        agent_version="1",
        versions=Versions(chassis="0.1.0"),
        run_secret=RUN_SECRET_VALUE,
    )
    assert ctx.run_secret == RUN_SECRET_VALUE
    assert RUN_SECRET_VALUE not in repr(ctx)
    assert ctx.model_copy(update={"run_secret": None}).run_secret is None
