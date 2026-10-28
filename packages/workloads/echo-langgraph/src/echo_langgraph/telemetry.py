"""This engine's observability wiring (PoC-7): the OpenInference LangChain instrumentor.

`workload_a2a.telemetry` calls `instrument` when the workload's tracing is configured; it is
registered when `echo_langgraph.handle` is imported. The graph, chain, chat model, and tool runs
then become OpenInference spans under `workload.handle`. The instrumentor does not make its spans
current (by design: callbacks may not end), so an outbound HTTP call is made under
`workload.handle`, and the chassis model proxy span hangs off that span, next to the LLM span.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openinference.instrumentation.langchain import LangChainInstrumentor


def instrument(tracer_provider: Any) -> Callable[[], None]:
    """Turn the LangChain instrumentor on for `tracer_provider`; return how to turn it off."""
    instrumentor = LangChainInstrumentor()
    instrumentor.instrument(tracer_provider=tracer_provider)

    def undo() -> None:
        instrumentor.uninstrument()

    return undo
