"""`LiteLLMGuardrails`: `GuardrailPort` over LiteLLM's guardrail API (PoC-7, backlog 022 H-6).

One `check(text, stage)` is one or more calls to `POST {root}/guardrails/apply_guardrail` with the
service's LiteLLM key, `{guardrail_name, text, input_type}`, one per guardrail name the stage
lists, in order. `root` is `LITELLM_BASE_URL` without a trailing `/v1` (the model adapter's URL
ends with it; the guardrail route does not).

The names per stage come from the environment (suggested names and defaults):

- `LITELLM_GUARDRAIL_INPUT`: `prompt-guard,pii-mask` (`input_type: request`).
- `LITELLM_GUARDRAIL_TOOL_OUTPUT`: `prompt-guard,secret-guard,pii-mask` (`request`: a tool's
  output goes into the model next).
- `LITELLM_GUARDRAIL_OUTPUT`: `secret-guard,pii-mask` (`response`).
- `LITELLM_GUARDRAIL_CHUNKED`: `prompt-guard`. A name here reads a fixed window (Prompt Guard 2
  reads 512 tokens), so the text is cut with `split_for_classifier` and each chunk is checked; a
  chunk answer never rewrites the text. Any other name sees the whole text, and its
  `response_text` (PII masked) is what the next name and the caller get.

Answers: 200 is allowed, with `text = response_text`, and `pii` in `reasons` when a name changed
the text. 400 or 422 is LiteLLM's "a guardrail blocked this": `Verdict(allowed=False)` with the
reason parsed from the message (`prompt_injection`, `secret`, else `blocked`). 5xx and transport
errors are `GuardrailError("guardrail_unavailable", retryable=True)`; 401 and 403 (the key is
wrong) are `guardrail_unavailable`, not retryable. No message holds the key or the text.

The key goes in `Authorization` only and is never logged or shown in `repr`; the httpx client
ignores the environment (`trust_env=False`), as the model adapter's does.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from chassis.adapters.litellm.client import API_KEY_VAR, BASE_URL_VAR
from chassis.ports.guardrail import (
    PII,
    PROMPT_INJECTION,
    GuardrailError,
    Stage,
    Verdict,
    split_for_classifier,
)
from chassis.redaction import redact_error_text

__all__ = ["APPLY_PATH", "BLOCKED", "SECRET", "LiteLLMGuardrails", "StageNames"]

APPLY_PATH = "/guardrails/apply_guardrail"
SECRET = "secret"  # pragma: allowlist secret (a reason name, not a secret)
BLOCKED = "blocked"
"""The reason of a block whose message names no known reason."""

INPUT_VAR = "LITELLM_GUARDRAIL_INPUT"
OUTPUT_VAR = "LITELLM_GUARDRAIL_OUTPUT"
TOOL_OUTPUT_VAR = "LITELLM_GUARDRAIL_TOOL_OUTPUT"
CHUNKED_VAR = "LITELLM_GUARDRAIL_CHUNKED"
TIMEOUT_S = 10.0
"""suggested: one apply call's timeout."""

_INPUT_TYPE: dict[Stage, str] = {"input": "request", "tool_output": "request", "output": "response"}
_REASONS: tuple[tuple[str, str], ...] = (
    ("prompt_injection", PROMPT_INJECTION),
    ("injection", PROMPT_INJECTION),
    ("jailbreak", PROMPT_INJECTION),
    ("secret", SECRET),
)


@dataclass(frozen=True)
class StageNames:
    """The LiteLLM guardrail names each stage applies, in order, and the chunked ones."""

    input: tuple[str, ...] = ("prompt-guard", "pii-mask")
    output: tuple[str, ...] = ("secret-guard", "pii-mask")
    tool_output: tuple[str, ...] = ("prompt-guard", "secret-guard", "pii-mask")
    chunked: frozenset[str] = frozenset({"prompt-guard"})

    def for_stage(self, stage: Stage) -> tuple[str, ...]:
        names: tuple[str, ...] = getattr(self, stage)
        return names

    @classmethod
    def from_env(cls) -> StageNames:
        default = cls()

        def read(var: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
            raw = os.environ.get(var)
            if raw is None:
                return fallback
            return tuple(n.strip() for n in raw.split(",") if n.strip())

        return cls(
            input=read(INPUT_VAR, default.input),
            output=read(OUTPUT_VAR, default.output),
            tool_output=read(TOOL_OUTPUT_VAR, default.tool_output),
            chunked=frozenset(read(CHUNKED_VAR, tuple(default.chunked))),
        )


def _root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root.removesuffix("/v1")


def _reason(body: bytes) -> str:
    """The port's reason for LiteLLM's blocked message."""
    text = body.decode(errors="replace").lower()
    for needle, reason in _REASONS:
        if needle in text:
            return reason
    return BLOCKED


class LiteLLMGuardrails:
    """`GuardrailPort` over LiteLLM. See the module docstring."""

    name = "litellm"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        names: StageNames | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_S,
    ) -> None:
        self.base_url = _root(base_url)
        self.names = names or StageNames()
        self._api_key = api_key
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            transport=transport,
            timeout=timeout,
            trust_env=False,
        )

    def __repr__(self) -> str:
        key = "set" if self._api_key else "none"
        return f"LiteLLMGuardrails(base_url={self.base_url!r}, api_key={key})"

    @classmethod
    def from_env(cls) -> LiteLLMGuardrails:
        """`LITELLM_BASE_URL` (required), `LITELLM_API_KEY`, and the stage names."""
        base_url = os.environ.get(BASE_URL_VAR)
        if not base_url:
            raise LookupError(f"guardrails: litellm needs {BASE_URL_VAR}")
        return cls(base_url, os.environ.get(API_KEY_VAR) or None, names=StageNames.from_env())

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check(self, text: str, stage: Stage) -> Verdict:
        input_type = _INPUT_TYPE[stage]
        current = text
        for guardrail in self.names.for_stage(stage):
            if not current:
                break
            if guardrail in self.names.chunked:
                for chunk in split_for_classifier(current):
                    blocked = await self._apply(guardrail, chunk, input_type)
                    if isinstance(blocked, Verdict):
                        return blocked
                continue
            answer = await self._apply(guardrail, current, input_type)
            if isinstance(answer, Verdict):
                return answer
            current = answer
        reasons = (PII,) if current != text else ()
        return Verdict(allowed=True, text=current, reasons=reasons)

    async def _apply(self, guardrail: str, text: str, input_type: str) -> str | Verdict:
        """The text LiteLLM gave back, or a blocked `Verdict`. Raises `GuardrailError`."""
        body: dict[str, Any] = {"guardrail_name": guardrail, "text": text, "input_type": input_type}
        try:
            response = await self._client.post(APPLY_PATH, json=body)
        except httpx.HTTPError as exc:
            raise GuardrailError(
                "guardrail_unavailable",
                f"LiteLLM could not be reached ({type(exc).__name__})",
                retryable=True,
            ) from exc
        status = response.status_code
        if status == 200:
            try:
                answer = response.json()["response_text"]
            except (ValueError, KeyError, TypeError) as exc:
                raise GuardrailError(
                    "guardrail_unavailable", "LiteLLM's answer has no response_text"
                ) from exc
            return str(answer) if answer is not None else text
        if status in (400, 422):
            return Verdict(allowed=False, text=text, reasons=(_reason(response.content),))
        message = redact_error_text(
            f"guardrail {guardrail}: LiteLLM answered HTTP {status}", self._api_key
        )
        raise GuardrailError("guardrail_unavailable", message, retryable=status >= 500)
