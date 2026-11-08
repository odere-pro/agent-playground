"""Every LiteLLM config under `deploy/` sets `num_retries` and a `fallbacks` entry per route
(CH-16). LiteLLM retries a provider error and takes the fallback route itself, so the chassis
never retries a model call (`ModelPortContract.test_a_failed_call_goes_upstream_once`). A route
whose only alternative would corrupt a measurement (a fake behind a hosted bake-off route) names
an empty fallback list: the choice is stated, not left out. Static: the files are parsed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIGS = sorted(
    p
    for p in (ROOT / "deploy").rglob("*.yaml")
    if "model_list:" in p.read_text() and "litellm_params" in p.read_text()
)
POC05_CONFIG = ROOT / "deploy/kind/poc05/platform/litellm/config.yaml"
PINNED_COPIES = {ROOT / "deploy/kind/poc06/hosted/litellm-config.yaml": POC05_CONFIG}
"""A config pinned to be another file plus one route (PoC-6b's static test): it keeps that
file's `fallbacks` as they are, so its added route has no entry. The added route is a hosted
bake-off model, and a fallback to the fake would corrupt the bake-off anyway."""
MIN_RETRIES = 1
# suggested: 2 retries per route; LiteLLM's default is 0 on the proxy, and a third attempt costs a
# user more wait than it buys.
EXPECTED_RETRIES = 2


def _config(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return data


def _routes(config: dict[str, Any]) -> list[str]:
    return [str(entry["model_name"]) for entry in config["model_list"]]


def _fallbacks(config: dict[str, Any]) -> dict[str, list[str]]:
    """`router_settings.fallbacks`, a list of one-key maps, as one map."""
    out: dict[str, list[str]] = {}
    for entry in config.get("router_settings", {}).get("fallbacks", []):
        assert isinstance(entry, dict) and len(entry) == 1, entry
        ((route, targets),) = entry.items()
        assert route not in out, f"{route} is listed twice"
        out[route] = list(targets)
    return out


def test_the_scan_finds_the_known_configs() -> None:
    names = {str(p.relative_to(ROOT)) for p in CONFIGS}
    assert {
        "deploy/compose/litellm/config.yaml",
        "deploy/compose/litellm/config.local.yaml",
        "deploy/compose/poc06/litellm.config.yaml",
        "deploy/compose/poc08-litellm/litellm.yaml",
        "deploy/kind/poc05/platform/litellm/config.yaml",
        "deploy/kind/poc06/hosted/litellm-config.yaml",
    } <= names, names


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_route_sets_num_retries(path: Path) -> None:
    config = _config(path)
    for entry in config["model_list"]:
        params = entry["litellm_params"]
        assert "num_retries" in params, f"{entry['model_name']} in {path.name} sets no num_retries"
        assert isinstance(params["num_retries"], int) and params["num_retries"] >= MIN_RETRIES
        assert params["num_retries"] == EXPECTED_RETRIES, "one value everywhere, until measured"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_route_has_a_fallbacks_entry_that_names_routes_in_the_file(path: Path) -> None:
    config = _config(path)
    routes = _routes(config)
    fallbacks = _fallbacks(config)
    if path in PINNED_COPIES:
        assert fallbacks == _fallbacks(_config(PINNED_COPIES[path])), "the pinned copy differs"
        return
    for route in routes:
        assert route in fallbacks, f"{route} in {path.name} has no fallbacks entry"
        for target in fallbacks[route]:
            assert target in routes, f"{route} falls back to {target}, not a route in {path.name}"
            assert target != route, f"{route} falls back to itself"
    assert set(fallbacks) <= set(routes), "a fallbacks entry names a route the file has not"


def test_the_compose_routes_fall_back_to_each_other() -> None:
    """The two-route Compose configs: `big-default` and `local-small` each cover the other."""
    for name in ("deploy/compose/litellm/config.yaml", "deploy/compose/litellm/config.local.yaml"):
        fallbacks = _fallbacks(_config(ROOT / name))
        assert fallbacks == {"big-default": ["local-small"], "local-small": ["big-default"]}, name
