"""Business-logic variants of the golden set, for the PoC-8 regression demo.

Every echo engine runs the same business logic: the simplifier prompt and the model behind it. In
CI that model is the scripted fake model server, so the answers the engines give are the script's.
A variant is a deterministic change to those answers, applied to the cases before the script is
generated (`bakeoff.golden.generate`), so no engine, workload, or chassis code changes:

- `normal`: the reference answers, as labelled.
- `drop-facts`: every answer loses its first expected fact (`expect.contains[0]`), the regression
  the planning doc's demo names: "A change to the business logic that drops facts fails the eval
  step in CI." An answer that does not hold the fact stays as it is.

The generated scripts are `golden-gate.yaml` and `golden-gate-drop-facts.yaml`; the eval run picks
one with `--logic` (`bakeoff.evalrun`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

from bakeoff.golden import Case

__all__ = ["LOGICS", "Logic", "apply_logic", "drop_first_fact"]

Logic = Literal["normal", "drop-facts"]
LOGICS: tuple[Logic, ...] = ("normal", "drop-facts")

_SPACES = re.compile(r"\s{2,}")
_BEFORE_PUNCT = re.compile(r"\s+([.,;:])")
_DOUBLE_PUNCT = re.compile(r"([,;:])\s*([.,;:])")


def drop_first_fact(text: str, fact: str) -> str:
    """`text` without the first case-insensitive `fact`, with the spacing and punctuation tidied.
    Unchanged when `fact` is not in `text`."""
    found = re.search(re.escape(fact), text, flags=re.IGNORECASE)
    if found is None:
        return text
    out = text[: found.start()] + text[found.end() :]
    out = _SPACES.sub(" ", out)
    out = _BEFORE_PUNCT.sub(r"\1", out)
    out = _DOUBLE_PUNCT.sub(r"\2", out)
    return out.strip(" ,;:")


def apply_logic(cases: Sequence[Case], logic: str) -> list[Case]:
    """The cases as `logic` answers them. Raises `ValueError` on an unknown logic."""
    if logic == "normal":
        return list(cases)
    if logic == "drop-facts":
        return [replace(c, reference=drop_first_fact(c.reference, c.contains[0])) for c in cases]
    raise ValueError(f"unknown logic {logic!r}; known: {', '.join(LOGICS)}")
