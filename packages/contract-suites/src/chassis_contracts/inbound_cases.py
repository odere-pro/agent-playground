"""The case tables behind `InboundAdapterContract` (`chassis_contracts.inbound`).

The logical cases, the dataclasses a binder reads back into, `sse()`, and the run's events each
test scripts. `chassis_contracts.inbound` re-exports every name in its `__all__`, so import from
there.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from chassis import CHASSIS_VERSION
from chassis.core.envelope import Budget, Request, TaskInput, Versions
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.core.inbound import Ids, Served

# --- What every binding shares ---

AGENT = "echo"
"""The served agent: OpenAI and Anthropic bodies name it in `model`."""
AGENT_VERSION = "0.0.1"
SERVED = Served(
    agent=AGENT,
    agent_version=AGENT_VERSION,
    versions=Versions(
        chassis=CHASSIS_VERSION, config="cfg-1", prompt="prompt-1", model_route="fake-route"
    ),
)
IDS = Ids(
    request_id="req-inbound-1",
    trace_id="0af7651916cd43dd8448eb211c80319c",
    idempotency_key="idem-1",
)
OTHER_IDS = Ids(
    request_id="req-inbound-2",
    trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
    idempotency_key="idem-2",
)
CREATED = 1_759_276_800
"""`ReplyMeta.created`: a fixed Unix time, so a reply never depends on the clock."""
MASK = "<masked>"

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Logical:
    """One logical request (section 2): what every format can say."""

    text: str
    system: str | None = None
    history: tuple[tuple[Role, str], ...] = ()
    max_tokens: int = 256
    """Always set: Anthropic requires it."""
    stream: bool = False


@dataclass(frozen=True)
class LogicalCase:
    id: str
    logical: Logical
    expected: Request
    """The canonical request, ids masked."""


def _canonical(input: TaskInput, *, max_tokens: int, stream: bool) -> Request:
    return Request(
        request_id=MASK,
        trace_id=MASK,
        idempotency_key=MASK,
        agent=AGENT,
        agent_version=AGENT_VERSION,
        input=input,
        context_ref=None,
        stream=stream,
        budget=Budget(max_tokens=max_tokens, timeout_ms=30_000),
    )


def masked(request: Request) -> Request:
    """`request` with its three ids replaced by one mask."""
    return request.model_copy(
        update={"request_id": MASK, "trace_id": MASK, "idempotency_key": MASK}
    )


BASE = Logical(
    text="Explain SLM.",
    system="Answer in plain words.\nKeep it short.",
    history=(("user", "Hi."), ("assistant", "Hello. Ask me anything.")),
    max_tokens=300,
)
"""The logical request the refused and ignored cases are built on. Its system prompt has two
lines, so a format that splits a system prompt (two OpenAI system messages, two Anthropic text
blocks) can show they are joined with `"\\n"`."""

BASE_REQUEST = _canonical(
    TaskInput(
        text="Explain SLM.",
        data={
            "system": "Answer in plain words.\nKeep it short.",
            "history": [
                {"role": "user", "text": "Hi."},
                {"role": "assistant", "text": "Hello. Ask me anything."},
            ],
        },
    ),
    max_tokens=300,
    stream=False,
)

LOGICAL_CASES: tuple[LogicalCase, ...] = (
    LogicalCase(
        "one-turn",
        Logical("Simplify: the quick brown fox jumps over the lazy dog.", max_tokens=256),
        _canonical(
            TaskInput(text="Simplify: the quick brown fox jumps over the lazy dog.", data={}),
            max_tokens=256,
            stream=False,
        ),
    ),
    LogicalCase(
        "system-stream",
        Logical("What is an SLM?", system="Answer in plain words.", max_tokens=512, stream=True),
        _canonical(
            TaskInput(text="What is an SLM?", data={"system": "Answer in plain words."}),
            max_tokens=512,
            stream=True,
        ),
    ),
    LogicalCase(
        "history-no-system",
        Logical(
            "And in one word?",
            history=(("user", "What is an SLM?"), ("assistant", "A small language model.")),
            max_tokens=64,
        ),
        _canonical(
            TaskInput(
                text="And in one word?",
                data={
                    "history": [
                        {"role": "user", "text": "What is an SLM?"},
                        {"role": "assistant", "text": "A small language model."},
                    ]
                },
            ),
            max_tokens=64,
            stream=False,
        ),
    ),
    LogicalCase("base", BASE, BASE_REQUEST),
    LogicalCase(
        "everything-unicode-stream",
        Logical(
            "Naïve café résumé, 日本語?",
            system="Be brief.",
            history=(("user", "One."), ("assistant", "Two."), ("user", "Three.")),
            max_tokens=1,
            stream=True,
        ),
        _canonical(
            TaskInput(
                text="Naïve café résumé, 日本語?",
                data={
                    "system": "Be brief.",
                    "history": [
                        {"role": "user", "text": "One."},
                        {"role": "assistant", "text": "Two."},
                        {"role": "user", "text": "Three."},
                    ],
                },
            ),
            max_tokens=1,
            stream=True,
        ),
    ),
)
"""Every logical case and the canonical request it must give, written once for every format."""


# --- What a binder reads back ---


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ReadError:
    """An error as the client of the format reads it."""

    message: str
    code: str | None = None
    """The chassis error code, when the format carries it (OpenAI `error.code`, the Anthropic
    `"<code>: "` message prefix, a native `error` event); None when it does not."""
    type: str | None = None
    """The format's own error type (OpenAI `error.type`, Anthropic `error.type`)."""
    should_retry: bool | None = None
    """The `x-should-retry` header; None when absent."""


