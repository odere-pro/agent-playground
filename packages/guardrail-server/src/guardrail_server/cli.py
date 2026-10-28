"""`guardrail-server --port 8095 --classifier prompt-guard|pattern`

`prompt-guard` loads Llama Prompt Guard 2 (needs `guardrail-server[promptguard]` and a Hugging Face
token with access to the gated model, read by transformers from its own environment). `pattern` is
the offline stand-in and says so in `/health`.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from guardrail_server.app import THRESHOLD, create_app
from guardrail_server.classifier import (
    PROMPT_GUARD_MODEL,
    Classifier,
    PatternClassifier,
    PromptGuardClassifier,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LiteLLM generic guardrail API: injection, secrets"
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--classifier", choices=("prompt-guard", "pattern"), default="prompt-guard")
    parser.add_argument("--model", default=PROMPT_GUARD_MODEL)
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    return parser.parse_args(argv)


def build_classifier(args: argparse.Namespace) -> Classifier:
    if args.classifier == "pattern":
        return PatternClassifier()
    return PromptGuardClassifier(args.model)  # pragma: no cover - needs the gated model


def main(argv: Sequence[str] | None = None) -> None:  # pragma: no cover - starts a server
    args = parse_args(argv)
    app = create_app(build_classifier(args), threshold=args.threshold)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
