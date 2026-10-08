"""The one contract every service implements: `handle(input, ctx)` yields chassis events."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Metrics, Start

Handle = Callable[[TaskInput, Context], AsyncIterator[Event]]
"""The typed form, internal to the chassis. The wire form a workload writes takes dicts."""


def wire(handle: Handle) -> Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[Event]]:
    """Adapt a typed `Handle` to the wire form: dicts in, events out as models (the template A2A
    server dumps them). For serving the chassis's own `echo` over a lane in tests.
    """

    async def wired(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Event]:
        async for event in handle(TaskInput.model_validate(input), Context.model_validate(ctx)):
            yield event

    return wired


async def echo(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    """The echo workload: streams the input text back, one word per delta."""
    yield Start(request_id=ctx.request_id)
    text = input.text or ""
    words = text.split(" ")
    for i, word in enumerate(words):
        yield Delta(text=word if i == len(words) - 1 else word + " ")
    yield Metrics(input_tokens=len(words), output_tokens=len(words), model_route=ctx.model_route)
    yield End(status="ok")


echo_wire = wire(echo)
"""`echo` in the wire form, for `spec.engine.handle: chassis.core.handle:echo_wire`."""