@dataclass(frozen=True)
class Readback:
    """What a client of the format reads from one answer.

    `text` is the answer text ("" for an answer with none; None for an error reply). `finish` is
    the format's stop reason (None when the format has none, or no finish was sent). `status` is
    the HTTP status. `run_status` is the run's chassis status where the format reports it (the
    native envelope, `x-chassis-status`), else None. `request_id` is the run's request id as the
    client can read it (the envelope, the `chatcmpl-`/`msg_` id), else None. `tool_calls` counts
    the tool calls the client is asked to run (OpenAI `tool_calls`/`function_call`, Anthropic
    `tool_use` blocks); the native envelope's `output.tool_calls` reports the agent's own calls
    and is not counted.
    """

    text: str | None
    usage: Usage | None
    finish: str | None
    status: int
    error: ReadError | None
    run_status: str | None = None
    request_id: str | None = None
    tool_calls: int = 0


@dataclass(frozen=True)
class BodyCase:
    """One format-specific body for the refused or ignored tests, built on `BASE`."""

    name: str
    body: Any
    headers: Mapping[str, str] = field(default_factory=dict)
    status: int = 400
    """Refused cases: the expected status."""
    code: str | None = None
    """Refused cases: the expected chassis code, when the binder pins one; else any of
    `INVALID_BODY_CODES` for a 400."""


Encode = Callable[[Logical], tuple[Any, Mapping[str, str]]]
ReadComplete = Callable[[int, Mapping[str, str], dict[str, Any]], Readback]
ReadStream = Callable[[int, Mapping[str, str], Sequence[str]], Readback]


@dataclass(frozen=True)
class SSEEvent:
    event: str | None
    data: str


def sse(frames: Iterable[str]) -> list[SSEEvent]:
    """Server-sent events from the frames a `StreamReply` yielded, whatever their chunking.
    Comment lines are dropped; several `data:` lines are joined with `"\\n"`.
    """
    text = "".join(frames).replace("\r\n", "\n")
    events: list[SSEEvent] = []
    for block in text.split("\n\n"):
        name: str | None = None
        data: list[str] = []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)
        if name is not None or data:
            events.append(SSEEvent(name, "\n".join(data)))
    return events


# --- The run's events each test scripts ---

ANSWER = ("Hello", ", ", "world.")
ANSWER_TEXT = "".join(ANSWER)
TOOL_ANSWER = "SLM means small language model."
END_OUTPUT_NO_TEXT: dict[str, Any] = {"score": 2, "tags": ["b", "a"], "verdict": {"ok": True}}
END_OUTPUT_TEXT: dict[str, Any] = {"text": "set by handle", "score": 2}
PARTIAL = "partial"
SDK_INTERFACES = frozenset({"openai", "anthropic"})
"""The interfaces that send a fixed text per error code (`public_message`)."""

MID_ERROR = Error(code="model.overloaded", message="try again later", retryable=True)

ERROR_CODES: tuple[tuple[str, bool], ...] = (
    ("unsupported_parameter", False),
    ("invalid_body", False),
    ("model_not_found", False),
    ("not_ready", True),
    ("a2a.timeout", True),
    ("engine_error", False),
    ("model.overloaded", True),
    ("workload.bad_event", False),
)
"""One code per row of the status table (section 3)."""
PRE_RUN_CODES = frozenset({"not_ready", "invalid_body"})
"""Answered before `to_request`: `ReplyMeta.request` is None."""

Mode = Literal["complete", "stream"]
MODES: tuple[Mode, ...] = ("complete", "stream")

END_OUTPUT_NO_TEXT_JSON = json.dumps(END_OUTPUT_NO_TEXT, separators=(",", ":"), sort_keys=True)


def _end_only(output: dict[str, Any]) -> list[Event]:
    """A run with no delta whose `end` carries `output`."""
    return [
        Start(request_id=IDS.request_id),
        Metrics(input_tokens=2, output_tokens=1),
        End(output=output),
    ]


END_OUTPUT_CASES: tuple[tuple[Mode, list[Event], str], ...] = (
    ("complete", _end_only(END_OUTPUT_NO_TEXT), END_OUTPUT_NO_TEXT_JSON),
    ("stream", _end_only(END_OUTPUT_NO_TEXT), END_OUTPUT_NO_TEXT_JSON),
    ("complete", _end_only(END_OUTPUT_TEXT), "set by handle"),
    ("stream", _end_only(END_OUTPUT_TEXT), "set by handle"),
    # With deltas before it, complete still answers `output.text`.
    (
        "complete",
        [
            Start(request_id=IDS.request_id),
            Delta(text="not the output"),
            End(output=END_OUTPUT_TEXT),
        ],
        "set by handle",
    ),
)
"""`(mode, events, the answer text)`: `end.output` with and without a string `text`."""
END_OUTPUT_IDS = (
    "no-text-complete",
    "no-text-stream",
    "text-complete",
    "text-stream",
    "text-after-deltas-complete",
)


def _answer_events() -> list[Event]:
    return [
        Start(request_id=IDS.request_id),
        *[Delta(text=t) for t in ANSWER],
        Metrics(input_tokens=7, output_tokens=5, attempt=1),
        End(status="ok"),
    ]
