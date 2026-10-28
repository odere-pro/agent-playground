"""Percentiles, by linear interpolation between the closest ranks."""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["mean", "percentile"]


def percentile(values: Sequence[float], q: float) -> float | None:
    """The `q`-th percentile (0 to 100) of `values`, or None when there are none."""
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
