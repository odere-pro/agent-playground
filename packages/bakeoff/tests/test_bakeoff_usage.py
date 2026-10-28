"""The bakeoff skeleton prints its usage and exits with 2. PoC-6 scaffold."""

from __future__ import annotations

import pytest
from bakeoff import USAGE, main


def test_main_prints_the_usage_and_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert capsys.readouterr().out == USAGE
    assert USAGE.startswith("usage: python -m bakeoff")
