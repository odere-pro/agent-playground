from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from chassis import CHASSIS_VERSION
from chassis.core.collector import collect
from chassis.core.envelope import Context, Request, Versions
from chassis.core.events import Delta, End, Error, Event, Metrics, Start, ToolCall
from chassis.core.handle import echo


async def _stream(events: Sequence[Event]) -> AsyncIterator[Event]:
    for e in events:
        yield e


VERSIONS = Versions(chassis=CHASSIS_VERSION)


async def test_echo_streams_and_collects_the_same_text(request_: Request, context: Context) -> None:
    events = [e async for e in echo(request_.input, context)]
    assert isinstance(events[0], Start) and isinstance(events[-1], End)
    streamed = "".join(e.text for e in events if isinstance(e, Delta))
    response = await collect(_stream(events), request_, VERSIONS)
    assert streamed == response.output["text"] == "hello big world"
    assert response.status == "ok"
    assert response.versions.chassis == CHASSIS_VERSION
    assert response.metrics["input_tokens"] == 3


async def test_tool_calls_and_metrics_are_kept(request_: Request) -> None:
    events: list[Event] = [
        Start(request_id="req-1"),
        ToolCall(call_id="c1", name="lookup", arguments={"q": "x"}),
        Metrics(input_tokens=5, output_tokens=2, cost_usd=0.001, attempt=1),
        Metrics(input_tokens=5, output_tokens=3, cost_usd=0.002, attempt=2, model_route="big"),
        Delta(text="done"),
        End(status="fallback"),
    ]
    response = await collect(_stream(events), request_, VERSIONS)
    assert response.status == "fallback"
    assert response.output["tool_calls"][0]["name"] == "lookup"
    assert response.metrics == {
        "input_tokens": 10,
        "output_tokens": 5,
        "attempts": 2,
        "cost_usd": 0.003,
        "model_route": "big",
    }


async def test_error_gives_status_error(request_: Request) -> None:
    events: list[Event] = [Start(request_id="req-1"), Error(code="timeout", message="too slow")]
    response = await collect(_stream(events), request_, VERSIONS)
    assert response.status == "error"
    assert response.output["error"] == {"code": "timeout", "message": "too slow"}


async def test_end_output_wins_over_joined_text(request_: Request) -> None:
    events: list[Event] = [Start(request_id="req-1"), Delta(text="raw"), End(output={"json": 1})]
    response = await collect(_stream(events), request_, VERSIONS)
    assert response.output == {"json": 1}
