"""The PoC-6 Mac command as one text, for the static checks that read it."""

from __future__ import annotations

from poc_harness.paths import ROOT

ENTRY = ROOT / "scripts/poc06_mac.sh"
PIECES_DIR = ROOT / "scripts/poc06_mac"
PIECES = ("lib.sh", "notes.sh", "steps.sh", "push.sh")
"""The sourced files, in the order the entry sources them."""


def mac_script_text() -> str:
    """The entry, then every piece in sourced order, so a check on ordering still holds."""
    paths = [ENTRY, *(PIECES_DIR / piece for piece in PIECES)]
    return "".join(path.read_text() for path in paths)
