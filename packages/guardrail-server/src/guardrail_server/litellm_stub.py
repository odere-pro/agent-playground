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
`StubSettings.guardrail_token`, when set, is sent to the guardrail server as `x-api-key`, as
LiteLLM v1.103.0's `generic_guardrail_api` sends its `api_key` param. A refusal (401) fails the
call closed, like any other non-200 answer.
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


# Hand copy of chassis/redaction.py's card and phone rules (R1-04); the import rules forbid
# sharing them. A card needs the Luhn check; a phone needs a leading `+` or a separator, and an
# ISO date, an IPv4 address, or an x.y.z version is kept.
_ISO_DATE = r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
_IPV4_GROUP = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_NOT_A_PHONE = re.compile(
    rf"{_ISO_DATE}|{_IPV4_GROUP}(?:\.{_IPV4_GROUP}){{3}}|\d{{1,2}}(?:\.\d{{1,2}}){{2}}"
)


def _passes_luhn(digits: str) -> bool:
    """The Luhn checksum every card number carries: double every second digit from the right."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _card_or_kept(match: re.Match[str]) -> str:
    text = match.group(0)
    return "<CREDIT_CARD>" if _passes_luhn(re.sub(r"[ -]", "", text)) else text


def _phone_or_kept(match: re.Match[str]) -> str:
    text = match.group(0)
    if text.isdigit() or _NOT_A_PHONE.fullmatch(text):
        return text
    return "<PHONE_NUMBER>"


def mask_pii(text: str) -> str:
    """The Presidio stand-in: each match as `<ENTITY>`, with the chassis's card and phone rules."""
    for entity, pattern in _PII:
        if entity == "CREDIT_CARD":
            text = pattern.sub(_card_or_kept, text)
        elif entity == "PHONE_NUMBER":
            text = pattern.sub(_phone_or_kept, text)
        else:
            text = pattern.sub(f"<{entity}>", text)
    return text


@dataclass
class StubSettings:
    master_key: str
    guardrail_base_url: str = "http://guardrail-server"
    fail_status: int | None = None
    guardrail_token: str | None = None
    """Sent to the guardrail server as `x-api-key` (LiteLLM's `api_key` param)."""
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
        headers = {"x-api-key": settings.guardrail_token} if settings.guardrail_token else {}
        async with httpx.AsyncClient(
            transport=guardrail_transport, base_url=settings.guardrail_base_url, headers=headers
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
