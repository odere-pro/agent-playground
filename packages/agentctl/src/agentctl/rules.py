"""What `agentctl new` accepts: names, kinds, frameworks, lanes, trust, and the key's routes.

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
    "langchain",
    "openai-agents",
    "smolagents",
    "claude-agent",
)
CONNECTORS: tuple[str, ...] = ("sidecar", "remote")
DEFAULT_FRAMEWORK = "langgraph"
"""ADR-006 decision 1, amended 2026-11-06: the default engine for new agents."""
DEFAULT_CONNECTOR = "sidecar"
"""ADR-001 item 4: the default lane."""
UNTRUSTED: frozenset[str] = frozenset({"smolagents", "claude-agent"})
"""ADR-006 decision 2: the model's code or shell runs in the workload, so `remote` only."""
TRUSTS: tuple[Trust, ...] = ("trusted", "untrusted")
"""suggested: the `--trust` values (P-15, decided 2026-11-13); the chassis's `spec.trust` values."""

GENERATED_ROUTES: tuple[str, ...] = ("big-default",)
"""Every model route the template's keyed configs name (`config/agent.yaml` and the chart's
chassis config). The `fake` profile's route is left out: it calls no key."""
DEFAULT_KEY_ROUTES: tuple[str, ...] = GENERATED_ROUTES
"""suggested: the routes the service's LiteLLM key lists when `--key-route` is not given.
agentctl has no offline source for the real key (it is minted at deploy, `seed.sh`), so the
default is the route the template already names; `--key-route` states the key's real list."""

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


def resolve_trust(framework: str, trust: str | None) -> Trust:
    """`--trust` may lower trust, never raise it: an untrusted engine stays untrusted (ADR-006).
    No `--trust` means the framework decides."""
    if trust is None:
        return trust_of(framework)
    check_choice(trust, TRUSTS, "trust")
    if trust == "trusted" and trust_of(framework) == "untrusted":
        raise UsageError(
            f"framework {framework!r} is untrusted (the model's code or shell runs in the "
            "workload); --trust trusted cannot change that (ADR-006 decision 2)"
        )
    return "untrusted" if trust == "untrusted" else "trusted"


def default_connector(trust: str | None) -> str:
    """No `--connector`: an untrusted agent (pass the resolved trust) gets `remote`; otherwise
    the default lane. The same rule as `connector`'s default in `copier.yml`."""
    return "remote" if trust == "untrusted" else DEFAULT_CONNECTOR


def check_lane(framework: str, connector: str, trust: str | None = None) -> None:
    """The trust rule. Untrusted engines run only in `remote`; trusted engines in either lane."""
    check_choice(framework, FRAMEWORKS, "framework")
    check_choice(connector, CONNECTORS, "connector")
    if resolve_trust(framework, trust) == "trusted" or connector == "remote":
        return
    if trust == "untrusted":
        raise UsageError(
            f"--trust untrusted runs only with --connector remote, not {connector!r}: an "
            "untrusted workload never shares the chassis's pod (spec.trust, ADR-001 item 5); "
            "nothing was written"
        )
    raise UsageError(
        f"framework {framework!r} is untrusted (the model's code or shell runs in the "
        f"workload), so it runs only with --connector remote, not {connector!r} "
        "(spec.trust, ADR-001 item 5, ADR-006)"
    )


def check_routes(generated: tuple[str, ...], key_routes: tuple[str, ...]) -> None:
    """Refuse, never rewrite, a generated route the service's key does not list (012 H-3)."""
    if not key_routes:
        raise UsageError("--key-route: the service's key must list at least one route")
    missing = [route for route in generated if route not in key_routes]
    if missing:
        raise UsageError(
            f"the generated config names route(s) {', '.join(map(repr, missing))}, which the "
            f"service's key does not list ({', '.join(key_routes)}); add the route to the key "
            "and pass it with --key-route, or change the route after scaffolding "
            "(a call outside the key's list answers 403 model_route_denied); nothing was written"
        )
