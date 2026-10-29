"""The AI-generated marking in the OpenAI and Anthropic bodies (PoC-8, 023 C-5, ADR-008 item 7).

`serve` puts `{MARKING_OPTION: True}` in `ReplyMeta.options` when `spec.governance.marking` is
on, so `chassis.core` does not change. The two chat adapters then add `BODY_FIELD` to their
answers: `{"ai_generated": true, "disclosure_required": true}` (suggested names, the same flags as
the `X-AI-Generated` and `X-AI-Disclosure-Required` headers). Neither format has a standard field
for it; both SDKs keep an unknown top-level field on the parsed object. With marking off the
field is not sent, as the headers are not.
"""

from __future__ import annotations

from typing import Any

from chassis.core.inbound import ReplyMeta

__all__ = ["BODY_FIELD", "FLAGS", "MARKING_OPTION", "marked", "with_marking"]

MARKING_OPTION = "ai_marking"
"""The `ReplyMeta.options` key `serve` sets when marking is on."""
BODY_FIELD = "ai_marking"
"""suggested: the top-level body field."""
FLAGS: dict[str, bool] = {"ai_generated": True, "disclosure_required": True}


def marked(meta: ReplyMeta) -> bool:
    return meta.options.get(MARKING_OPTION) is True


def with_marking(body: dict[str, Any], meta: ReplyMeta) -> dict[str, Any]:
    """`body` with `BODY_FIELD` added when marking is on; the chassis's flags always win."""
    return {**body, BODY_FIELD: dict(FLAGS)} if marked(meta) else body
