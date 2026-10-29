"""A small reader of the Prometheus text format for the observability tests. Not a test module."""

from __future__ import annotations

import re

_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? (\S+)$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

Key = tuple[str, frozenset[tuple[str, str]]]


def samples(text: str) -> dict[Key, float]:
    """Every sample line as `(name, labels) -> value`."""
    out: dict[Key, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        assert match, f"not a sample line: {line!r}"
        name, labels, value = match.groups()
        pairs = frozenset(_LABEL.findall(labels or ""))
        out[(name, pairs)] = float(value)
    return out


def families(text: str) -> dict[str, str]:
    """Every `# TYPE` line as `family -> kind`."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split(" ", 3)
            out[name] = kind
    return out


def value(text: str, name: str, **labels: str) -> float:
    """The one sample `name` whose labels include `labels`; 0 when there is none."""
    wanted = set(labels.items())
    found = [v for (n, ls), v in samples(text).items() if n == name and wanted <= ls]
    assert len(found) <= 1, f"{name} {labels} matches {len(found)} samples"
    return found[0] if found else 0.0
