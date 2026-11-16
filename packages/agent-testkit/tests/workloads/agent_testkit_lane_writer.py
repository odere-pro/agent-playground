"""A deliberately bad workload for the stateless lane check (057 H-19): each call writes the input
text to a file in the folder named by `AGENT_TESTKIT_WRITE_DIR`. The agent hides the failure, so
only the read-only root check sees the write.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any


def _write(text: str) -> None:
    path = Path(os.environ["AGENT_TESTKIT_WRITE_DIR"], "last-request.txt")
    with suppress(OSError):  # hidden on purpose: the check must still see the write
        path.write_text(text)


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    yield {"schema_version": "0", "type": "start", "request_id": str(ctx.get("request_id", ""))}
    _write(str(input["text"]))
    yield {"schema_version": "0", "type": "delta", "text": str(input["text"]).upper()}
    yield {"schema_version": "0", "type": "end", "status": "ok"}
