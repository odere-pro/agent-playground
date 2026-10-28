"""The smolagents pieces of the simplifier: the chat model at the chassis and the `CodeAgent`.

- `ChassisModel` is `OpenAIModel` with three changes. It takes a ready `openai.OpenAI` client, so
  the headers, the timeout, and the no-retry rule are set in one place (`handle`). It always cuts
  the reply at the stop sequences itself, because the chassis model proxy drops `stop` (it keeps
  only `model`, `messages`, `temperature`, `max_tokens`, `tools`, and `stream`), and a model that
  runs on past `</code>` would otherwise get two code blocks run as one. And it turns a reply that
  is not a chat completion into `BadResponse`.
- `SimplifierAgent` is `CodeAgent` with one override: running out of steps raises
  `ToolLoopExceeded` at once, where smolagents would make one more model call for a "final answer".
"""

from __future__ import annotations

from typing import Any

import openai
from smolagents import CodeAgent, OpenAIServerModel  # type: ignore[import-untyped]
from smolagents.models import (  # type: ignore[import-untyped]
    ChatMessage,
    TokenUsage,
    remove_content_after_stop_sequences,
)

from echo_smolagents.mapping import BadResponse, ToolLoopExceeded

PROMPT_VERSION = "simplifier-v1"
INSTRUCTIONS = "Rewrite in plain words. Short sentences. Keep every fact."
"""Fixed, in the system prompt (smolagents `instructions`). The input text is never added here."""
MAX_STEPS = 4
"""suggested: 3 tool rounds plus the final answer. Running out is `tool_loop_exceeded`."""


class ChassisModel(OpenAIServerModel):  # type: ignore[misc]
    def __init__(self, route: str, client: openai.OpenAI) -> None:
        super().__init__(model_id=route, client=client, retry=False)

    def generate(
        self,
        messages: list[Any],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Any] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        completion_kwargs = self._prepare_completion_kwargs(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            model=self.model_id,
            custom_role_conversions=self.custom_role_conversions,
            convert_images_to_image_urls=True,
            **kwargs,
        )
        response = self.client.chat.completions.create(**completion_kwargs)
        try:
            choice = response.choices[0]
            content = choice.message.content
            usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
        except (AttributeError, IndexError, TypeError) as exc:
            raise BadResponse(f"not a chat completion: {type(exc).__name__}") from exc
        if not isinstance(content, str):
            raise BadResponse("the reply has no text content")
        return ChatMessage(
            role=choice.message.role,
            content=remove_content_after_stop_sequences(content, stop_sequences),
            tool_calls=choice.message.tool_calls,
            raw=response,
            token_usage=usage,
        )


class SimplifierAgent(CodeAgent):  # type: ignore[misc]
    def _handle_max_steps_reached(self, task: str) -> Any:
        raise ToolLoopExceeded(f"no final answer in {self.max_steps} steps")
