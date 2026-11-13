"""Stock SDK clients in the workload container (CH-2, 013). live-drill.yaml mounts it read-only at
/opt/drill/sdk_clients.py; the live drill runs it with `docker compose exec -T workload python
/opt/drill/sdk_clients.py <text>` and reads one JSON object from stdout.

Each client is the stock SDK class, pointed at the chassis's model proxy on the workload's loopback
(`CHASSIS_MODEL_URL`, the only model setting a workload gets). Each asks once complete and once
streamed. The workload holds no key: the SDKs require an `api_key` argument, so they get a
placeholder, which the proxy never forwards (it adds the service's key itself).
"""

from __future__ import annotations

import json
import os
import sys

import anthropic
import openai

ROUTE = "big-default"
PLACEHOLDER = "no-key-in-the-workload"  # pragma: allowlist secret (not a key; the proxy drops it)
MAX_TOKENS = 64


def openai_answers(base: str, text: str) -> dict[str, object]:
    client = openai.OpenAI(base_url=base, api_key=PLACEHOLDER, max_retries=0)
    messages: list[openai.types.chat.ChatCompletionMessageParam] = [
        {"role": "user", "content": text}
    ]
    done = client.chat.completions.create(model=ROUTE, messages=messages, max_tokens=MAX_TOKENS)
    stream = client.chat.completions.create(
        model=ROUTE, messages=messages, max_tokens=MAX_TOKENS, stream=True
    )
    chunks = [c.choices[0].delta.content or "" for c in stream if c.choices]
    return {
        "sdk": f"openai {openai.__version__}",
        "complete": done.choices[0].message.content,
        "streamed": [c for c in chunks if c],
    }


def anthropic_answers(base: str, text: str) -> dict[str, object]:
    client = anthropic.Anthropic(base_url=base, api_key=PLACEHOLDER, max_retries=0)
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": text}]
    done = client.messages.create(model=ROUTE, messages=messages, max_tokens=MAX_TOKENS)
    with client.messages.stream(model=ROUTE, messages=messages, max_tokens=MAX_TOKENS) as stream:
        chunks = list(stream.text_stream)
    return {
        "sdk": f"anthropic {anthropic.__version__}",
        "complete": "".join(b.text for b in done.content if b.type == "text"),
        "streamed": [c for c in chunks if c],
    }


def main() -> None:
    text = sys.argv[1]
    model_url = os.environ["CHASSIS_MODEL_URL"]
    result = {
        "model_url": model_url,
        "key_like_env": sorted(k for k in os.environ if "KEY" in k or "TOKEN" in k),
        "openai": openai_answers(model_url, text),
        "anthropic": anthropic_answers(model_url.removesuffix("/v1"), text),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
