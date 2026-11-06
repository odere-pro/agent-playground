"""`guardrail-server --port 8095 --classifier prompt-guard|pattern [--token-file PATH]`

`prompt-guard` loads Llama Prompt Guard 2 (needs `guardrail-server[promptguard]` and a Hugging Face
token with access to the gated model, read by transformers from its own environment). `pattern` is
the offline stand-in and says so in `/health`.

`--token-file` (default: `GUARDRAIL_TOKEN_FILE`) holds the credential both POST routes need, as
`x-api-key` or `Authorization: Bearer`. LiteLLM sends it as `x-api-key` from the guardrail's
`api_key` param. Without a token file the server starts only on loopback, and warns that auth is
off. `resolve_token` makes these checks before any server starts.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import uvicorn

from guardrail_server.app import THRESHOLD, create_app
from guardrail_server.classifier import (
    PROMPT_GUARD_MODEL,
    Classifier,
    PatternClassifier,
    PromptGuardClassifier,
)

__all__ = [
    "TOKEN_FILE_ENV",
    "StartupError",
    "build_classifier",
    "is_loopback",
    "main",
    "parse_args",
    "resolve_token",
]

log = logging.getLogger("guardrail_server")

TOKEN_FILE_ENV = "GUARDRAIL_TOKEN_FILE"
LOOPBACK_NAMES = frozenset({"localhost"})


class StartupError(Exception):
    """The server must not start. The message names a path or a flag, never a token."""


def parse_args(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> argparse.Namespace:
    """The CLI args. `env` (default: the process environment) gives `--token-file` its default."""
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        description="LiteLLM generic guardrail API: injection, secrets"
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--classifier", choices=("prompt-guard", "pattern"), default="prompt-guard")
    parser.add_argument("--model", default=PROMPT_GUARD_MODEL)
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    parser.add_argument(
        "--token-file",
        default=env.get(TOKEN_FILE_ENV) or None,
        help=f"file holding the credential the POST routes need (default: ${TOKEN_FILE_ENV})",
    )
    return parser.parse_args(argv)


def is_loopback(host: str) -> bool:
    """True for `localhost` and a loopback address (127.0.0.0/8, ::1)."""
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _read_token(path: str) -> str:
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        # The type only: an error message is not shown, so no content can be in it.
        raise StartupError(f"cannot read the token file {path} ({type(exc).__name__})") from None
    if not token:
        raise StartupError(f"the token file {path} is empty")
    return token


def resolve_token(args: argparse.Namespace) -> str | None:
    """The token from `--token-file`, or None on loopback with a warning. `StartupError` for an
    empty or unreadable file, or for no token on a host that is not loopback."""
    if args.token_file:
        return _read_token(args.token_file)
    if not is_loopback(args.host):
        raise StartupError(
            f"no token: --host {args.host} is not loopback, so pass --token-file "
            f"(or set {TOKEN_FILE_ENV}); the check must not be open to the network"
        )
    log.warning("auth is off: no --token-file, serving on loopback (%s) only", args.host)
    return None


def build_classifier(args: argparse.Namespace) -> Classifier:
    if args.classifier == "pattern":
        return PatternClassifier()
    return PromptGuardClassifier(args.model)  # pragma: no cover - needs the gated model


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        token = resolve_token(args)
    except StartupError as exc:
        sys.exit(f"guardrail-server: {exc}")
    app = create_app(build_classifier(args), threshold=args.threshold, token=token)
    uvicorn.run(app, host=args.host, port=args.port)  # pragma: no cover - starts a server


if __name__ == "__main__":  # pragma: no cover
    main()
