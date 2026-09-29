"""`chassis serve --config configs/fake.yaml --port 8080`."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from chassis.server.app import create_app
from chassis.server.config import load_config


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chassis", description="The service chassis")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the chassis from a config file")
    serve.add_argument("--config", required=True, help="YAML config file")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if args.command == "serve":
        uvicorn.run(create_app(load_config(args.config)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
