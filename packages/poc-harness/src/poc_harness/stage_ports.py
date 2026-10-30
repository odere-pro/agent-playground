"""The tool box and the recording guardrails of the full stack."""

from __future__ import annotations

from typing import Any

import httpx
from chassis.adapters.litellm.guardrails import LiteLLMGuardrails
from chassis.fakes.tool import GLOSSARY_LOOKUP, InMemoryTools
from chassis.ports.guardrail import GuardrailPort, Stage, Verdict
from chassis.ports.tool import ToolDefinition
from guardrail_server import PatternClassifier
from guardrail_server import create_app as create_guard
from guardrail_server.litellm_stub import StubSettings, create_stub

from poc_harness.stage_model import log

STUB_KEY = "poc08-stub-key-not-real"  # pragma: allowlist secret (a test value)
FETCH_PAGE = ToolDefinition(
    name="fetch_page",
    description="Fetch a page, or store a note",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}},
)


class ToolBox:
    """`glossary_lookup` and the red-team `fetch_page`, whose output is `page`."""

    def __init__(self) -> None:
        self.page = "A plain product page."
        self.tools = InMemoryTools(
            [
                (GLOSSARY_LOOKUP, lambda term: {"term": term, "definition": "A small model."}),
                (FETCH_PAGE, lambda text="": self.page),
            ]
        )

    @property
    def calls(self) -> list[Any]:
        return list(self.tools.calls)


class RecordingGuardrails:
    """A `GuardrailPort` around another: records each stage it was asked to check."""

    def __init__(self, inner: GuardrailPort) -> None:
        self.inner = inner
        self.name = inner.name
        self.stages: list[Stage] = []

    async def check(self, text: str, stage: Stage) -> Verdict:
        self.stages.append(stage)
        log.info("guardrail check", extra={"stage": stage})
        return await self.inner.check(text, stage)


def litellm_guardrails() -> LiteLLMGuardrails:
    """The PoC-7 stack: the LiteLLM stub -> the guardrail server (pattern stand-in)."""
    guard = create_guard(PatternClassifier())
    stub = create_stub(
        StubSettings(master_key=STUB_KEY), guardrail_transport=httpx.ASGITransport(app=guard)
    )
    return LiteLLMGuardrails(
        "http://litellm.invalid/v1", STUB_KEY, transport=httpx.ASGITransport(app=stub)
    )
