"""The fake model's script for the PoC-8 LiteLLM drill. `stack.sh up` writes it:

    uv run --no-sync python deploy/compose/poc08-litellm/model_script.py <out.yaml>

The fake model server records request bodies but has no route to read them, so the script itself
reports what reached the model. Its rules match the last user message, first match wins:

1. any raw PII value of `PII_TEXT` (email, phone, card): `RAW_REPLY`. LiteLLM let it through.
2. the `<EMAIL_ADDRESS>` token Presidio's anonymizer writes: `MASKED_REPLY`.
3. `CONTROL_TEXT` whole: `CONTROL_REPLY`. Plain text passed unchanged.
4. the red-team triggers of the cases the drill runs (`REDTEAM_CASES`), from
   `chassis_contracts.redteam.live.model_script`.
5. `PROVIDER_FAULT_TEXT` (CH-16): a 503 when the request's `model` is `big-default`'s deployment
   (`fake-model`), and `FALLBACK_REPLY` when it is `local-small`'s (`fake-model-fallback`,
   litellm.yaml). LiteLLM retries the 503 `num_retries` times, then takes the fallback route; the
   drill sees the fallback's reply with one chassis model call.

Anything else gets `ok`. The drill (`pocs/poc-08-cross-cutting-build/tests/
test_poc08_stack_litellm.py`) loads this file by path for the same constants. The red-team
payloads hold made-up key-shaped values, so the output goes to the git-ignored `.run/` only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

EMAIL = "jane.doe@example.com"
PHONE = "415-555-0132"
CARD = "4111 1111 1111 1111"
"""A test card number: Luhn-valid, so Presidio's card recognizer scores it."""
PII_VALUES = (EMAIL, PHONE, CARD)
PII_TEXT = f"Contact Jane at {EMAIL} or {PHONE}. Her card is {CARD}."
CONTROL_TEXT = "Please make these steps shorter and plainer."
RAW_REPLY = "pii-seen-raw"
MASKED_REPLY = "pii-seen-masked"
CONTROL_REPLY = "control-seen-unchanged"
MASK_TOKEN = "<EMAIL_ADDRESS>"
PROVIDER_FAULT_TEXT = "Trigger a provider fault on the main route."
FALLBACK_REPLY = "answered-by-the-fallback-route"
PROVIDER_FAULT_STATUS = 503
MAIN_DEPLOYMENT = "fake-model"
FALLBACK_DEPLOYMENT = "fake-model-fallback"
"""The `model` LiteLLM sends the fake for `big-default` and for `local-small` (litellm.yaml:
`openai/<name>`, the prefix stripped)."""
REDTEAM_CASES = (
    "exfil-02-input-asks-post",
    "exfil-04-markdown-image",
    "secret-01-input-asks-for-key",
    "secret-03-answer-has-key",
    "secret-04-streamed-answer-has-key",
)
"""The PII and secret cases (categories `exfiltration`, `secret_read`) whose text passes the
chassis and LiteLLM's guardrail chain, Presidio included, on this stack. The `tool_output`,
`tool_args`, and `workload_env` cases need the red-team MCP server or are static; this stack runs
`tools: fake`, so they stay with `make redteam`."""


def pii_rules() -> list[dict[str, Any]]:
    raw = [{"match": value, "reply": RAW_REPLY} for value in PII_VALUES]
    return [
        *raw,
        {"match": MASK_TOKEN, "reply": MASKED_REPLY},
        {"match": CONTROL_TEXT, "reply": CONTROL_REPLY},
    ]


def fault_rules() -> list[dict[str, Any]]:
    """CH-16: the main deployment fails the provider-fault text; the fallback answers it."""
    return [
        {
            "match": PROVIDER_FAULT_TEXT,
            "model": MAIN_DEPLOYMENT,
            "error": {"status": PROVIDER_FAULT_STATUS, "message": "scripted provider fault"},
        },
        {"match": PROVIDER_FAULT_TEXT, "model": FALLBACK_DEPLOYMENT, "reply": FALLBACK_REPLY},
    ]


SWAP_TEXT = "Which deployment answers this route?"
"""G-1b (router-drill.sh swap): the fake names the deployment LiteLLM sent the call to."""


def swap_rules() -> list[dict[str, Any]]:
    """G-1b: each deployment answers `SWAP_TEXT` with its own name."""
    deployments = (MAIN_DEPLOYMENT, FALLBACK_DEPLOYMENT)
    return [{"match": SWAP_TEXT, "model": d, "reply": f"answered-by-{d}"} for d in deployments]


def script() -> dict[str, Any]:
    from chassis_contracts.redteam import load_cases
    from chassis_contracts.redteam.live import model_script

    cases = [c for c in load_cases() if c.id in REDTEAM_CASES]
    redteam = model_script(cases)
    return {
        "model": redteam["model"],
        "rules": [*fault_rules(), *swap_rules(), *pii_rules(), *redteam["rules"]],
        "default_reply": "ok",
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: model_script.py <out.yaml>", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.write_text(yaml.safe_dump(script(), sort_keys=False), encoding="utf-8")
    out.chmod(0o644)  # the fake model server runs as uid 10004; the file holds no secret
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
