"""`agentctl new <name> --kind ... [--framework ...] [--trust ...] [--connector ...]
[--key-route ROUTE ... | --key-manifest PATH] [--dest DIR]`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agentctl import rules
from agentctl.manifest import read_key_manifest
from agentctl.scaffold import Answers, find_platform, new_agent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentctl", description="The PoC-9 agent factory.")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="scaffold a new agent from the service template")
    new.add_argument("name", help="the agent name, a DNS label, e.g. invoice-reader")
    new.add_argument("--kind", required=True, choices=rules.KINDS)
    new.add_argument("--framework", default=rules.DEFAULT_FRAMEWORK, choices=rules.FRAMEWORKS)
    new.add_argument(
        "--trust",
        choices=rules.TRUSTS,
        help="spec.trust (default: the framework's); untrusted needs the remote lane",
    )
    new.add_argument(
        "--connector",
        choices=rules.CONNECTORS,
        help="the lane (default: sidecar, or remote when the agent is untrusted); "
        "smolagents and claude-agent need remote",
    )
    key = new.add_mutually_exclusive_group()
    key.add_argument(
        "--key-route",
        dest="key_routes",
        action="append",
        metavar="ROUTE",
        help="a route the service's LiteLLM key lists; repeat for each "
        f"(default: {', '.join(rules.DEFAULT_KEY_ROUTES)}). A generated route the key does not "
        "list is refused",
    )
    key.add_argument(
        "--key-manifest",
        type=Path,
        metavar="PATH",
        help="the key manifest the deploy seed wrote (svc-<svc>.json); its routes are the key's "
        "list, in place of --key-route",
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


def _answers(args: argparse.Namespace, platform: Path) -> Answers:
    """No `--connector`: the lane follows the resolved trust, as `copier.yml` does. Resolving it
    raises `UsageError` for `--trust trusted` on an untrusted framework."""
    connector = args.connector or rules.default_connector(
        rules.resolve_trust(args.framework, args.trust)
    )
    key_routes = tuple(args.key_routes or rules.DEFAULT_KEY_ROUTES)
    key_source = "the service's key"
    if args.key_manifest is not None:
        # The alias is checked against `manifest.ALIAS`, so it is safe to print; the routes are not
        # printed at all.
        manifest = read_key_manifest(args.key_manifest)
        key_routes = manifest.routes
        key_source = f"the key {manifest.key_alias} (--key-manifest {args.key_manifest})"
    return Answers(
        agent_name=args.name,
        kind=args.kind,
        framework=args.framework,
        connector=connector,
        platform_path=platform.resolve() if platform.is_absolute() else platform,
        trust=args.trust,
        key_routes=key_routes,
        key_source=key_source,
        echo_key_routes=args.key_manifest is None,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    platform = args.platform_path or find_platform(Path(__file__).resolve(), Path.cwd())
    if platform is None:
        print("agentctl: no agent-platform checkout found; pass --platform-path", file=sys.stderr)
        return 2
    try:
        answers = _answers(args, platform)
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
