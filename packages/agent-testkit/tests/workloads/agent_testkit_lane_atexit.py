"""A deliberately bad workload for the stateless lane check (057 H-19): its calls write nothing, but
an `atexit` handler writes a file in `AGENT_TESTKIT_WRITE_DIR` when the process stops.
"""

from __future__ import annotations

import atexit
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any


def _flush() -> None:
    with suppress(OSError):  # hidden on purpose: the check must still see the write
        Path(os.environ["AGENT_TESTKIT_WRITE_DIR"], "flushed-at-exit.txt").write_text("kept")


atexit.register(_flush)


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    yield {"schema_version": "0", "type": "start", "request_id": str(ctx.get("request_id", ""))}
    yield {"schema_version": "0", "type": "delta", "text": str(input["text"]).upper()}
    yield {"schema_version": "0", "type": "end", "status": "ok"}
