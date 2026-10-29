"""Exit criterion 2, part one: the four new ports exist in the PoC-1 shape. Each is a Protocol
in `chassis.ports`, has a no-op default in `PortBundle`, a fake registered in `REGISTRY`, and a
contract suite the fake passes (`packages/chassis/tests/test_contracts_poc07.py`). The real
adapters bind the same suites in their own tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import chassis_contracts
import pytest
from chassis.ports.bundle import PortBundle
from chassis.profiles import PROFILE_DEFAULTS, REGISTRY

ROOT = Path(__file__).resolve().parents[3]
PORTS = {
    "auth": ("AuthPort", "AuthPortContract", "static"),
    "guardrails": ("GuardrailPort", "GuardrailPortContract", "fake"),
    "evaluator": ("EvaluatorPort", "EvaluatorPortContract", "fake"),
    "feedback": ("FeedbackPort", "FeedbackPortContract", "memory"),
}


@pytest.mark.parametrize("port", sorted(PORTS))
def test_each_new_port_has_a_default_a_fake_and_a_suite(port: str) -> None:
    protocol, suite, fake = PORTS[port]
    assert protocol in PortBundle.__annotations__[port]
    assert {"none", fake} <= set(REGISTRY[port])
    assert hasattr(chassis_contracts, suite)
    assert PROFILE_DEFAULTS["fake"].model_dump()[port] == "none"


def test_each_fake_is_bound_to_its_suite() -> None:
    tree = ast.parse((ROOT / "packages/chassis/tests/test_contracts_poc07.py").read_text())
    bases = {
        base.id
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test")
        for base in node.bases
        if isinstance(base, ast.Name)
    }
    assert {suite for _, suite, _ in PORTS.values()} <= bases
