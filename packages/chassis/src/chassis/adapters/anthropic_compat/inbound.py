"""`AnthropicInbound`: the Anthropic Messages format (`POST /v1/messages` on the public port) onto
the canonical `Request`, and the run's answer back as the official `anthropic` 1.11 types (PoC-3
open note, sections 2 and 3). Pure: no I/O, no clock, no ids. The router is
`chassis.server.interfaces.anthropic`.

**In** (`to_request`). The body is the validated `MessageCreateParams` with any top-level keys the
SDK type drops merged back from the raw JSON (the router does it), or a plain dict. Lazy iterators
are read here (`materialize`); a `ValidationError` raised while reading one is 400 `invalid_body`.

- `model` must equal the agent's name, else 404 `not_found_error` (`model_not_found`).
- The last message must be `role: user`; it is `input.text`, text blocks joined with `"\n"` (the
  model proxy's rule, `openai_compat.messages.text_of`). Assistant prefill is 400.
- `system`, a string or text blocks, then any `role: system` messages in order, joined with
  `"\n"`, is `input.data.system`. Earlier `user` and `assistant` turns are `input.data.history`.
  Each is set only when not empty.
- `max_tokens` is `budget.max_tokens`; 0 or less is 400 (suggested: the minimum of 1, the same
  on every interface). `stream` is `request.stream`. More `messages` than `messages_max`
  (`spec.limits`) is 400 `limit_exceeded`; the budget ceiling is the router's
  (`chassis.server.interfaces.limits`).
- Refused, 400 `invalid_request_error`: non-empty `tools`, a `tool_choice` other than `auto` or
  `none`, `mcp_servers`, `container`, `output_config.format`, and any block that is not text in any
  message or in `system` (`image`, `document`, `tool_use`, `tool_result`, `thinking`, and the rest).
- Ignored and reported by `ignored(body)` (the router passes it to `serve`, which counts
  `chassis.inbound_ignored`): `IGNORED_PARAMS`, `tool_choice` `auto`/`none`, and
  `output_config.effort`. `metadata` and `user_profile_id` are dropped and never counted or logged
  (personal data). Headers (`anthropic-version`, `anthropic-beta`, `x-api-key`) are never read.

**Back.** The answer text is `chassis.core.inbound.answer_text`, and a stream's last text delta is
`rest_of_answer`. `complete` is a `Message` with `stop_reason: end_turn`; `stream` is
`message_start`, `content_block_start` at the first text, `content_block_delta` per delta,
`content_block_stop` only when a block was opened, `message_delta` with the run's usage, and
`message_stop`. An `error` event mid-stream is `event: error` and then the end. Every answer
carries `request-id` (what the SDK reads), `x-request-id`, and `x-trace-id`. `error` is the HTTP
error body `{"type": "error", "error": {"type", "message": "<code>: <message>"}, "request_id"}` with
`status_for(code, retryable)` and `x-should-retry`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from chassis.adapters.anthropic_compat.types import (
    ErrorResponse,
    Message,
    MessageDelta,
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    Usage,
)
from chassis.adapters.openai_compat.messages import UnsupportedMessage, is_empty, text_of
from chassis.core.envelope import Budget, Request, Response, TaskInput
from chassis.core.events import Delta, End, Error, Event
from chassis.core.inbound import (
    Ids,
    Interface,
    Refused,
    Reply,
    ReplyMeta,
    Served,
    StreamReply,
    answer_text,
    public_message,
    rest_of_answer,
    status_for,
)

__all__ = [
    "DROPPED_PARAMS",
    "IGNORED_PARAMS",
    "AnthropicInbound",
    "InvalidBody",
    "answer_text",
    "error_type_for",
    "materialize",
]

IGNORED_PARAMS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "service_tier",
        "thinking",
        "cache_control",
        "inference_geo",
        "diagnostics",
        "workspace_id",
    }
)
"""Top-level params that only tune the answer: accepted, ignored, counted. `temperature`,
`top_p`, and `top_k` are not in the 1.11 `MessageCreateParams`; they arrive as raw keys."""

DROPPED_PARAMS = frozenset({"metadata", "user_profile_id"})
"""Dropped and never counted or logged: they may identify a person. Caller identity is PoC-8's."""

