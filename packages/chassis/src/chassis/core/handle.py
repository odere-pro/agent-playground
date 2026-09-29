"""The one contract every service implements: `handle(input, ctx)` yields chassis events."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Metrics, Start

Handle = Callable[[TaskInput, Context], AsyncIterator[Event]]


async def echo(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    """The echo workload: streams the input text back, one word per delta."""
    yield Start(request_id=ctx.request_id)
    text = input.text or ""
    words = text.split(" ")
    for i, word in enumerate(words):
        yield Delta(text=word if i == len(words) - 1 else word + " ")
    yield Metrics(input_tokens=len(words), output_tokens=len(words), model_route=ctx.model_route)
    yield End(status="ok")
