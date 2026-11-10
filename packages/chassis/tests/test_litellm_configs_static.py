"""Static check of every LiteLLM config under `deploy/` (002 G-1): no key literal is written there.

Parses files only; no Docker, no socket. It does not read the router's logs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"
KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer [A-Za-z0-9._-]{8,}|AIza[0-9A-Za-z_-]{20,}")
ENV_REF = re.compile(r"^os\.environ/[A-Z][A-Z0-9_]*$")


def _is_router_config(path: Path) -> bool:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return False
    return isinstance(data, dict) and "model_list" in data


def _configs() -> list[Path]:
    found = [p for p in sorted(DEPLOY.rglob("*.yaml")) if "litellm" in str(p).lower()]
    return [p for p in found if _is_router_config(p)]


def _walk(node: Any, key: str = "") -> list[tuple[str, Any]]:
    if isinstance(node, dict):
        return [pair for k, v in node.items() for pair in _walk(v, str(k))]
    if isinstance(node, list):
        return [pair for v in node for pair in _walk(v, key)]
    return [(key, node)]


def test_the_scan_finds_the_router_configs() -> None:
    assert len(_configs()) >= 4


@pytest.mark.parametrize("path", _configs(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_key_literal_in_a_router_config(path: Path) -> None:
    assert not KEY_SHAPE.findall(path.read_text())
    for key, value in _walk(yaml.safe_load(path.read_text())):
        if key in {"api_key", "master_key"} and isinstance(value, str):
            assert value == "fake" or ENV_REF.match(value), f"{key} is a literal"
        if key == "master_key":
            assert ENV_REF.match(str(value)), "the master key is an environment reference"
