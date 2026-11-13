"""A deliberately bad workload for the input-as-data case (025 H-10): `echo_python`, except that
the input text is merged into the system prompt and the user message is a fixed line.

It runs only in the workload process the test starts (this folder on its `PYTHONPATH`), so the
swap of `echo_python.handle._messages` never reaches the test process.
"""

from __future__ import annotations

import importlib
from typing import Any

# The module, not the `handle` function `echo_python` exports under the same name.
echo = importlib.import_module("echo_python.handle")

FIXED_ASK = "Rewrite the text in the system prompt."


def merged_messages(text: str) -> list[dict[str, Any]]:
    """The anti-pattern: the input becomes part of the system prompt."""
    system = f"{echo.SYSTEM_PROMPT}\n\nText:\n{text}"
    return [{"role": "system", "content": system}, {"role": "user", "content": FIXED_ASK}]


setattr(echo, "_messages", merged_messages)  # noqa: B010  (a private name, swapped on purpose)
handle = echo.handle
