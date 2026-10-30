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
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

from chassis.server import cli
from chassis.server.lifecycle import DrainSettings, ProxyBindFailed


def _split_uds(argv: Sequence[str]) -> tuple[str | None, list[str]]:
    rest = list(argv)
    if "--uds" not in rest:
        return None, rest
    at = rest.index("--uds")
    if at + 1 >= len(rest):
        raise SystemExit("chassis_serve: --uds needs a path")
    uds = rest[at + 1]
    del rest[at : at + 2]
    return uds, rest


def main(argv: Sequence[str]) -> None:
    uds, rest = _split_uds(argv)
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
