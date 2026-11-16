"""A deliberately bad workload for the stateless lane check (057 H-19): it keeps the last request
in a module variable and puts it in the next answer, so a reused pod answers differently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

last: list[str] = []


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    yield {"schema_version": "0", "type": "start", "request_id": str(ctx.get("request_id", ""))}
    seen = last[-1] if last else ""
    last.append(str(input["text"]))
    yield {"schema_version": "0", "type": "delta", "text": f"{input['text']} after {seen!r}"}
    yield {"schema_version": "0", "type": "end", "status": "ok"}
