"""The plain-A2A mode of the `remote` connector (contract v5 draft, part B).

A third-party A2A agent (kagent-adk, a managed runtime) sends no `metadata["chassis.event"]`. This
module reads its own A2A stream and builds chassis events from it. `PlainTranslator` is fed each
`StreamResponse` of one run and returns raw event dicts; `plain_message` builds the request. It is
chassis-only: `mapping.py`, and its copy in `packages/workload-a2a`, are not touched.

Rules the translator holds by construction, because no server-side check exists for a third party:

- `start` first and once (the id is the request's), one terminal event last, nothing after it.
- It never reads `metadata["chassis.event"]` or `task.history`. A remote cannot forge a `tool_call`,
  an `end` with status `retry` or `fallback`, or a chassis error code.
- Text parts only. A text part of a status or artifact update is a `delta`. An artifact event with
  `last_chunk` and no `append` is a snapshot of the whole artifact: kept, and used as `end.output`
  only if no `delta` was sent.
- `COMPLETED` is `metrics` then `end ok`. `FAILED` and `REJECTED` are `error {a2a.failed}` with
  FIXED text; the remote's own text is logged, redacted and capped, and goes nowhere else.
  `INPUT_REQUIRED` and `AUTH_REQUIRED` are `error {a2a.unsupported_state}` and leave the task open
  on the remote (`server_finished` is false), so the connector cancels it.
- Nothing the remote says in words reaches a `Response`, a span, or an event: the failure states
  have fixed text, and so do transport and timeout errors in this mode (the connector asks
  `RemoteConnector` for the text; the SDK puts the remote's payload in its exceptions). The
  remote's text goes to the log only, through `redact`.
- Usage is read from one metadata key (`usage_key`), strictly: non-negative integers only, a float
  with no fractional part counts as an integer (protobuf `Struct` numbers are doubles), anything
  else counts as zero and is logged once per run. The last value wins; values are never summed.
  Zeros mean unknown.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from a2a.helpers import new_data_part, new_text_part
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskState,
)
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Struct

from chassis.core.events import SCHEMA_VERSION

REMOTE_TEXT_CAP = 300
"""suggested: characters of the remote's own failure text that reach the log."""
MAX_ARTIFACTS = 64
"""suggested: distinct artifacts whose snapshot is kept; more are ignored, with one log line."""
MAX_COUNT = 2**53
"""suggested: the largest token count read. A double above it is not exact; it counts as zero."""

INPUT_ALIASES = ("promptTokenCount", "prompt_tokens", "input_tokens", "inputTokens")
OUTPUT_ALIASES = ("candidatesTokenCount", "completion_tokens", "output_tokens", "outputTokens")
"""suggested: the aliases, from the kagent-adk (Google GenAI) and OpenAI-style usage objects."""

_WORKING = frozenset(
    {TaskState.TASK_STATE_UNSPECIFIED, TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING}
)
_FAILED = frozenset({TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED})
_UNSUPPORTED = frozenset({TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED})
_TERMINAL = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)

_BEARER = re.compile(r"(?i)bearer[\s:]+\S+")
_KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=.]{32,}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")

Log = Callable[..., None]
"""`TelemetryPort.log` shaped: `(level, message, **fields)`."""


@dataclass(frozen=True)
class PlainOptions:
    """`spec.engine.a2a`, as the connector reads it."""

    usage_key: str | None = None
    context_id: Literal["omit", "trace_id"] = "omit"

    @classmethod
    def from_mapping(cls, config: object) -> PlainOptions:
        """Read `spec.engine.a2a` from the `setup` mapping; `ValueError` on a bad value."""
        if config is None:
            return cls()
        if not isinstance(config, dict):
            raise ValueError("engine.a2a must be a mapping")
        usage_key = config.get("usage_key")
        if usage_key is not None and (not isinstance(usage_key, str) or not usage_key):
            raise ValueError("engine.a2a.usage_key must be a non-empty string or null")
        context_id = config.get("context_id", "omit")
        if context_id not in ("omit", "trace_id"):
            raise ValueError("engine.a2a.context_id must be 'omit' or 'trace_id'")
        return cls(usage_key=usage_key, context_id=context_id)


