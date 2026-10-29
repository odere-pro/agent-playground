"""A stub of LiteLLM's guardrail API, for offline tests (PoC-7). Not LiteLLM: it has the shape of
the one route the chassis calls, and the three guardrails the PoC-7 config names.

`POST /guardrails/apply_guardrail` (LiteLLM 1.106.5's shape) takes `{guardrail_name, text,
language?, entities?, input_type?: "request" | "response", messages?, metadata?}` with the
service's virtual key as `Authorization: Bearer`, and answers `{response_text}`. A guardrail
that blocks makes the call fail with HTTP 400 and LiteLLM's error body (`{"error": {"message",
"type", "param", "code"}}`); the message holds the guardrail's blocked reason.

The guardrails (`litellm/config.yaml` in this package is the matching LiteLLM config):

- `pii-mask`: LiteLLM's built-in `presidio` hook in the real stack. Here a regex STAND-IN that
  masks emails, phone numbers, and card numbers as `<ENTITY>`, Presidio's default form. Names
  (a named-entity model) are not masked by the stand-in.
- `prompt-guard`: `generic_guardrail_api` with `checks: [injection]`, to the guardrail server.
- `secret-guard`: `generic_guardrail_api` with `checks: [secrets]`, to the guardrail server.

The stub calls the guardrail server over `guardrail_transport` (an ASGI transport offline).
`StubSettings.fail_status` makes every guardrail-server call fail with that status (an outage).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from guardrail_server.app import GENERIC_PATH

__all__ = ["APPLY_PATH", "GUARDRAILS", "StubSettings", "create_stub", "mask_pii"]

APPLY_PATH = "/guardrails/apply_guardrail"

GUARDRAILS: dict[str, dict[str, Any]] = {
    "pii-mask": {"kind": "presidio"},
    "prompt-guard": {"kind": "generic", "checks": ["injection"]},
    "secret-guard": {"kind": "generic", "checks": ["secrets"]},
}

_PII: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL_ADDRESS", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CREDIT_CARD", re.compile(r"\b\d{4}(?:[ -]?\d{4}){2}[ -]?\d{1,7}\b")),
    (
        "PHONE_NUMBER",
        re.compile(r"(?<![\w-])\+?\d{1,3}?[ .-]?\(?\d{2,4}\)?(?:[ .-]?\d{2,4}){2,3}(?![\w-])"),
    ),
)


def mask_pii(text: str) -> str:
    """The Presidio stand-in: each match as `<ENTITY>`."""
    for entity, pattern in _PII:
        text = pattern.sub(f"<{entity}>", text)
    return text


@dataclass
class StubSettings:
    master_key: str
    guardrail_base_url: str = "http://guardrail-server"
    fail_status: int | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    """Each apply call: the guardrail name, the input type, and the text."""


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    guardrail_name: str
    text: str
    language: str | None = None
    entities: list[str] | None = None
    input_type: Literal["request", "response"] = "request"
    messages: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None


def _error(status: int, message: str) -> JSONResponse:
    body = {"error": {"message": message, "type": "None", "param": "None", "code": str(status)}}
    return JSONResponse(body, status_code=status)


def create_stub(
    settings: StubSettings, *, guardrail_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    app = FastAPI(title="litellm-guardrail-stub")

    @app.post(APPLY_PATH)
    async def apply(body: ApplyRequest, request: Request) -> JSONResponse:
        if request.headers.get("authorization") != f"Bearer {settings.master_key}":
            return _error(401, "Authentication Error, invalid key")
        settings.calls.append(
            {"guardrail": body.guardrail_name, "input_type": body.input_type, "text": body.text}
        )
        guardrail = GUARDRAILS.get(body.guardrail_name)
        if guardrail is None:
            return _error(400, f"guardrail {body.guardrail_name} not found")
        if guardrail["kind"] == "presidio":
            return JSONResponse({"response_text": mask_pii(body.text)})
        if settings.fail_status is not None:
            return _error(settings.fail_status, "guardrail server unavailable")
        payload = {
            "input_type": body.input_type,
            "texts": [body.text],
            "request_data": {"metadata": body.metadata or {}},
            "additional_provider_specific_params": {"checks": guardrail["checks"]},
        }
        async with httpx.AsyncClient(
            transport=guardrail_transport, base_url=settings.guardrail_base_url
        ) as client:
            try:
                answer = await client.post(GENERIC_PATH, json=payload)
            except httpx.HTTPError:
                return _error(500, "guardrail server unreachable")
        if answer.status_code != 200:
            return _error(500, f"guardrail server answered {answer.status_code}")
        data = answer.json()
        if data.get("action") == "BLOCKED":
            reason = data.get("blocked_reason") or "blocked"
            return _error(400, f"Guardrail {body.guardrail_name} blocked this request: {reason}")
        texts = data.get("texts")
        text = texts[0] if data.get("action") == "GUARDRAIL_INTERVENED" and texts else body.text
        return JSONResponse({"response_text": text})

    return app
