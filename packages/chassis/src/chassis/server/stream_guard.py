"""The streamed answer's checks in the chassis: the regex filter (PoC-8, 022 H-6; red-team case
`secret-04-streamed-answer-has-key`) and the `output` stage's sentence window (R7-04).

A complete call's `output` stage holds back the whole answer; a stream's deltas leave as they
come, so it cannot. This guard sits between the engine and the interface on a streamed run
(`Run.events`, `request.stream`) when either check below is on, in every lane:

- **Hold a suspect last word.** When the last word could be the start of a secret or a link (it
  holds one of `[](){}<>=:/_-` or a digit, is all capitals, or is a prefix of `sk` or `Bearer`),
  it waits for the next whitespace, so a key or a URL is whole before any of it leaves. Plain words
  leave at once. Deltas are never split: they leave whole, as the engine gave them, once the held
  word they touch is done. Past `HOLD_MAX_CHARS` held (suggested), all but the last
  `WINDOW_CHARS` leaves, split if need be. When the text before the held word ends in a link
  opener (`](`, `]:`, `=`), the word with the opener is held too, so a destination and its
  opener leave together.
- **Secrets** (`spec.guardrails.stream_secrets`, default true): before anything leaves, the held
  text and the last `WINDOW_CHARS` sent are matched against `output_filter.SECRET_PATTERNS`. A
  match ends the stream: the held text is dropped, the engine's stream is closed, and one `error`
  event `guardrail_blocked` (not retryable, the fixed public text) is the last event. Counted as
  `chassis.guardrail_blocked{stage="output_stream", reason="secret"}` and logged with the request
  id and the kind, never the value. `end.output.text` is checked the same way. This runs first,
  with any guardrail adapter, `none` included.
- **URLs** (`spec.guardrails.output_url_allow`): what leaves goes through
  `output_filter.mask_urls`; a delta that changed leaves as one delta with the masked text.
- **The `output` stage, one window at a time** (`guard.enabled(ports, spec, "output")`: a
  guardrail port other than `NoGuardrails` and `spec.guardrails.output` on). What the regex filter
  releases is collected into a window and sent to the port at stage `output` through
  `guard.check_text`, before any of it leaves. A window closes at a sentence end (`.`, `!`, `?`
  followed by whitespace) or a newline, at the end of a delta that sits on whitespace; before any
  non-delta event; at `end`; or at `PORT_WINDOW_CHARS` (suggested), cut at the last whitespace.
  A window breaks only at whitespace, so a masked text lines up with what it replaces. Clean: the
  window's deltas leave as they came. Masked: one delta with the masked text. Blocked, or the port
  down with `fail_closed`: the window is dropped, the engine's stream is closed, and the last
  event is the stage's `error` (`guardrail_blocked`, not retryable; `guardrail_unavailable`,
  retryable); no `end`. The port down with `fail_open`: the window passes, and the rest of that
  stream skips the port (suggested), so one outage does not add a timeout to every sentence.
  `end.output.text` goes through the port too (masked, or it blocks). A regex block drops the
  open window unchecked. Counts and logs are `guard.py`'s: ids and reasons, never the text.

What it cannot do: text that left is never pulled back. A window that left before a block stays
with the client, and so does text that left before a non-delta event (a `tool_call`, `metrics`), so
a key split by one can leave in part before the rest is blocked. PII that spans two windows (a
newline or a sentence end inside it) can pass, and nothing behind this catches it: LiteLLM's
`pii-mask` is `pre_call`, so it masks the model's input, not its answer. The regex check is regex
only: an encoded or spelled-out key passes, as on the `output` stage. With the `output` stage on, a
delta waits for its window to close and for one port call, so the first delta leaves after the
first sentence, not the first word.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field

from chassis.core.events import Delta, End, Error, Event
from chassis.core.inbound import public_message
from chassis.ports.bundle import PortBundle
from chassis.ports.telemetry import TelemetryPort
from chassis.server import guard
from chassis.server.config import GuardrailsSpec
from chassis.server.output_filter import URL_MASKED, find_secret, mask_urls

__all__ = [
    "HOLD_MAX_CHARS",
    "PORT_WINDOW_CHARS",
    "STAGE",
    "WINDOW_CHARS",
    "PortWindow",
    "StreamFilter",
    "guard_stream",
    "stream_guard_on",
]

STAGE = "output_stream"
"""The `stage` label of a block here (not a `GuardrailPort` stage)."""
BLOCKED_CODE = "guardrail_blocked"
WINDOW_CHARS = 256
"""suggested: how much sent text a new piece is matched with."""
HOLD_MAX_CHARS = 4096
"""suggested: the most text held back; past it, all but the last `WINDOW_CHARS` leaves."""
OPENERS = ("](", "]:", "=")
PORT_WINDOW_CHARS = 512
"""suggested: the longest text one `output` port call on a stream sees."""
_WINDOW_END = re.compile(r"[.!?](?=\s)|\n")


def stream_guard_on(spec: GuardrailsSpec) -> bool:
    """Either check is on: secrets, or an allow-list that is not `*`."""
    return spec.stream_secrets or "*" not in spec.output_url_allow


_SUSPECT_CHARS = frozenset("[](){}<>=:/_-")
_PREFIXES = ("sk", "Bearer")


def _suspect(word: str) -> bool:
    """`word` could be the start of a secret or a link: it holds a markup or key character or a
    digit, is all capitals, or is a prefix of `sk` or `Bearer`. A plain word is not held."""
    if not word:
        return False
    if any(c in _SUSPECT_CHARS or c.isdigit() for c in word):
        return True
    if word.isupper():
        return True
    return any(p.startswith(word) for p in _PREFIXES)


def _word_start(text: str, end: int) -> int:
    start = end
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    return start


def _boundary(text: str) -> int:
    """How much of `text` may leave: all of it, or up to the last word when that word is suspect,
    or one word earlier when the text before it ends in a link opener."""
    start = _word_start(text, len(text))
    cut = start if _suspect(text[start:]) else len(text)
    head = text[:cut].rstrip()
    if cut and head.endswith(OPENERS):
        return _word_start(head, len(head))
    return cut


@dataclass
class StreamFilter:
    """The held deltas and the sent tail of one stream. `on_event` gives what may leave now."""

    allow: list[str]
    secrets: bool = True
    held: list[Delta] = field(default_factory=list)
    tail: str = ""
    blocked_kind: str | None = None
    masked: int = 0

    def on_event(self, event: Event) -> list[Event]:
        if isinstance(event, Delta):
            return self.feed(event)
        if isinstance(event, End):
            return self.end(event)
        return [*self.flush(), event]

    def feed(self, delta: Delta) -> list[Event]:
        if not delta.text:
            return []
        self.held.append(delta)
        text = "".join(d.text for d in self.held)
        if self._secret(text):
            return [self._block()]
        boundary = _boundary(text)
        split = len(text) - boundary > HOLD_MAX_CHARS
        return self._release(len(text) - WINDOW_CHARS if split else boundary, split=split)

    def flush(self) -> list[Event]:
        text = "".join(d.text for d in self.held)
        if text and self._secret(text):
            return [self._block()]
        return self._release(len(text))

    def end(self, event: End) -> list[Event]:
        out = self.flush()
        if self.blocked_kind is not None:
            return out
        output = event.output
        if output is None or not isinstance(output.get("text"), str):
            return [*out, event]
        if self.secrets and (kind := find_secret(output["text"])) is not None:
            self.blocked_kind = kind
            return [*out, self._error()]
        text, count = mask_urls(output["text"], self.allow)
        if count:
            self.masked += count
            event = event.model_copy(update={"output": {**output, "text": text}})
        return [*out, event]

    def _secret(self, text: str) -> bool:
        if not self.secrets:
            return False
        kind = find_secret(self.tail + text)
        if kind is not None:
            self.blocked_kind = kind
        return kind is not None

    def _block(self) -> Error:
        self.held = []
        return self._error()

    @staticmethod
    def _error() -> Error:
        return Error(code=BLOCKED_CODE, message=public_message(BLOCKED_CODE), retryable=False)

    def _release(self, boundary: int, *, split: bool = False) -> list[Event]:
        """Whole held deltas up to the last delta end at or before `boundary` that sits on a word
        edge, URLs masked; so no word is cut. With `split` (the hold passed its cap), the delta
        that straddles `boundary` is split there."""
        text = "".join(d.text for d in self.held)
        cut, end = 0, 0
        for delta in self.held:
            end += len(delta.text)
            if end > boundary:
                break
            if end == len(text) or text[end - 1].isspace() or text[end].isspace():
                cut = end
        if split and cut < boundary:
            cut = boundary
        if cut == 0:
            return []
        group: list[Delta] = []
        used = 0
        while used < cut:
            delta = self.held.pop(0)
            if used + len(delta.text) > cut:  # only with `split`
                self.held.insert(0, Delta(text=delta.text[cut - used :]))
                delta = Delta(text=delta.text[: cut - used])
            group.append(delta)
            used += len(delta.text)
        released = "".join(d.text for d in group)
        masked, count = mask_urls(released, self.allow)
        self.tail = (self.tail + masked)[-WINDOW_CHARS:]
        if count:
            self.masked += count
            return [Delta(text=masked)]
        return list[Event](group)


def _at_space(text: str, end: int) -> bool:
    return text[end - 1].isspace() or (end < len(text) and text[end].isspace())


def _window_cut(text: str, ends: list[int]) -> int:
    """Where the first window of `text` closes, or 0 while it stays open. `ends` are the delta
    ends in `text`. The first delta end on whitespace at or past a sentence end or a newline,
    within `PORT_WINDOW_CHARS`; else, at the cap or more, the last whitespace within the cap (all
    of it when there is none), so no window passes the cap."""
    stops = [m.end() for m in _WINDOW_END.finditer(text)]
    if stops:
        for end in ends:
            if end > PORT_WINDOW_CHARS:
                break
            if end >= stops[0] and _at_space(text, end):
                return end
    if len(text) < PORT_WINDOW_CHARS:
        return 0
    head = text[:PORT_WINDOW_CHARS]
    space = max(head.rfind(c) for c in " \t\n\r")
    return space + 1 if space >= 0 else len(text)


@dataclass
class PortWindow:
    """The `output` stage on one stream: what the regex filter releases, a window at a time.
    `on_event` gives what may leave now; `error` is set when the stream must end."""

    ports: PortBundle
    spec: GuardrailsSpec
    request_id: str
    pending: list[Delta] = field(default_factory=list)
    skip: bool = False
    error: Error | None = None

    async def on_event(self, event: Event) -> list[Event]:
        if isinstance(event, Delta):
            return await self.feed(event)
        out = await self.flush()
        if self.error is not None:
            return out
        if isinstance(event, End):
            event = await self._end(event)
            if self.error is not None:
                return [*out, self.error]
        return [*out, event]

    async def feed(self, delta: Delta) -> list[Event]:
        if not delta.text:
            return []
        self.pending.append(delta)
        out: list[Event] = []
        while self.pending and self.error is None:
            text = "".join(d.text for d in self.pending)
            ends = _ends(self.pending)
            cut = _window_cut(text, ends)
            if cut == 0:
                break
            out.extend(await self._send(cut))
        return out

    async def flush(self) -> list[Event]:
        """The open window, checked: before a non-delta event and when the stream ends."""
        size = sum(len(d.text) for d in self.pending)
        return await self._send(size) if size else []

    async def _send(self, cut: int) -> list[Event]:
        window = self._take(cut)
        text = "".join(d.text for d in window)
        if self.skip:
            return list[Event](window)
        outcome = await self._check(text)
        if outcome is None:
            return [self.error] if self.error is not None else []
        return list[Event](window) if outcome == text else [Delta(text=outcome)]

    async def _end(self, event: End) -> End:
        output = event.output
        if self.skip or output is None or not isinstance(output.get("text"), str):
            return event
        outcome = await self._check(output["text"])
        if outcome is None or outcome == output["text"]:
            return event
        return event.model_copy(update={"output": {**output, "text": outcome}})

    async def _check(self, text: str) -> str | None:
        """The text to pass on, or None with `error` set."""
        outcome = await guard.check_text(
            self.ports, self.spec, text, "output", request_id=self.request_id
        )
        if isinstance(outcome, Error):
            self.pending = []
            self.error = outcome
            return None
        if guard.UNCHECKED in outcome.reasons:
            self.skip = True
        return outcome.text

    def _take(self, cut: int) -> list[Delta]:
        """The pending deltas up to `cut`; a delta that straddles it is split there."""
        window: list[Delta] = []
        used = 0
        while used < cut:
            delta = self.pending.pop(0)
            if used + len(delta.text) > cut:
                self.pending.insert(0, Delta(text=delta.text[cut - used :]))
                delta = Delta(text=delta.text[: cut - used])
            window.append(delta)
            used += len(delta.text)
        return window


def _ends(deltas: list[Delta]) -> list[int]:
    out: list[int] = []
    end = 0
    for delta in deltas:
        end += len(delta.text)
        out.append(end)
    return out


async def guard_stream(
    source: AsyncGenerator[Event],
    spec: GuardrailsSpec,
    ports: PortBundle,
    *,
    request_id: str,
) -> AsyncGenerator[Event]:
    """`source`'s events through a `StreamFilter`, then a `PortWindow` when the `output` stage is
    on. A block or a closed outage closes `source` and ends here."""
    telemetry = ports.telemetry
    flt = StreamFilter(allow=list(spec.output_url_allow), secrets=spec.stream_secrets)
    window = PortWindow(ports, spec, request_id) if guard.enabled(ports, spec, "output") else None
    try:
        async with aclosing(source):
            async for event in source:
                for out in await _pass(flt, window, flt.on_event(event)):
                    yield out
                if flt.blocked_kind is not None:
                    _blocked(telemetry, flt.blocked_kind, request_id)
                    return
                if window is not None and window.error is not None:
                    return
            tail = await _pass(flt, window, flt.flush())
            if window is not None and window.error is None and flt.blocked_kind is None:
                tail.extend(await window.flush())
            for out in tail:
                yield out
            if flt.blocked_kind is not None:
                _blocked(telemetry, flt.blocked_kind, request_id)
    finally:
        if flt.masked:
            telemetry.counter(URL_MASKED, flt.masked)


async def _pass(flt: StreamFilter, window: PortWindow | None, events: list[Event]) -> list[Event]:
    """What the regex filter released, through the window. A regex block drops the open window
    unchecked: the stream ends with the filter's `error`."""
    if window is None:
        return events
    out: list[Event] = []
    for event in events:
        if flt.blocked_kind is not None and isinstance(event, Error):
            window.pending = []
            out.append(event)
            break
        out.extend(await window.on_event(event))
        if window.error is not None:
            break
    return out


def _blocked(telemetry: TelemetryPort, kind: str, request_id: str) -> None:
    telemetry.counter("chassis.guardrail_blocked", stage=STAGE, reason="secret")
    telemetry.log(
        "warning", "guardrail blocked", stage=STAGE, reasons=[kind], request_id=request_id
    )
