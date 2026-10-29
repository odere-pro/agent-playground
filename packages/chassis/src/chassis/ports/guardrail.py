"""GuardrailPort: content checks on what goes into and out of a run (PoC-7, backlog 022 H-6).
The real adapter is `LiteLLMGuardrails` (`chassis.adapters.litellm.guardrails`), which calls
LiteLLM's guardrail API, where Presidio (PII) and Llama Prompt Guard 2 (injection) run.

`check(text, stage)` returns a `Verdict`: whether the text may pass, the text to pass on (PII
masked), and why. A port that cannot answer raises `GuardrailError`; the chassis fails closed
unless `spec.guardrails.on_unavailable` says otherwise.

The stages are where the chassis calls the port: `input` (the caller's text, before the engine
runs), `tool_output` (a tool's result, before the workload reads it), and `output` (the answer,
before it leaves). The hard control is the same guardrails run by LiteLLM on every model call
(ADR-001 item 6), which the workload cannot skip; the chassis stages add the checks LiteLLM
cannot see.

`split_for_classifier` is the chunking rule for a classifier that reads a fixed number of tokens
(Prompt Guard 2 reads 512). Pure, no tokenizer: it counts word and punctuation pieces, which a
subword tokenizer splits further, so the budget keeps headroom. suggested: 384 pieces per chunk,
32 pieces of overlap, so an injection on a boundary is seen whole in one chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

Stage = Literal["input", "output", "tool_output"]
STAGES: tuple[Stage, ...] = ("input", "output", "tool_output")

PROMPT_INJECTION = "prompt_injection"
PII = "pii"
"""The two reasons every adapter uses. An adapter may add its own."""

CHUNK_PIECES = 384
"""suggested: pieces per chunk, for a 512-token classifier."""
CHUNK_OVERLAP = 32
"""suggested: pieces shared by two neighbor chunks."""

_PIECE = re.compile(r"\w+|[^\w\s]")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    text: str
    """The text to pass on: the input itself, or with PII masked. Unused when not allowed."""
    reasons: tuple[str, ...] = ()


class GuardrailError(Exception):
    """The port could not check the text. `guardrail_unavailable` (`retryable=True`): the
    guardrail service did not answer or failed with a 5xx.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class GuardrailPort(Protocol):
    name: str

    async def check(self, text: str, stage: Stage) -> Verdict: ...


class NoGuardrails:
    """Guardrails off: every text passes unchanged. The `PortBundle` default and
    `spec.adapters.guardrails: none`."""

    name = "none"

    async def check(self, text: str, stage: Stage) -> Verdict:
        return Verdict(allowed=True, text=text)


def split_for_classifier(
    text: str, pieces: int = CHUNK_PIECES, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """`text` cut into chunks of at most `pieces` word and punctuation pieces, neighbors sharing
    `overlap` pieces. Each chunk is a slice of `text`, so spacing is kept. Short text is one chunk.
    """
    if pieces <= overlap or overlap < 0:
        raise ValueError("pieces must be larger than overlap, and overlap at least 0")
    spans = [m.span() for m in _PIECE.finditer(text)]
    if len(spans) <= pieces:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(spans):
        end = min(start + pieces, len(spans))
        chunks.append(text[spans[start][0] : spans[end - 1][1]])
        if end == len(spans):
            break
        start = end - overlap
    return chunks
