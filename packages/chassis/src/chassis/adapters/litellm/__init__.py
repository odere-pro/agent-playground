"""The LiteLLM adapter: `ModelPort` over OpenAI-compatible HTTP with plain httpx.

Points at a LiteLLM router (or anything that speaks `/v1/chat/completions`). The API key goes in
the `Authorization` header only; it is never logged or shown in `repr`.
"""

from __future__ import annotations

from chassis.adapters.litellm.client import LiteLLMModel

__all__ = ["LiteLLMModel"]
