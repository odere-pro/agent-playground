"""The streamed answer's check in the chassis (PoC-8, 022 H-6; red-team case
`secret-04-streamed-answer-has-key`).

The `output` stage holds back a complete answer; a stream's deltas leave as they come, so it
cannot. This guard sits between the engine and the interface on every streamed run
(`Run.events`, `request.stream`), in every lane, with any guardrail adapter, `none` included:

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
  id and the kind, never the value. `end.output.text` is checked the same way.
- **URLs** (`spec.guardrails.output_url_allow`): what leaves goes through
  `output_filter.mask_urls`; a delta that changed leaves as one delta with the masked text.

What it cannot do: text that left before a non-delta event (a `tool_call`, `metrics`) is flushed
at that event, so a key split by one can leave in part before the rest is blocked. The check is
regex only: an encoded or spelled-out key passes, as on the `output` stage.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field

from chassis.core.events import Delta, End, Error, Event
from chassis.core.inbound import public_message
from chassis.ports.telemetry import TelemetryPort
from chassis.server.config import GuardrailsSpec
from chassis.server.output_filter import URL_MASKED, find_secret, mask_urls

__all__ = [
    "HOLD_MAX_CHARS",
    "STAGE",
    "WINDOW_CHARS",
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


async def guard_stream(
    source: AsyncGenerator[Event],
    spec: GuardrailsSpec,
    telemetry: TelemetryPort,
    *,
    request_id: str,
) -> AsyncGenerator[Event]:
    """`source`'s events through a `StreamFilter`. A block closes `source` and ends here."""
    flt = StreamFilter(allow=list(spec.output_url_allow), secrets=spec.stream_secrets)
    try:
        async with aclosing(source):
            async for event in source:
                for out in flt.on_event(event):
                    yield out
                if flt.blocked_kind is not None:
                    _blocked(telemetry, flt.blocked_kind, request_id)
                    return
            for out in flt.flush():
                yield out
            if flt.blocked_kind is not None:
                _blocked(telemetry, flt.blocked_kind, request_id)
    finally:
        if flt.masked:
            telemetry.counter(URL_MASKED, flt.masked)


def _blocked(telemetry: TelemetryPort, kind: str, request_id: str) -> None:
    telemetry.counter("chassis.guardrail_blocked", stage=STAGE, reason="secret")
    telemetry.log(
        "warning", "guardrail blocked", stage=STAGE, reasons=[kind], request_id=request_id
    )