ROLES = frozenset({"user", "assistant", "system"})
QUIET_TOOL_CHOICES = frozenset({"auto", "none"})
_PARAMS = frozenset(
    {"tools", "tool_choice", "mcp_servers", "container", "output_config", "output_config.format"}
)
"""Refused top-level params: `unsupported_parameter`. A refused message or block is
`unsupported_message`."""

ErrorKind = Literal[
    "invalid_request_error", "not_found_error", "overloaded_error", "timeout_error", "api_error"
]


class InvalidBody(ValueError):
    """A lazy iterator raised `ValidationError` while it was read. `str()` names where and why,
    never the input.
    """

    def __init__(self, path: str, exc: ValidationError) -> None:
        parts = []
        for error in exc.errors():
            loc = path or "body"
            for p in error.get("loc", ()):
                loc += f"[{p}]" if isinstance(p, int) else f".{p}"
            parts.append(f"{loc}: {error.get('msg', 'invalid')}")
        super().__init__("; ".join(parts) or f"{path or 'body'}: invalid")


def materialize(value: Any, path: str = "") -> Any:
    """`value` with every mapping as a dict and every other non-string iterable as a list, read
    once, all the way down. FastAPI hands over `messages`, `system`, and block `content` as lazy
    `ValidatorIterator`s that validate each item when it is read; their `ValidationError` is
    raised here as `InvalidBody`.
    """
    try:
        if isinstance(value, Mapping):
            return {
                key: materialize(item, f"{path}.{key}" if path else str(key))
                for key, item in value.items()
            }
        if isinstance(value, str | bytes | bytearray) or not isinstance(value, Iterable):
            return value
        return [materialize(item, f"{path}[{i}]") for i, item in enumerate(value)]
    except ValidationError as exc:
        raise InvalidBody(path, exc) from None


def error_type_for(status: int) -> ErrorKind:
    """Anthropic's `error.type` for a status from `status_for` (section 3)."""
    if status in (400, 422):
        return "invalid_request_error"
    if status == 404:
        return "not_found_error"
    if status == 503:
        return "overloaded_error"
    if status == 504:
        return "timeout_error"
    return "api_error"


def _ids_headers(ids: Ids) -> dict[str, str]:
    """The id headers on every answer. The Anthropic SDK reads `request-id` (`_request_id`, an
    error's `request_id`); `x-request-id` is the one every chassis format sends.
    """
    return {
        "request-id": ids.request_id,
        "x-request-id": ids.request_id,
        "x-trace-id": ids.trace_id,
    }


def _frame(name: str, payload: BaseModel, *, exclude: set[str] | None = None) -> str:
    data = payload.model_dump(mode="json", exclude=exclude)
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _usage(response: Response) -> tuple[int, int]:
    metrics = response.metrics
    return int(metrics.get("input_tokens", 0)), int(metrics.get("output_tokens", 0))


def _message(
    meta: ReplyMeta,
    text: str,
    *,
    stop_reason: Literal["end_turn"] | None,
    usage: tuple[int, int],
) -> Message:
    return Message(
        id=f"msg_{meta.ids.request_id}",
        type="message",
        role="assistant",
        model=meta.served.agent,
        content=[TextBlock(type="text", text=text)] if text else [],
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=usage[0], output_tokens=usage[1]),
    )


ENGINE_ERROR = "engine_error"
NO_END_MESSAGE = "the run ended without an end event"


def _error_response(code: str, message: str, status: int, request_id: str | None) -> ErrorResponse:
    return ErrorResponse.model_validate(
        {
            "type": "error",
            "error": {"type": error_type_for(status), "message": f"{code}: {message}"},
            "request_id": request_id,
        }
    )


