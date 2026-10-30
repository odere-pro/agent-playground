"""Exit criterion 2 (security part): the real `OidcAuth` and `LiteLLMGuardrails` adapters bind
the same contract suites as their fakes (`AuthPortContract`, `GuardrailPortContract`), and the
registry builds them from `spec.adapters` by name. The bindings run in `packages/chassis/tests`
(`test_oidc_adapter.py`, `test_litellm_guardrails.py`), offline: a JWKS on an httpx
`MockTransport`, and a LiteLLM stub in front of the guardrail server over ASGI.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from chassis.profiles import REGISTRY

ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "packages/chassis/tests"
BINDINGS = {
    "test_oidc_adapter.py": ("TestOidcAuth", "AuthPortContract"),
    "test_litellm_guardrails.py": ("TestLiteLLMGuardrails", "GuardrailPortContract"),
}


@pytest.mark.parametrize("filename", sorted(BINDINGS))
def test_the_real_adapter_binds_the_fakes_suite(filename: str) -> None:
    """Criterion 2: "Each new real adapter passes the same contract suite as its fake": security."""
    binder, suite = BINDINGS[filename]
    tree = ast.parse((TESTS / filename).read_text())
    classes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert binder in classes
    bases = {b.id for b in classes[binder].bases if isinstance(b, ast.Name)}
    assert suite in bases


def test_the_registry_names_both_adapters_by_config() -> None:
    """Criterion 2: "Each new real adapter" is built by name from `spec.adapters`."""
    assert callable(REGISTRY["auth"]["oidc"])
    assert callable(REGISTRY["guardrails"]["litellm"])
