"""The named routes (G-1b, issue 003). Every route in both Compose variants declares native
tool-call support and a version in `model_info`, the two variants name the same routes with the
same declarations, every other LiteLLM config under `deploy/` that serves one of these routes
declares it the same way, and the route table in `deploy/compose/README.md` matches the configs.
Static: the files are parsed; no router runs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
LITELLM = ROOT / "deploy/compose/litellm"
VARIANTS = (LITELLM / "config.yaml", LITELLM / "config.local.yaml")
README = ROOT / "deploy/compose/README.md"
ROUTES = {"big-default", "local-small"}
VERSION = re.compile(r"^[1-9][0-9]*$")
"""suggested: a route version is a positive integer, bumped on every change to its target."""
BACKTICKED = re.compile(r"`([^`]+)`")


def _entries(path: Path) -> list[dict[str, Any]]:
    config: dict[str, Any] = yaml.safe_load(path.read_text())
    return [dict(e) for e in config["model_list"]]


def _routes(path: Path) -> dict[str, dict[str, Any]]:
    return {str(e["model_name"]): dict(e.get("model_info") or {}) for e in _entries(path)}


def _models(path: Path) -> dict[str, str]:
    return {str(e["model_name"]): str(e["litellm_params"]["model"]) for e in _entries(path)}


def _is_litellm_config(path: Path) -> bool:
    text = path.read_text()
    return "model_list:" in text and "litellm_params" in text


OTHER_CONFIGS = sorted(
    p
    for p in (ROOT / "deploy").rglob("*.yaml")
    if p not in VARIANTS and _is_litellm_config(p) and ROUTES & set(_routes(p))
)
"""Every other LiteLLM config under `deploy/` that serves a named route (the scan of
test_litellm_route_resilience.py). Routes outside `ROUTES`, such as PoC-5's `fake-chat`, are
not G-1b routes and are not checked."""


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def test_the_scan_finds_every_config_that_serves_a_named_route() -> None:
    assert {_rel(p) for p in OTHER_CONFIGS} == {
        "deploy/compose/poc06/litellm.config.yaml",
        "deploy/compose/poc08-litellm/litellm.yaml",
        "deploy/kind/poc06/hosted/litellm-config.yaml",
    }


@pytest.mark.parametrize("path", VARIANTS, ids=lambda p: p.name)
def test_both_named_routes_exist(path: Path) -> None:
    assert set(_routes(path)) == ROUTES


@pytest.mark.parametrize("path", VARIANTS, ids=lambda p: p.name)
def test_every_route_declares_native_tool_calls_yes_or_no(path: Path) -> None:
    for route, info in _routes(path).items():
        assert isinstance(info.get("supports_function_calling"), bool), f"{route} in {path.name}"


@pytest.mark.parametrize("path", VARIANTS, ids=lambda p: p.name)
def test_every_route_has_a_version_and_an_owner(path: Path) -> None:
    for route, info in _routes(path).items():
        assert VERSION.match(str(info.get("route_version", ""))), f"{route} in {path.name}"
        assert str(info.get("owner", "")).strip(), f"{route} in {path.name} names no owner"


HISTORY: dict[str, dict[int, tuple[str, str]]] = {
    "big-default": {1: ("openai/fake-model", "os.environ/BIG_DEFAULT_MODEL")},
    "local-small": {1: ("openai/fake-model", "openai/local-small")},
}
"""Each route's version history: version -> (target in `config.yaml`, target in
`config.local.yaml`). A changed target needs a bumped `route_version` and a new entry here."""


def test_every_route_version_pins_its_targets_in_the_history() -> None:
    """A target change without a version bump, or a bump without a history entry, fails here."""
    fake_models, local_models = (_models(p) for p in VARIANTS)
    for route, info in _routes(VARIANTS[0]).items():
        version = int(info["route_version"])
        assert version in HISTORY[route], f"{route} v{version} has no HISTORY entry"
        assert (fake_models[route], local_models[route]) == HISTORY[route][version], (
            f"{route}: the target changed but route_version {version} did not"
        )


def test_the_history_has_every_version_up_to_the_current_one() -> None:
    for route, info in _routes(VARIANTS[0]).items():
        assert sorted(HISTORY[route]) == list(range(1, int(info["route_version"]) + 1)), route


def test_the_variants_declare_each_route_the_same_way() -> None:
    """A client sees one route, whichever variant runs: only the target behind it differs."""
    fake, local = (_routes(p) for p in VARIANTS)
    assert fake == local


@pytest.mark.parametrize("path", OTHER_CONFIGS, ids=_rel)
def test_every_other_config_declares_its_named_routes_as_the_variants_do(path: Path) -> None:
    """The same route name means the same tool-call support, version, and owner everywhere."""
    reference = _routes(VARIANTS[0])
    for route, info in _routes(path).items():
        if route in ROUTES:
            assert info == reference[route], f"{route} in {_rel(path)}"


def _table() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in README.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = cells[0].strip("`")
        if line.startswith("| `") and name in ROUTES:
            rows[name] = cells
    return rows


def _first_code(cell: str) -> str:
    """The first backticked value in a cell: the model string, before any prose about it."""
    match = BACKTICKED.search(cell)
    assert match, f"no backticked value in {cell!r}"
    return match.group(1)


def test_the_route_table_matches_the_config() -> None:
    """Columns: route, model (fake), model (local), owner, native tool calls, version."""
    table = _table()
    assert set(table) == ROUTES, "the route table in deploy/compose/README.md lists other routes"
    fake_models, local_models = (_models(p) for p in VARIANTS)
    for route, info in _routes(VARIANTS[0]).items():
        _, fake, local, owner, tools, version = table[route]
        assert _first_code(fake) == fake_models[route], route
        assert _first_code(local) == local_models[route], route
        assert _first_code(owner) == info["owner"], route
        assert tools == ("yes" if info["supports_function_calling"] else "no"), route
        assert version == str(info["route_version"]), route


def test_the_router_guide_links_the_route_table() -> None:
    guide = (ROOT / "docs/guides/point-a-client-at-the-router.md").read_text()
    assert "../../deploy/compose/README.md#model-routes-g-1b" in guide
    assert "## Model routes (G-1b)" in README.read_text()