def plain_message(
    input: dict[str, Any], ctx: dict[str, Any], options: PlainOptions
) -> SendMessageRequest:
    """One `Request` as a `SendMessageRequest` for a third-party agent.

    A text part with `input.text` when set, a data part with `input.data` when not empty,
    `context_id` per `options`, and NO metadata: `chassis.ctx` holds the agent name, the
    idempotency key, the budget, and the versions, which a third party has no use for.
    """
    parts: list[Part] = []
    text = input.get("text")
    if text:
        parts.append(new_text_part(text))
    data = input.get("data")
    if data:
        parts.append(new_data_part(data, media_type="application/json"))
    message = Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)
    if options.context_id == "trace_id":
        message.context_id = str(ctx["trace_id"])
    return SendMessageRequest(message=message)


def redact(text: str, cap: int = REMOTE_TEXT_CAP, secret: str | None = None) -> str:
    """The remote's text for a log line: the exact `secret` (the remote's own bearer token, which
    the shapes below can miss when it is short), no control characters, no bearer or key shape, no
    long token-like run, at most `cap` characters. The secret goes first, before the cut.
    """
    if secret:
        text = text.replace(secret, "[redacted]")
    text = _CONTROL.sub(" ", text)
    text = _BEARER.sub("[redacted]", text)
    text = _KEY_SHAPE.sub("[redacted]", text)
    text = _LONG_TOKEN.sub("[redacted]", text)
    return text[:cap]


def _text(parts: Any) -> str:
    """The text parts joined with no separator. Data, file, and URL parts are ignored."""
    return "".join(p.text for p in parts if p.HasField("text"))


def _strict_count(value: Any) -> tuple[int, str | None]:
    """`(count, None)` for a non-negative integer, or a float with no fractional part; else
    `(0, reason)`.
    """
    if isinstance(value, bool):
        return 0, "a boolean"
    if isinstance(value, int):
        number: float = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            return 0, "not finite"
        if value != math.floor(value):
            return 0, "a fraction"
        number = value
    else:
        return 0, f"a {type(value).__name__}"
    if number < 0:
        return 0, "negative"
    if number > MAX_COUNT:
        return 0, "too large"
    return int(number), None


