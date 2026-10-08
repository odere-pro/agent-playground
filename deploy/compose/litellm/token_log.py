"""One log line per model call: route, tokens, cost, and tags. Never the prompt or the reply.

LiteLLM loads it through `litellm_settings.callbacks: token_log.handler` from the config's folder.
PoC-1 runs LiteLLM without a database, so `/spend/logs` is not available; this line in
`docker compose logs litellm` is where token counts per request are visible in the router.
"""

from __future__ import annotations

import sys
from typing import Any

from litellm.integrations.custom_logger import CustomLogger


def _tags(kwargs: dict[str, Any]) -> list[str]:
    metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    tags = metadata.get("tags") or metadata.get("request_tags") or []
    return [str(t) for t in tags]


def _line(kwargs: dict[str, Any], response: Any, status: str) -> str:
    metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    cost = kwargs.get("response_cost") or 0.0
    return (
        f"token_log status={status} route={metadata.get('model_group', kwargs.get('model'))} "
        f"model={kwargs.get('model')} prompt_tokens={prompt} completion_tokens={completion} "
        f"total_tokens={prompt + completion} cost_usd={cost:.6f} tags={','.join(_tags(kwargs))}"
    )


class TokenLog(CustomLogger):
    async def async_log_success_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        print(_line(kwargs, response_obj, "ok"), file=sys.stdout, flush=True)

    async def async_log_failure_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        print(_line(kwargs, response_obj, "error"), file=sys.stdout, flush=True)


handler = TokenLog()
