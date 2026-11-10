"""Runs in the chassis's own environment, as a script:
`python -I chassis_serve.py --uds PATH <chassis serve flags>`. It lives in `data/`, not in the
`agent_testkit` package: the kit never imports it, and `-I` keeps the script's folder off
`sys.path`. The kit passes the file's path to the chassis's interpreter
(`agent_testkit.processes`). It is the only file in the kit that imports `chassis`.

It is `chassis serve` with one addition: `--uds PATH` puts the public listener (`/health`,
`/ready`, `/v1/run`) on a Unix socket, because the offline gate (pytest-socket) refuses TCP in the
test process and allows Unix sockets. The proxy listener stays on loopback TCP: only the workload
process calls it, and pytest-socket does not reach a child process. Without `--uds` it is exactly
`chassis serve`. The ask to add `--uds` to `chassis serve` itself is in
`pocs/poc-09-agent-mvp-template/notes/2026-10-29-testkit-asks.md`; when it lands, this file goes.

`--model-script PATH` (015 H-8): a JSON list of `ScriptRule` objects, so a test scripts the fake
model's answer per case. The `fake` model adapter becomes `ScriptedModel(rules)`. A key that is
not a `ScriptRule` field stops the start, so a typo never scripts nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from chassis.server import cli
from chassis.server.lifecycle import DrainSettings, ProxyBindFailed


def _split(argv: Sequence[str], flag: str) -> tuple[str | None, list[str]]:
    """The value of `flag` and `argv` without the flag and its value."""
    rest = list(argv)
    if flag not in rest:
        return None, rest
    at = rest.index(flag)
    if at + 1 >= len(rest):
        raise SystemExit(f"chassis_serve: {flag} needs a path")
    value = rest[at + 1]
    del rest[at : at + 2]
    return value, rest


def _use_model_script(path: str) -> None:
    """Make the `fake` model adapter answer from the rules in the JSON file at `path`."""
    from chassis import profiles
    from chassis.fakes.model import ScriptedModel, ScriptRule

    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, list) or not all(isinstance(rule, dict) for rule in raw):
        raise SystemExit("chassis_serve: --model-script holds a JSON list of objects")
    fields = set(ScriptRule.model_fields)
    for index, rule in enumerate(raw):
        unknown = sorted(set(rule) - fields)
        if unknown:
            raise SystemExit(
                f"chassis_serve: model script rule {index} has unknown keys {unknown};"
                f" a rule takes {sorted(fields)}"
            )
    rules = [ScriptRule.model_validate(rule) for rule in raw]

    def scripted(agent: str | None = None) -> object:
        return ScriptedModel(rules)

    profiles.REGISTRY["model"]["fake"] = scripted


def main(argv: Sequence[str]) -> None:
    script, rest = _split(argv, "--model-script")
    if script is not None:
        _use_model_script(script)
    uds, rest = _split(rest, "--uds")
    if uds is None:
        cli.main(rest)
        return
    args = cli.parse_args(rest)
    servers = cli.build_all(args)
    servers.public.config.uds = uds  # uvicorn binds `uds` instead of host and port
    settings = DrainSettings(delay_s=args.drain_delay_s, timeout_s=args.drain_timeout_s)
    try:
        asyncio.run(cli.serve(servers.public, servers.proxy, settings, servers.remote))
    except ProxyBindFailed as exc:
        print(f"chassis: {exc}", file=sys.stderr)
        sys.exit(cli.STARTUP_FAILURE)
    if not servers.public.started or servers.startup.failed is not None:
        sys.exit(cli.STARTUP_FAILURE)


if __name__ == "__main__":
    main(sys.argv[1:])
