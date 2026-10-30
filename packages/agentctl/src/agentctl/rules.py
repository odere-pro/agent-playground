"""What `agentctl new` accepts: names, kinds, frameworks, lanes, and the trust rule.

The trust rule mirrors the chassis config loader (`ChassisConfig._lane_fits_profile` in
`packages/chassis/src/chassis/server/config.py`): `spec.trust: untrusted` needs
`spec.engine.connector: remote`; a trusted engine may use either lane. agentctl does not import
the chassis, so a test checks the two agree. ADR-006 names which engine is untrusted.
"""

from __future__ import annotations

import keyword
import re
from typing import Literal

Trust = Literal["trusted", "untrusted"]

KINDS: tuple[str, ...] = ("transformer", "evaluator", "tool")
FRAMEWORKS: tuple[str, ...] = (
    "python",
    "pydanticai",
    "langgraph",
    "openai-agents",
    "smolagents",
    "claude-agent",
)
CONNECTORS: tuple[str, ...] = ("sidecar", "remote")
DEFAULT_FRAMEWORK = "pydanticai"
"""ADR-006 decision 1: the default engine for new agents."""
DEFAULT_CONNECTOR = "sidecar"
"""ADR-001 item 4: the default lane."""
UNTRUSTED: frozenset[str] = frozenset({"smolagents", "claude-agent"})
"""ADR-006 decision 2: the model's code or shell runs in the workload, so `remote` only."""

NAME_MAX = 63
"""A DNS label: the agent name is also the image, Service, and Helm release name."""
_NAME = re.compile(r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")
RESERVED: frozenset[str] = frozenset(
    {
        # The module would shadow a dependency of the rendered agent.
        "agents",
        "agent_testkit",
        "chassis",
        "chassis_contracts",
        "claude_agent_sdk",
        "fake_model_server",
        "httpx",
        "httpx2",
        "langchain",
        "langgraph",
        "mcp",
        "openai",
        "pydantic",
        "pydantic_ai",
        "smolagents",
        "workload_a2a",
        # pytest would confuse the package with the test folder.
        "test",
        "tests",
    }
)


class UsageError(ValueError):
    """A bad argument. The CLI prints it and exits 2."""


def package_name(agent_name: str) -> str:
    """The Python package of an agent: dashes become underscores."""
    return agent_name.replace("-", "_")


def check_name(name: str) -> str:
    if len(name) > NAME_MAX:
        raise UsageError(f"name {name!r} is {len(name)} characters; the limit is {NAME_MAX}")
    if not _NAME.match(name):
        raise UsageError(
            f"name {name!r} is not a DNS label: use lower case letters, digits, and dashes, "
            "start with a letter, and do not end with a dash (for example 'invoice-reader')"
        )
    package = package_name(name)
    if keyword.iskeyword(package) or package in RESERVED:
        raise UsageError(
            f"name {name!r} gives the Python package {package!r}, which is reserved; "
            "pick another name"
        )
    return name


def trust_of(framework: str) -> Trust:
    return "untrusted" if framework in UNTRUSTED else "trusted"


def check_choice(value: str, choices: tuple[str, ...], what: str) -> str:
    if value not in choices:
        raise UsageError(f"unknown {what} {value!r}; one of {', '.join(choices)}")
    return value


def check_lane(framework: str, connector: str) -> None:
    """The trust rule. Untrusted engines run only in `remote`; trusted engines in either lane."""
    check_choice(framework, FRAMEWORKS, "framework")
    check_choice(connector, CONNECTORS, "connector")
    if trust_of(framework) == "untrusted" and connector != "remote":
        raise UsageError(
            f"framework {framework!r} is untrusted (the model's code or shell runs in the "
            f"workload), so it runs only with --connector remote, not {connector!r} "
            "(spec.trust, ADR-001 item 5, ADR-006)"
        )