class AnthropicInbound:
    """`InboundAdapter` for the Anthropic Messages format (module docstring)."""

    interface: Interface = "anthropic"

    def __init__(self, *, messages_max: int | None = None) -> None:
        self.messages_max = messages_max
        """At most this many `messages` (`spec.limits.messages_max`), else 400 `limit_exceeded`;
        None: no cap."""

    # --- in ------------------------------------------------------------------------------------

    def ignored(self, body: Mapping[str, Any]) -> list[str]:
        """The accepted-and-ignored params `body` sets, sorted; the router passes them to `serve`.
        Reads top-level keys only, so it never reads a lazy iterator.
        """
        names = {key for key in IGNORED_PARAMS if not is_empty(body.get(key))}
        choice = body.get("tool_choice")
        if isinstance(choice, Mapping) and choice.get("type") in QUIET_TOOL_CHOICES:
            names.add("tool_choice")
        config = body.get("output_config")
        if isinstance(config, Mapping) and not is_empty(config.get("effort")):
            names.add("output_config.effort")
        return sorted(names)

    def to_request(
        self, body: Mapping[str, Any], headers: Mapping[str, str], *, ids: Ids, served: Served
    ) -> Request:
        try:
            raw = materialize(body)
        except InvalidBody as exc:
            raise self._refused("invalid_body", str(exc), ids, served) from None
        if not isinstance(raw, dict):
            raise self._refused("invalid_body", "the body must be an object", ids, served)
        model = raw.get("model")
        if model != served.agent:
            raise self._refused(
                "model_not_found",
                f"model {model!r} is not served here; this agent is {served.agent!r}",
                ids,
                served,
            )
        messages = raw.get("messages")
        cap = self.messages_max
        if cap is not None and isinstance(messages, list) and len(messages) > cap:
            why = f"messages: {len(messages)} is more than the limit of {cap}"
            raise self._refused("limit_exceeded", why, ids, served)
        try:
            self._refuse_params(raw)
            system = self._system(raw.get("system"))
            text, system_turns, history = self._messages(raw.get("messages"))
        except UnsupportedMessage as exc:
            code = "unsupported_parameter" if exc.param in _PARAMS else "unsupported_message"
            raise self._refused(code, str(exc), ids, served) from None
        max_tokens = raw.get("max_tokens")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise self._refused(
                "invalid_body", "max_tokens: a positive integer is required", ids, served
            )
        stream = raw.get("stream")
        if stream is not None and not isinstance(stream, bool):
            raise self._refused("invalid_body", "stream: a boolean is required", ids, served)
        data: dict[str, Any] = {}
        joined = "\n".join(part for part in [system, *system_turns] if part)
        if joined:
            data["system"] = joined
        if history:
            data["history"] = history
        return Request(
            request_id=ids.request_id,
            trace_id=ids.trace_id,
            idempotency_key=ids.idempotency_key,
            agent=served.agent,
            agent_version=served.agent_version,
            input=TaskInput(text=text, data=data),
            context_ref=None,
            stream=bool(stream),
            budget=Budget(max_tokens=max_tokens),
        )

    def _refused(self, code: str, message: str, ids: Ids, served: Served) -> Refused:
        reply = self.error(code, message, False, ReplyMeta(ids, served, 0))
        return Refused(reply.status, reply.headers, reply.body)

    @staticmethod
    def _refuse_params(raw: dict[str, Any]) -> None:
        if not is_empty(raw.get("tools")):
            raise UnsupportedMessage(
                "tools", "client tools are not supported; the agent's tools are its own"
            )
        choice = raw.get("tool_choice")
        if not is_empty(choice) and not (
            isinstance(choice, dict) and choice.get("type") in QUIET_TOOL_CHOICES
        ):
            raise UnsupportedMessage("tool_choice", "only tool_choice auto or none is supported")
        if not is_empty(raw.get("mcp_servers")):
            raise UnsupportedMessage("mcp_servers", "client MCP servers are not supported")
        if not is_empty(raw.get("container")):
            raise UnsupportedMessage("container", "containers are not supported")
        config = raw.get("output_config")
        if not is_empty(config):
            if not isinstance(config, dict):
                raise UnsupportedMessage("output_config", "output_config must be an object")
            if not is_empty(config.get("format")):
                raise UnsupportedMessage(
                    "output_config.format", "structured output formats are not supported"
                )

    @staticmethod
    def _system(value: Any) -> str:
        return text_of(value, "system") or ""

    @staticmethod
    def _messages(value: Any) -> tuple[str, list[str], list[dict[str, str]]]:
        """The last user text, the `system` turns' texts, and the history."""
        if not isinstance(value, list) or not value:
            raise UnsupportedMessage("messages", "at least one message is required")
        turns: list[tuple[str, str]] = []
        for i, message in enumerate(value):
            at = f"messages[{i}]"
            if not isinstance(message, dict):
                raise UnsupportedMessage(at, "a message must be an object")
            role = message.get("role")
            if role not in ROLES:
                raise UnsupportedMessage(f"{at}.role", f"role {role!r} is not supported")
            text = text_of(message.get("content"), f"{at}.content")
            if text is None:
                raise UnsupportedMessage(f"{at}.content", "content is required")
            turns.append((role, text))
        last_role, last_text = turns[-1]
        if last_role != "user":
            raise UnsupportedMessage(
                f"messages[{len(turns) - 1}].role",
                f"the last message must be role user, not {last_role!r}; "
                "assistant prefill is not supported",
            )
        system_turns = [text for role, text in turns[:-1] if role == "system"]
        history = [{"role": role, "text": text} for role, text in turns[:-1] if role != "system"]
        return last_text, system_turns, history

    # --- back ----------------------------------------------------------------------------------

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        """A finished run as a `Message`. A run that ended without an `end` event (status `error`
        with no `error` event) is an HTTP error, as in the OpenAI format.
        """
        if response.status == "error":
            found = response.output.get("error")
            if isinstance(found, Mapping):
                code = str(found.get("code") or ENGINE_ERROR)
                return self.error(code, public_message(code), False, meta)
            return self.error(ENGINE_ERROR, NO_END_MESSAGE, False, meta)
        message = _message(
            meta, answer_text(response.output), stop_reason="end_turn", usage=_usage(response)
        )
        headers = {**_ids_headers(meta.ids), "x-chassis-status": response.status}
        return Reply(200, headers, message.model_dump(mode="json"))

    def stream(self, events: AsyncIterator[Event], meta: ReplyMeta) -> StreamReply:
        async def frames() -> AsyncIterator[str]:
            start = _message(meta, "", stop_reason=None, usage=(0, 0))
            yield _frame("message_start", RawMessageStartEvent(type="message_start", message=start))
            seen: list[Event] = []
            sent: list[str] = []
            opened = False
            ended = False

            def text_frames(text: str) -> list[str]:
                nonlocal opened
                out = []
                if not opened:
                    opened = True
                    block = TextBlock(type="text", text="")
                    out.append(
                        _frame(
                            "content_block_start",
                            RawContentBlockStartEvent(
                                type="content_block_start", index=0, content_block=block
                            ),
                        )
                    )
                sent.append(text)
                delta = TextDelta(type="text_delta", text=text)
                out.append(
                    _frame(
                        "content_block_delta",
                        RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta),
                    )
                )
                return out

            async for event in events:
                if ended:
                    continue  # the run is over; nothing after `end` is part of it
                seen.append(event)
                if isinstance(event, Delta) and event.text:
                    for frame in text_frames(event.text):
                        yield frame
                elif isinstance(event, Error):
                    status = status_for(event.code, event.retryable)
                    # The fixed text for the code; the run's message stays in the log and span.
                    message = public_message(event.code)
                    error = _error_response(event.code, message, status, None)
                    yield _frame("error", error, exclude={"request_id"})
                    return
                elif isinstance(event, End):
                    ended = True
            response = await meta.collect(seen)
            if response.status == "error":  # no `end` and no `error`: the run did not finish
                status = status_for(ENGINE_ERROR, False)
                error = _error_response(ENGINE_ERROR, NO_END_MESSAGE, status, None)
                yield _frame("error", error, exclude={"request_id"})
                return
            rest = rest_of_answer(response.output, "".join(sent))
            if rest:
                for frame in text_frames(rest):
                    yield frame
            if opened:
                stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
                yield _frame("content_block_stop", stop)
            input_tokens, output_tokens = _usage(response)
            delta = RawMessageDeltaEvent(
                type="message_delta",
                delta=MessageDelta(stop_reason="end_turn", stop_sequence=None),
                usage=MessageDeltaUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            )
            yield _frame("message_delta", delta)
            yield _frame("message_stop", RawMessageStopEvent(type="message_stop"))

        return StreamReply(frames(), _ids_headers(meta.ids))

    def error(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> Reply:
        status = status_for(code, retryable)
        body = _error_response(code, message, status, meta.ids.request_id)
        headers = {**_ids_headers(meta.ids), "x-should-retry": "true" if retryable else "false"}
        return Reply(status, headers, body.model_dump(mode="json"))
