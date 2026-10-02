"""Locust user for the PoC-4 load matrix: native `POST /v1/run`, complete mode (plan, section 9).

Locust is not a workspace dependency. Run it through uv, pinned here and in `run_matrix.py`:

    uv run --no-project --with "locust==2.46.6" locust \
        -f pocs/poc-04-stateless-scalable/load/locustfile.py \
        --host http://127.0.0.1:18080 --headless -u 64 -r 64 --run-time 60s

Environment:

- `LOAD_KEY_SHARE` (suggested default 1.0): the share of calls, 0 to 1, that carry a fresh
  `Idempotency-Key` header. A keyed call takes the realistic path, with Valkey in it.
- `LOAD_NO_KEY=1`: no call carries a key (the same as `LOAD_KEY_SHARE=0`), for the baseline that
  prices idempotency.
- `LOAD_TEXT`: the fixed short input (suggested default below).

A call fails on any status other than 200 and on an envelope with `status: error`.
"""

from __future__ import annotations

import os
import random
import uuid
from typing import Any

from locust import (  # type: ignore[import-not-found]
    FastHttpUser,
    constant,
    constant_throughput,
    task,
)

LOAD_TEXT = os.environ.get("LOAD_TEXT", "Simplify: the cat sat on the mat.")


def key_share() -> float:
    """The share of keyed calls, from the environment, clamped to 0..1."""
    if os.environ.get("LOAD_NO_KEY") == "1":
        return 0.0
    try:
        share = float(os.environ.get("LOAD_KEY_SHARE", "1.0"))
    except ValueError:
        share = 1.0
    return min(max(share, 0.0), 1.0)


KEY_SHARE = key_share()


class RunUser(FastHttpUser):  # type: ignore[misc]
    """One complete call after another, with no think time."""

    # LOAD_RATE_PER_USER (calls per second per user) gives a fixed rate, for the `rate` scenarios;
    # unset, each user sends one call after another.
    _rate = float(os.environ.get("LOAD_RATE_PER_USER", "0") or 0)
    wait_time = constant_throughput(_rate) if _rate > 0 else constant(0)

    @task  # type: ignore[untyped-decorator]
    def post_run(self) -> None:
        headers: dict[str, str] = {}
        name = "/v1/run (no key)"
        if KEY_SHARE >= 1.0 or (KEY_SHARE > 0.0 and random.random() < KEY_SHARE):
            headers["Idempotency-Key"] = f"load-{uuid.uuid4().hex}"
            name = "/v1/run (key)"
        with self.client.post(
            "/v1/run",
            json={"input": {"text": LOAD_TEXT}},
            headers=headers,
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                envelope: Any = response.json()
            except ValueError:
                response.failure("body is not JSON")
                return
            status = envelope.get("status") if isinstance(envelope, dict) else None
            if status not in ("ok", "retry", "fallback"):
                response.failure(f"envelope status {status!r}")
