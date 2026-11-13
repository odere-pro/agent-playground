"""H-14 (issue 008): the agent class, the kind, and the B.5 module table as rules.

Every class serves the same `handle`, envelope, and endpoints; only the core differs. This module
holds no I/O: the config check calls `check_agent_class` at start.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, get_args

AgentClass = Literal["stateless", "orchestrator", "data"]
"""`spec.class`."""
Kind = Literal["transformer", "evaluator", "tool", "orchestrator", "data"]
"""`spec.kind`: what `agentctl new --kind` renders."""
Rule = Literal["always", "optional", "tool_only", "no", "never"]
"""One B.5 cell. `tool_only` is "Tool agents only": optional for `kind: tool`, else refused."""

CLASSES: tuple[AgentClass, ...] = get_args(AgentClass)
KINDS: tuple[Kind, ...] = get_args(Kind)

KIND_CLASS: Mapping[Kind, AgentClass] = {
    "transformer": "stateless",
    "evaluator": "stateless",
    "tool": "stateless",
    "orchestrator": "orchestrator",
    "data": "data",
}
"""The fixed mapping of kind to class."""

MODULE_TABLE: Mapping[str, tuple[Rule, Rule, Rule]] = {
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
"""The epic's B.5 table, columns in `CLASSES` order (stateless, orchestrator, data)."""

REFUSED: frozenset[Rule] = frozenset({"no", "never"})


def module_rule(module: str, agent_class: AgentClass) -> Rule:
    """The B.5 cell for one module and class. An unknown module raises `ValueError`."""
    row = MODULE_TABLE.get(module)
    if row is None:
        raise ValueError(f"unknown module {module!r}; known: {', '.join(sorted(MODULE_TABLE))}")
    return row[CLASSES.index(agent_class)]


def _allowed(module: str, agent_class: AgentClass, kind: Kind) -> bool:
    rule = module_rule(module, agent_class)
    if rule == "tool_only":
        return kind == "tool"
    return rule not in REFUSED


def check_agent_class(agent_class: AgentClass, kind: Kind, modules: frozenset[str]) -> None:
    """Refuse a kind that does not match its class, or a switched-on module the table refuses."""
    expected = KIND_CLASS[kind]
    if expected != agent_class:
        raise ValueError(f"spec.kind: {kind} needs spec.class: {expected} (got {agent_class!r})")
    for module in sorted(modules):
        if not _allowed(module, agent_class, kind):
            raise ValueError(
                f"module {module} is not allowed for spec.class: {agent_class} "
                f"(spec.kind: {kind}; epic B.5)"
            )
