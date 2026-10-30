"""`proxy_fakes.sse_frames`: the proxy's frames are `event:` then `data:`, in that order."""

from __future__ import annotations

import pytest
from proxy_fakes import sse_frames


def test_sse_frames_reads_an_event_line_then_a_data_line() -> None:
    assert sse_frames('event: ping\ndata: {"type": "ping"}\n\n') == [("ping", {"type": "ping"})]


def test_sse_frames_refuses_data_before_event() -> None:
    """The shared parser reads this block the same as the right order, so this check is here."""
    with pytest.raises(AssertionError, match="event: ping"):
        sse_frames('data: {"type": "ping"}\nevent: ping\n\n')
