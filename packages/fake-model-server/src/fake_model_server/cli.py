"""`fake-model-server --script scripts/example.yaml --port 8081`"""

from __future__ import annotations

import argparse

import uvicorn

from fake_model_server.app import create_app
from fake_model_server.script import Script


def main() -> None:
    parser = argparse.ArgumentParser(description="Scripted OpenAI-compatible model server")
    parser.add_argument("--script", required=True, help="YAML script file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    uvicorn.run(create_app(Script.from_yaml(args.script)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
