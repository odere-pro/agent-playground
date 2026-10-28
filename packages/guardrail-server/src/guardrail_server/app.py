"""The guardrail server: LiteLLM's generic guardrail API over an injection classifier and the
secret detector (PoC-7, 022 H-6).

LiteLLM's `generic_guardrail_api` hook POSTs to `{api_base}/beta/litellm_basic_guardrail_api`:

    {"input_type": "request" | "response", "texts": [str], "request_data": {...},
     "litellm_call_id"?, "litellm_trace_id"?, "structured_messages"?, "tool_calls"?, "model"?,
     "additional_provider_specific_params"?: {"checks": ["injection", "secrets"]}}

and reads `{"action": "BLOCKED" | "NONE" | "GUARDRAIL_INTERVENED", "blocked_reason"?, "texts"?}`.
This server never rewrites a text, so it answers `NONE` or `BLOCKED`.

`checks` (suggested; from the guardrail's params in LiteLLM's config) picks what runs: `injection`
(the `Classifier`, over its windows; blocked at or over `threshold`) and `secrets` (regex, any
match). Both by default. `blocked_reason` starts with the reason (`prompt_injection`, `secret`) and
never holds the text.

`POST /v1/check` (`{"text", "input_type"?, "checks"?}` -> `{"allowed", "reasons"}`) is a
convenience for a caller that is not LiteLLM (a smoke test, the red-team runner's report).
`GET /health` names the classifier, so a run can say which one answered.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from guardrail_server.classifier import Classifier
from guardrail_server.secret_scan import find_secret

__all__ = ["GENERIC_PATH", "THRESHOLD", "create_app"]

GENERIC_PATH = "/beta/litellm_basic_guardrail_api"
THRESHOLD = 0.5
"""suggested: the injection probability that blocks."""

Check = Literal["injection", "secrets"]
DEFAULT_CHECKS: tuple[Check, ...] = ("injection", "secrets")


class ProviderParams(BaseModel):
    model_config = ConfigDict(extra="allow")
    checks: list[Check] = Field(default_factory=lambda: list(DEFAULT_CHECKS))


class GenericRequest(BaseModel):
    """LiteLLM's generic guardrail request. Fields this server does not read are accepted."""

    model_config = ConfigDict(extra="allow")
    input_type: Literal["request", "response"]
    texts: list[str] = Field(default_factory=list)
    request_data: dict[str, Any] = Field(default_factory=dict)
    litellm_call_id: str | None = None
    litellm_trace_id: str | None = None
    model: str | None = None
    additional_provider_specific_params: ProviderParams | None = None


class GenericResponse(BaseModel):
    action: Literal["BLOCKED", "NONE", "GUARDRAIL_INTERVENED"]
    blocked_reason: str | None = None
    texts: list[str] | None = None


class CheckRequest(BaseModel):
    text: str
    input_type: Literal["request", "response"] = "request"
    checks: list[Check] = Field(default_factory=lambda: list(DEFAULT_CHECKS))


class CheckResponse(BaseModel):
    allowed: bool
    reasons: list[str]


def create_app(classifier: Classifier, *, threshold: float = THRESHOLD) -> FastAPI:
    """The server over `classifier`. `threshold` is the injection probability that blocks."""
    app = FastAPI(title="guardrail-server", version="0.1.0")

    async def reasons_for(texts: list[str], checks: list[Check]) -> list[str]:
        reasons: list[str] = []
        if "secrets" in checks:
            for text in texts:
                kind = find_secret(text)
                if kind is not None:
                    reasons.append(f"secret ({kind})")
                    break
        if "injection" in checks:
            for text in texts:
                if not text.strip():
                    continue
                chunks = await run_in_threadpool(classifier.chunks, text)
                scores = await run_in_threadpool(classifier.score, chunks)
                top = max(scores, default=0.0)
                if top >= threshold:
                    reasons.append(f"prompt_injection (score {top:.2f}, {classifier.name})")
                    break
        return reasons

    @app.post(GENERIC_PATH, response_model=GenericResponse, response_model_exclude_none=True)
    async def generic(body: GenericRequest) -> GenericResponse:
        params = body.additional_provider_specific_params or ProviderParams()
        reasons = await reasons_for(body.texts, params.checks)
        if reasons:
            return GenericResponse(action="BLOCKED", blocked_reason="; ".join(reasons))
        return GenericResponse(action="NONE")

    @app.post("/v1/check", response_model=CheckResponse)
    async def check(body: CheckRequest) -> CheckResponse:
        reasons = await reasons_for([body.text], body.checks)
        return CheckResponse(allowed=not reasons, reasons=[r.split(" ", 1)[0] for r in reasons])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "classifier": classifier.name}

    return app
