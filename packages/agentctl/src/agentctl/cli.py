"""`agentctl new <name> --kind ... [--framework ...] [--connector ...] [--dest DIR]`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agentctl import rules
from agentctl.scaffold import Answers, find_platform, new_agent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentctl", description="The PoC-9 agent factory.")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="scaffold a new agent from the service template")
    new.add_argument("name", help="the agent name, a DNS label, e.g. invoice-reader")
    new.add_argument("--kind", required=True, choices=rules.KINDS)
    new.add_argument("--framework", default=rules.DEFAULT_FRAMEWORK, choices=rules.FRAMEWORKS)
    new.add_argument(
        "--connector",
        default=rules.DEFAULT_CONNECTOR,
        choices=rules.CONNECTORS,
        help="the lane; smolagents and claude-agent need remote",
    )
    new.add_argument("--dest", type=Path, default=Path.cwd(), help="parent folder (default: .)")
    new.add_argument(
        "--platform-path",
        type=Path,
        help="the agent-platform checkout (default: the one agentctl runs from)",
    )
    new.add_argument(
        "--no-lock",
        action="store_true",
        help="skip pinning to the platform's versions and writing uv.lock",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    platform = args.platform_path or find_platform(Path(__file__).resolve(), Path.cwd())
    if platform is None:
        print("agentctl: no agent-platform checkout found; pass --platform-path", file=sys.stderr)
        return 2
    answers = Answers(
        agent_name=args.name,
        kind=args.kind,
        framework=args.framework,
        connector=args.connector,
        platform_path=platform.resolve() if platform.is_absolute() else platform,
    )
    try:
        target = new_agent(answers, args.dest.resolve(), lock=not args.no_lock)
    except rules.UsageError as exc:
        print(f"agentctl: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"agentctl: {exc}", file=sys.stderr)
        return 1
    print(f"agentctl: created {answers.agent_name} at {target} (first commit on main)")
    print(f"next: cd {target} && make setup test run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