class PlainTranslator:
    """One run's reader of a plain A2A stream. Made per run; see the module docstring."""

    def __init__(
        self, request_id: str, options: PlainOptions, log: Log, secret: str | None = None
    ) -> None:
        self._secret = secret
        """The remote's bearer token, removed from the text that goes to the log."""
        self._request_id = request_id
        self._options = options
        self._log = log
        self._started = False
        self._finished = False
        self._server_finished = False
        self._task_id: str | None = None
        self._delta_sent = False
        self._snapshots: dict[str, str] = {}
        """Artifact id to the whole text of that artifact, in the order first seen."""
        self._usage: tuple[int, int] = (0, 0)
        self._usage_known = False
        self._logged: set[str] = set()

    @property
    def server_finished(self) -> bool:
        """True after `COMPLETED`, `FAILED`, `REJECTED`, `CANCELED`; false after a pause state,
        where the task is still open on the remote."""
        return self._server_finished

    @property
    def task_id(self) -> str | None:
        """The remote's task id, from the first `task`, `status_update`, or `artifact_update`."""
        return self._task_id

    @property
    def span_attributes(self) -> dict[str, Any]:
        return {"a2a.protocol": "a2a", "a2a.usage_known": self._usage_known}

    # --- reading ---

    def feed(self, response: StreamResponse) -> list[dict[str, Any]]:
        if self._finished:
            return []
        out: list[dict[str, Any]] = []
        if not self._started:
            self._started = True
            out.append(
                {"schema_version": SCHEMA_VERSION, "type": "start", "request_id": self._request_id}
            )
        if response.HasField("task"):
            task = response.task
            self._remember_task(task.id)
            self._read_usage(task.metadata)
            out += self._on_state(
                task.status.state,
                _text(task.status.message.parts) if task.status.HasField("message") else "",
                artifacts=[(a.artifact_id, _text(a.parts)) for a in task.artifacts],
            )
        elif response.HasField("status_update"):
            update = response.status_update
            self._remember_task(update.task_id)
            self._read_usage(update.metadata)
            text = _text(update.status.message.parts) if update.status.HasField("message") else ""
            out += self._on_state(update.status.state, text)
        elif response.HasField("artifact_update"):
            out += self._on_artifact(response)
        elif response.HasField("message"):
            message = response.message
            self._read_usage(message.metadata)
            out += self._complete(_text(message.parts))
        return out

    def _remember_task(self, task_id: str) -> None:
        if self._task_id is None and task_id:
            self._task_id = task_id

    def _on_artifact(self, response: StreamResponse) -> list[dict[str, Any]]:
        update = response.artifact_update
        self._remember_task(update.task_id)
        self._read_usage(update.metadata)
        self._read_usage(update.artifact.metadata)
        text = _text(update.artifact.parts)
        if update.last_chunk and not update.append:
            # A snapshot of the whole artifact (kagent repeats the full text): not a delta.
            self._keep_snapshot(update.artifact.artifact_id, text)
            return []
        return self._delta(text)

    def _keep_snapshot(self, artifact_id: str, text: str) -> None:
        """Keep an artifact's whole text for `end.output`. Nothing is kept once a delta went out,
        and at most `MAX_ARTIFACTS` artifacts are, so a remote cannot grow the translator."""
        if not text or self._delta_sent:
            return
        if artifact_id not in self._snapshots and len(self._snapshots) >= MAX_ARTIFACTS:
            self._log_once("too-many-artifacts", "plain a2a artifact snapshot ignored (too many)")
            return
        self._snapshots[artifact_id] = text

    def _delta(self, text: str) -> list[dict[str, Any]]:
        if not text:
            return []
        self._delta_sent = True
        self._snapshots.clear()
        return [{"schema_version": SCHEMA_VERSION, "type": "delta", "text": text}]

    def _on_state(
        self, state: int, text: str, *, artifacts: list[tuple[str, str]] | None = None
    ) -> list[dict[str, Any]]:
        if state in _WORKING:
            return self._delta(text) if artifacts is None else []
        if state == TaskState.TASK_STATE_COMPLETED:
            for artifact_id, artifact_text in artifacts or []:
                self._keep_snapshot(artifact_id, artifact_text)
            return self._complete(text)
        if state in _FAILED:
            return self._failed(state, text)
        if state == TaskState.TASK_STATE_CANCELED:
            self._stop(server_finished=True)
            return [self._error("a2a.canceled", "the task was canceled")]
        if state in _UNSUPPORTED:
            self._stop(server_finished=False)
            name = TaskState.Name(state)
            return [self._error("a2a.unsupported_state", f"{name} is not in the contract")]
        return []

    def _stop(self, *, server_finished: bool) -> None:
        self._finished = True
        self._server_finished = server_finished

    def _complete(self, fallback_text: str) -> list[dict[str, Any]]:
        """`metrics`, then `end ok`. `output` only when no delta went out: the snapshots in the
        order first seen, else the terminal message's text."""
        self._stop(server_finished=True)
        end: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "type": "end", "status": "ok"}
        if not self._delta_sent:
            text = "".join(self._snapshots.values()) or fallback_text
            if text:
                end["output"] = {"text": text}
        return [self._metrics(), end]

    def _failed(self, state: int, text: str) -> list[dict[str, Any]]:
        self._stop(server_finished=True)
        name = TaskState.Name(state)
        # The remote's own text is for the operator's log, never for the response.
        self._log(
            "warning",
            "plain a2a remote failed",
            state=name,
            remote_text=redact(text, secret=self._secret),
        )
        out = [self._metrics()] if self._usage_known else []
        return [*out, self._error("a2a.failed", f"the task ended in state {name}")]

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "type": "error",
            "code": code,
            "message": message,
            "retryable": False,
        }

    def _metrics(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "type": "metrics",
            "input_tokens": self._usage[0],
            "output_tokens": self._usage[1],
            "attempt": 1,
        }

    # --- usage ---

    def _log_once(self, reason: str, message: str) -> None:
        if reason not in self._logged:
            self._logged.add(reason)
            self._log("warning", message, reason=reason)

    def _count(self, usage: dict[str, Any], aliases: tuple[str, ...], side: str) -> int:
        """The first alias present decides; a value that is not a strict count is zero."""
        for alias in aliases:
            if alias in usage:
                count, reason = _strict_count(usage[alias])
                if reason is not None:
                    self._log_once(
                        f"{side}:{reason}", f"plain a2a usage {side} count ignored ({reason})"
                    )
                return count
        return 0

    def _read_usage(self, metadata: Struct) -> None:
        key = self._options.usage_key
        if key is None or not metadata.fields:
            return
        if key not in metadata.fields:
            return
        found = json_format.MessageToDict(metadata.fields[key])
        if found is None:
            self._log_once("not-an-object", "plain a2a usage value is not an object; ignored")
            return
        if not isinstance(found, dict):
            self._log_once("not-an-object", "plain a2a usage value is not an object; ignored")
            return
        # The last value wins. kagent reports the same totals on the final artifact and status.
        self._usage = (
            self._count(found, INPUT_ALIASES, "input"),
            self._count(found, OUTPUT_ALIASES, "output"),
        )
        self._usage_known = True
