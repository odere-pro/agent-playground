"""H-14 (issue 008): the agent classes, the kind-to-class mapping, and the B.5 module table."""

from __future__ import annotations

from typing import Any

import pytest
from chassis.agent_class import (
    CLASSES,
    KIND_CLASS,
    KINDS,
    MODULE_TABLE,
    check_agent_class,
    module_rule,
)
from chassis.server.config import load_config, switched_on_modules

# The B.5 table of the epic, one row per module, columns stateless, orchestrator, data.
B5 = {
    "standard_interface": ("always", "always", "always"),
    "harness": ("always", "always", "always"),
    "model_port": ("always", "always", "optional"),
    "evaluator_gate": ("optional", "optional", "no"),
    "tool_port": ("tool_only", "optional", "no"),
    "result_events": ("optional", "optional", "no"),
    "own_store": ("never", "never", "always"),
    "routing_rules": ("no", "always", "no"),
    "workflow_engine": ("no", "always", "no"),
    "registry_search": ("no", "always", "no"),
    "long_term_memory": ("no", "optional", "no"),
    "a2a": ("optional", "optional", "optional"),
}


def test_the_table_has_every_b5_row_and_nothing_else() -> None:
    assert set(MODULE_TABLE) == set(B5)
    assert CLASSES == ("stateless", "orchestrator", "data")


@pytest.mark.parametrize("module", sorted(B5))
@pytest.mark.parametrize("index", range(3))
def test_every_b5_cell_is_the_rule(module: str, index: int) -> None:
    assert module_rule(module, CLASSES[index]) == B5[module][index]


def test_kind_maps_to_its_class() -> None:
    assert KINDS == ("transformer", "evaluator", "tool", "orchestrator", "data")
    assert KIND_CLASS == {
        "transformer": "stateless",
        "evaluator": "stateless",
        "tool": "stateless",
        "orchestrator": "orchestrator",
        "data": "data",
    }


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_with_its_own_class_passes(kind: str) -> None:
    check_agent_class(KIND_CLASS[kind], kind, frozenset())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("agent_class", "kind"),
    [("data", "tool"), ("stateless", "orchestrator"), ("orchestrator", "transformer")],
)
def test_a_kind_that_does_not_match_its_class_is_refused(agent_class: str, kind: str) -> None:
    with pytest.raises(ValueError, match=rf"spec.kind: {kind} needs spec.class: "):
        check_agent_class(agent_class, kind, frozenset())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("agent_class", "kind", "module"),
    [
        ("data", "data", "evaluator_gate"),
        ("data", "data", "result_events"),
        ("data", "data", "tool_port"),
        ("stateless", "transformer", "own_store"),
        ("orchestrator", "orchestrator", "own_store"),
        ("stateless", "transformer", "tool_port"),
        ("stateless", "evaluator", "long_term_memory"),
    ],
)
def test_a_module_the_table_refuses_fails(agent_class: str, kind: str, module: str) -> None:
    with pytest.raises(ValueError, match=rf"module {module} is not allowed for spec.class"):
        check_agent_class(agent_class, kind, frozenset({module}))  # type: ignore[arg-type]


def test_the_tool_port_is_allowed_for_a_tool_agent_only() -> None:
    check_agent_class("stateless", "tool", frozenset({"tool_port"}))


def test_an_unknown_module_is_refused() -> None:
    with pytest.raises(ValueError, match=r"unknown module"):
        check_agent_class("stateless", "transformer", frozenset({"telepathy"}))


def _doc(**spec: Any) -> dict[str, Any]:
    return {"profile": "fake", "agent": {"name": "echo", "version": "1"}, "spec": spec}


def test_spec_class_and_kind_are_typed_fields() -> None:
    config = load_config(_doc(**{"class": "data", "kind": "data"}))
    assert config.spec.agent_class == "data"
    assert config.spec.kind == "data"


def test_a_config_without_class_or_kind_still_loads() -> None:
    config = load_config(_doc())
    assert config.spec.agent_class is None
    assert config.spec.kind is None


def test_the_config_refuses_a_kind_that_does_not_match_its_class() -> None:
    with pytest.raises(ValueError, match=r"spec.kind: tool needs spec.class: stateless"):
        load_config(_doc(**{"class": "data", "kind": "tool"}))


@pytest.mark.parametrize(
    "flags",
    [{"harness": {"evaluator_gate": True}}, {"events": {"result_events": True}}],
)
def test_the_config_refuses_a_flag_the_table_marks_no_for_data(flags: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"is not allowed for spec.class: data"):
        load_config(_doc(**{"class": "data", "kind": "data"}, **flags))


def test_the_config_allows_the_optional_flags_for_stateless() -> None:
    load_config(
        _doc(
            **{"class": "stateless", "kind": "evaluator"},
            harness={"evaluator_gate": True},
            events={"result_events": True},
        )
    )


def test_class_without_kind_is_refused() -> None:
    with pytest.raises(ValueError, match=r"spec.class and spec.kind are set together"):
        load_config(_doc(**{"class": "stateless"}))


def test_flags_off_switch_on_no_optional_module() -> None:
    config = load_config(_doc(**{"class": "data", "kind": "data"}))
    assert switched_on_modules(config.spec) == frozenset()
