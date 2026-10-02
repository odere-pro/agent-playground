"""`OpenAIInbound`: the OpenAI interface's pure mapping (PoC-3 open note, sections 2 and 3). The
router is `chassis.server.interfaces.openai` (`POST /v1/chat/completions` on the public port, where
`model` is the agent). Not the model proxy (the same path on the proxy port, where a workload calls
a model; `chassis.server.model_proxy`). Both share `messages.py` for content parts.

**In** (`to_request`). The body is the SDK's `CompletionCreateParams` as FastAPI validated it:
`messages` and every list inside it are lazy iterators, read here once; a `ValidationError` while
reading is 400 `invalid_body`, never a 500. Then, in order:

- `model` must equal the served agent, else 404 `model_not_found`.
- The refused parameters are 400 `unsupported_parameter` with `param` set (`REFUSED_PARAMS`): what
  the client would read and not find.
- The last message is `input.text` and must be `role: user`. `system` and `developer` messages, in
  order, joined with `"\n"`, are `input.data.system`; earlier `user` and `assistant` text turns
  are `input.data.history` (`[{"role", "text"}]`). Both keys are set only when not empty. Any
  other part, role, or key with a value is 400 `unsupported_message` naming it.
- `max_completion_tokens`, else `max_tokens`, is `budget.max_tokens` (default 2000). Both set and
  different, or not positive, is 400 `invalid_body`.
- More `messages` than `messages_max` (`spec.limits`) is 400 `limit_exceeded`. The budget ceiling
  is checked after the mapping, by the router (`chassis.server.interfaces.limits`).

`ignored(body, headers)` names what was accepted and ignored, for `chassis.inbound_ignored`;
`metadata`, `user`, and `safety_identifier` are dropped and never counted or logged.
`options(body)` reads `stream_options.include_usage` for the reply.

**Back.** The official SDK models, dumped with only the fields set: `ChatCompletion` (`id
chatcmpl-<request_id>`, `finish_reason: stop`, the run's whole usage), headers `x-request-id`,
`x-trace-id`, and `x-chassis-status`. The answer text is `chassis.core.inbound.answer_text`, and a
stream's last text chunk is `rest_of_answer`. A stream is a role chunk, one chunk per `delta`, a
finish chunk with `usage` (or, with `include_usage`, a last `choices: []` chunk with it), then
`data: [DONE]`. An `error` after text is one `data: {"error": ...}` frame, then `[DONE]`. Errors
are `{"error": {"message", "type", "param", "code", "retryable"}}` with `status_for` and
`x-should-retry`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from chassis.adapters.openai_compat.messages import (
    UnsupportedMessage,
    is_empty,
    refuse_extra,
    text_of,
)
from chassis.adapters.openai_compat.types import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessage,
    Choice,
    ChoiceDelta,
    ChunkChoice,
    CompletionUsage,
    OpenAIError,
    OpenAIErrorResponse,
)
from chassis.core.envelope import Budget, Request, Response, TaskInput
from chassis.core.events import Delta, Error, Event
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
    "IGNORED_HEADERS",
    "IGNORED_PARAMS",
    "INCLUDE_USAGE",
    "REFUSED_PARAMS",
    "OpenAIInbound",
    "answer_text",
]

INCLUDE_USAGE = "include_usage"
"""The `ReplyMeta.options` key for `stream_options.include_usage`."""

IGNORED_PARAMS = (
    "temperature",
    "top_p",
    "seed",
    "stop",
    "frequency_penalty",
    "presence_penalty",
    "logit_bias",
    "store",
    "service_tier",
    "reasoning_effort",
    "prediction",
    "verbosity",
    "parallel_tool_calls",
    "prompt_cache_key",
    "prompt_cache_options",
    "prompt_cache_retention",
    "stream_options",
)
"""Accepted and ignored: they only tune the answer. `stream_options` counts only when it sets
more than `include_usage`, which the reply honors."""

IGNORED_HEADERS = ("authorization",)
"""Accepted and ignored; never logged or forwarded. Auth is PoC-8."""

DROPPED_PARAMS = ("metadata", "user", "safety_identifier")
"""Dropped, never counted or logged: they may be personal data."""

_TEXT_ROLES = frozenset({"user", "assistant"})
_SYSTEM_ROLES = frozenset({"system", "developer"})
_MESSAGE_KEYS = frozenset({"role", "content", "name"})
_REFUSED_BY = "the OpenAI interface"
"""Who refuses an unknown message key, in the error text (not the model proxy's "model port")."""


def _not_one(value: Any) -> bool:
    return value is not None and value != 1


def _not_none_or_auto(value: Any) -> bool:
    return value not in (None, "none", "auto")


def _not_text_format(value: Any) -> bool:
    return value is not None and not (isinstance(value, Mapping) and value.get("type") == "text")


REFUSED_PARAMS: tuple[tuple[str, Callable[[Any], bool], str], ...] = (
    ("n", _not_one, "n other than 1 is not supported: one answer per run"),
    ("logprobs", bool, "logprobs are not supported: the chassis never sees them"),
    ("top_logprobs", bool, "top_logprobs are not supported: the chassis never sees them"),
    ("tools", lambda v: not is_empty(v), "client tools are not supported; the agent has its own"),
    ("functions", lambda v: not is_empty(v), "functions are not supported; the agent has its own"),
    ("tool_choice", _not_none_or_auto, "tool_choice must be none or auto: no client tools"),
    ("function_call", _not_none_or_auto, "function_call must be none or auto: no functions"),
    ("response_format", _not_text_format, "only the text response_format is supported"),
    ("audio", lambda v: v is not None, "audio output is not supported"),
    ("modalities", lambda v: "audio" in (v or ()), "only the text modality is supported"),
    ("web_search_options", lambda v: v is not None, "web search is not supported"),
    ("moderation", lambda v: v is not None, "moderation results are not supported"),
)
"""`(param, refused(value), why)`: what the client would read and not find (section 2)."""


def _plain(value: Any) -> Any:
    """`value` with every lazy iterator read into a list, once. Raises `ValidationError`."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, str | bytes):
        return value
    if isinstance(value, Iterable):
        return [_plain(item) for item in value]
    return value


def _validation_text(exc: ValidationError, at: str) -> str:
    """At most three distinct `<loc>: <msg>` lines; the input is never echoed."""
    lines: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in (at, *error.get("loc", ())))
        line = f"{loc}: {error.get('msg', 'invalid')}"
        if line not in lines:
            lines.append(line)
    more = "; ..." if len(lines) > 3 else ""
    return "; ".join(lines[:3]) + more


class OpenAIInbound:
    """`InboundAdapter[CompletionCreateParams]` (module docstring). Pure: no I/O, clock, or ids."""

    interface: Interface = "openai"

    def __init__(self, *, messages_max: int | None = None) -> None:
        self.messages_max = messages_max
        """At most this many `messages` (`spec.limits.messages_max`), else 400 `limit_exceeded`;
        None: no cap."""

    # --- in ------------------------------------------------------------------------------------

    def to_request(
        self, body: Mapping[str, Any], headers: Mapping[str, str], *, ids: Ids, served: Served
    ) -> Request:
        plain = self._read(body, ids)
        model = plain.get("model")
        if model != served.agent:
            raise self._refused(
                ids,
                "model_not_found",
                f"the model {model!r} does not exist here; this agent is {served.agent!r}",
                "model",
            )
        for param, refused, why in REFUSED_PARAMS:
            if refused(plain.get(param)):
                raise self._refused(ids, "unsupported_parameter", why, param)
        messages = plain.get("messages")
        cap = self.messages_max
        if cap is not None and isinstance(messages, list) and len(messages) > cap:
            why = f"messages: {len(messages)} is more than the limit of {cap}"
            raise self._refused(ids, "limit_exceeded", why, "messages")
        try:
            task = self._task_input(messages)
        except UnsupportedMessage as exc:
            raise self._refused(ids, "unsupported_message", exc.message, exc.param) from exc
        return Request(
            request_id=ids.request_id,
            trace_id=ids.trace_id,
            idempotency_key=ids.idempotency_key,
            agent=served.agent,
            agent_version=served.agent_version,
            input=task,
            stream=plain.get("stream") is True,
            budget=self._budget(plain, ids),
        )

    def ignored(self, body: Mapping[str, Any], headers: Mapping[str, str]) -> list[str]:
        """The accepted-and-ignored params the body sets, in body order, then the headers."""
        names: list[str] = []
        for key, value in body.items():
            if key not in IGNORED_PARAMS or value is None:
                continue
            if key == "stream_options" and not (
                isinstance(value, Mapping) and set(value) - {INCLUDE_USAGE}
            ):
                continue
            names.append(key)
        names.extend(name for name in IGNORED_HEADERS if headers.get(name))
        return names

    def options(self, body: Mapping[str, Any]) -> dict[str, object]:
        """`include_usage`: a streamed body with `stream_options.include_usage` true."""
        stream_options = body.get("stream_options")
        include = (
            body.get("stream") is True
            and isinstance(stream_options, Mapping)
            and stream_options.get(INCLUDE_USAGE) is True
        )
        return {INCLUDE_USAGE: include}

    def _read(self, body: Mapping[str, Any], ids: Ids) -> dict[str, Any]:
        plain: dict[str, Any] = {}
        for key, value in body.items():
            try:
                plain[key] = _plain(value)
            except ValidationError as exc:
                raise self._refused(ids, "invalid_body", _validation_text(exc, key), key) from exc
        return plain

    @staticmethod
    def _task_input(messages: Any) -> TaskInput:
        if not isinstance(messages, list) or not messages:
            raise UnsupportedMessage("messages", "messages must hold at least one message")
        system: list[str] = []
        turns: list[dict[str, str]] = []
        for index, raw in enumerate(messages):
            at = f"messages[{index}]"
            if not isinstance(raw, dict):
                raise UnsupportedMessage(at, "a message must be an object")
            role = raw.get("role")
            if role not in _SYSTEM_ROLES | _TEXT_ROLES:
                raise UnsupportedMessage(
                    f"{at}.role",
                    f"role {role!r} is not supported; use system, developer, user, or assistant",
                )
            refuse_extra(raw, _MESSAGE_KEYS, at, by=_REFUSED_BY)
            text = text_of(raw.get("content"), f"{at}.content")
            if text is None:
                raise UnsupportedMessage(f"{at}.content", "content is required")
            if role in _SYSTEM_ROLES:
                system.append(text)
            else:
                turns.append({"role": role, "text": text})
        last = len(messages) - 1
        if messages[last].get("role") != "user":
            raise UnsupportedMessage(
                f"messages[{last}].role", "the last message must be a user message"
            )
        data: dict[str, Any] = {}
        if system:
            data["system"] = "\n".join(system)
        if len(turns) > 1:
            data["history"] = turns[:-1]
        return TaskInput(text=turns[-1]["text"], data=data)

    def _budget(self, plain: Mapping[str, Any], ids: Ids) -> Budget:
        completion, legacy = plain.get("max_completion_tokens"), plain.get("max_tokens")
        if completion is not None and legacy is not None and completion != legacy:
            raise self._refused(
                ids,
                "invalid_body",
                "max_completion_tokens and max_tokens differ; send one",
                "max_tokens",
            )
        limit, param = (
            (completion, "max_completion_tokens")
            if completion is not None
            else (legacy, "max_tokens")
        )
        if limit is None:
            return Budget()
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise self._refused(ids, "invalid_body", f"{param} must be a positive integer", param)
        return Budget(max_tokens=limit)

    def _refused(self, ids: Ids, code: str, message: str, param: str | None) -> Refused:
        reply = self._error(code, message, False, ids, param)
        return Refused(reply.status, reply.headers, reply.body)

    # --- back ----------------------------------------------------------------------------------

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        """A finished run as a `ChatCompletion`. A run that ended without an `end` event (status
        `error` with no `error` event) is an HTTP error.
        """
        if response.status == "error":
            found = response.output.get("error")
            error = found if isinstance(found, Mapping) else {}
            code = str(error.get("code") or "engine_error")
            message = public_message(code) if error else "the run ended without an end event"
            return self.error(code, message, False, meta)
        completion = ChatCompletion(
            id=_completion_id(meta),
            object="chat.completion",
            created=meta.created,
            model=meta.served.agent,
            choices=[
                Choice(
                    index=0,
                    finish_reason="stop",
                    logprobs=None,
                    message=ChatCompletionMessage(
                        role="assistant", content=answer_text(response.output), refusal=None
                    ),
                )
            ],
            usage=_usage(response.metrics),
        )
        headers = {**_id_headers(meta.ids), "x-chassis-status": response.status}
        return Reply(200, headers, completion.model_dump(mode="json", exclude_unset=True))

    def stream(self, events: AsyncIterator[Event], meta: ReplyMeta) -> StreamReply:
        include_usage = meta.options.get(INCLUDE_USAGE) is True

        def chunk(
            delta: ChoiceDelta, *, finish: bool = False, usage: CompletionUsage | None = None
        ) -> str:
            choice = ChunkChoice(
                index=0, delta=delta, finish_reason="stop" if finish else None, logprobs=None
            )
            return _chunk_frame(meta, [choice], usage, include_usage)

        async def frames() -> AsyncIterator[str]:
            seen: list[Event] = []
            sent: list[str] = []
            yield chunk(ChoiceDelta(role="assistant", content=""))
            async for event in events:
                seen.append(event)
                if isinstance(event, Delta) and event.text:
                    sent.append(event.text)
                    yield chunk(ChoiceDelta(content=event.text))
                elif isinstance(event, Error):
                    # The fixed text for the code; the run's message stays in the log and span.
                    code = event.code
                    yield self._error_frame(code, public_message(code), event.retryable, meta)
                    yield _DONE
                    return
            response = await meta.collect(seen)
            if response.status == "error":
                message = "the run ended without an end event"
                yield self._error_frame("engine_error", message, False, meta)
                yield _DONE
                return
            rest = rest_of_answer(response.output, "".join(sent))
            if rest:
                yield chunk(ChoiceDelta(content=rest))
            usage = _usage(response.metrics)
            if include_usage:
                yield chunk(ChoiceDelta(), finish=True)
                yield _chunk_frame(meta, [], usage, include_usage)
            else:
                yield chunk(ChoiceDelta(), finish=True, usage=usage)
            yield _DONE

        return StreamReply(frames(), _id_headers(meta.ids))

    def error(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> Reply:
        return self._error(code, message, retryable, meta.ids, None)

    @staticmethod
    def _error(code: str, message: str, retryable: bool, ids: Ids, param: str | None) -> Reply:
        status = status_for(code, retryable)
        headers = {**_id_headers(ids), "x-should-retry": "true" if retryable else "false"}
        return Reply(status, headers, _error_body(code, message, retryable, param, status))

    def _error_frame(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> str:
        status = status_for(code, retryable)
        body = _error_body(code, message, retryable, None, status)
        return f"data: {json.dumps(body, separators=(',', ':'))}\n\n"


_DONE = "data: [DONE]\n\n"


def _completion_id(meta: ReplyMeta) -> str:
    return f"chatcmpl-{meta.ids.request_id}"


def _id_headers(ids: Ids) -> dict[str, str]:
    return {"x-request-id": ids.request_id, "x-trace-id": ids.trace_id}


def _usage(metrics: Mapping[str, Any]) -> CompletionUsage:
    prompt, completion = int(metrics.get("input_tokens", 0)), int(metrics.get("output_tokens", 0))
    return CompletionUsage(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
    )


def _error_body(
    code: str, message: str, retryable: bool, param: str | None, status: int
) -> dict[str, Any]:
    # 409 `idempotency_in_progress` is the server's state, not a bad request (PoC-4 plan, sec. 4)
    kind = "invalid_request_error" if status < 500 and status != 409 else "server_error"
    error = OpenAIError(code=code, message=message, param=param, type=kind, retryable=retryable)
    return OpenAIErrorResponse(error=error).model_dump(mode="json", exclude_unset=True)


def _chunk_frame(
    meta: ReplyMeta,
    choices: list[ChunkChoice],
    usage: CompletionUsage | None,
    include_usage: bool,
) -> str:
    """One `data:` frame. With `include_usage`, every chunk carries `usage` (null but the last)."""
    fields: dict[str, Any] = {}
    if include_usage or usage is not None:
        fields["usage"] = usage
    chunk = ChatCompletionChunk(
        id=_completion_id(meta),
        object="chat.completion.chunk",
        created=meta.created,
        model=meta.served.agent,
        choices=choices,
        **fields,
    )
    return f"data: {chunk.model_dump_json(exclude_unset=True)}\n\n"
