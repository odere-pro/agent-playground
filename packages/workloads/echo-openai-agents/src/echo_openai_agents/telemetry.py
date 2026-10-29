"""This engine's observability wiring (PoC-7): the OpenInference OpenAI Agents instrumentor.

`workload_a2a.telemetry` calls `instrument` when the workload's tracing is configured; it is
registered when `echo_openai_agents.handle` is imported. The SDK's own trace export to OpenAI
stays off: the instrumentor is the only trace processor (`exclusive_processor=True`), so the SDK's
spans become OpenInference spans under `workload.handle` and nothing else. Only while it is on is
the SDK's tracing switched on (`TRACING.enabled`); `handle` reads it for each run's `RunConfig`.
The processor makes each SDK span current, so the model call's HTTP request carries the
generation span's `traceparent`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents import set_tracing_disabled
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor


class _Tracing:
    enabled = False


TRACING = _Tracing()


def instrument(tracer_provider: Any) -> Callable[[], None]:
    """Turn the instrumentor on as the only SDK trace processor; return how to turn it off."""
    instrumentor = OpenAIAgentsInstrumentor()
    instrumentor.instrument(tracer_provider=tracer_provider, exclusive_processor=True)
    set_tracing_disabled(False)
    TRACING.enabled = True

    def undo() -> None:
        TRACING.enabled = False
        set_tracing_disabled(True)
        instrumentor.uninstrument()
        from agents.tracing import set_trace_processors

        set_trace_processors([])

    return undo
