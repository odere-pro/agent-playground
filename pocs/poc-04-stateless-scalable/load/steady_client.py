"""Steady load on native `POST /v1/run` for the PoC-4 drills (plan, sections 8b, 9, and 10).

    uv run python pocs/poc-04-stateless-scalable/load/steady_client.py \
        --url http://127.0.0.1:18080 --concurrency 20 --duration-s 60 --no-retry

`--concurrency` workers each send one complete call at a time until `--duration-s` passes. Every
call carries its own fresh `Idempotency-Key`. Two modes:

- `--no-retry` (the default): one attempt per call. Any failure counts. The kind rolling-restart
  drill and the graceful-stop drill use it and expect 0 failed calls.
- `--retry`: a failed attempt is sent again with the same key and the same input, up to
  `--max-attempts`, `--retry-delay-s` apart. The killed-replica drill uses it.

A call fails on a transport error, an HTTP status other than 200, or an envelope with `status:
error`. `ok`, `retry`, and `fallback` envelopes are successes. The totals go to stdout; the exit
code is 1 when any call failed. Plain httpx, no Locust, so it runs from the workspace as is.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:18080"
# suggested: above the default `budget.timeout_ms` (30 s), so the chassis times out first.
REQUEST_TIMEOUT_S = 35.0
# suggested: statuses worth a retry with the same key. 409 is `idempotency_in_progress`.
RETRY_STATUSES = frozenset({409, 429, 500, 502, 503, 504})
REPLAYED_HEADER = "idempotent-replayed"
MAX_LISTED_FAILURES = 20


@dataclass
class Call:
    """One logical call: one key, one input, one or more attempts."""

    key: str
    text: str
    attempts: int = 0
    ok: bool = False
    latency_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    response: dict[str, Any] | None = None


@dataclass
class Summary:
    """What a run sent and how it ended. `calls` is left out of `as_dict`."""

    url: str
    mode: str
    concurrency: int
    duration_s: float
    sent: int = 0
    ok: int = 0
    failed: int = 0
    attempts: int = 0
    retried: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    failures: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("calls")
        return data


def body_for(text: str) -> dict[str, Any]:
    return {"input": {"text": text}}


def percentile(values: Sequence[float], q: int) -> float | None:
    """The q-th percentile (1 to 99) of `values`, or None when there are fewer than two."""
    if len(values) < 2:
        return round(values[0], 2) if values else None
    return round(statistics.quantiles(values, n=100, method="inclusive")[q - 1], 2)


async def attempt(client: httpx.AsyncClient, call: Call) -> tuple[bool, str | None]:
    """Send `call` once. Returns (ok, error); error is None on success."""
    headers = {"Idempotency-Key": call.key}
    opened: list[bool] = []

    async def trace(event: str, info: dict[str, Any]) -> None:
        if event == "connection.connect_tcp.started":
            opened.append(True)

    try:
        response = await client.post(
            "/v1/run",
            json=body_for(call.text),
            headers=headers,
            extensions={"trace": trace},
        )
    except httpx.HTTPError as exc:
        # When and on which connection: the drills compare it with the pod's SIGTERM and exit.
        when = time.strftime("%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"
        conn = "new" if opened else "reused"
        return False, f"{type(exc).__name__}: {exc} [{when}, {conn} connection]"
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}: {response.text[:200]}"
    try:
        envelope = response.json()
    except ValueError:
        return False, "HTTP 200 with a body that is not JSON"
    status = envelope.get("status") if isinstance(envelope, dict) else None
    if status not in ("ok", "retry", "fallback"):
        return False, f"envelope status {status!r}: {json.dumps(envelope.get('output', {}))[:200]}"
    call.response = envelope
    return True, None


def retryable(error: str) -> bool:
    """Transport errors, the retry statuses, and an error envelope (errors are never cached)."""
    if not error.startswith("HTTP "):
        return True
    code = error[5:8]
    if not code.isdigit():
        return False
    return int(code) == 200 or int(code) in RETRY_STATUSES


async def run_call(
    client: httpx.AsyncClient, call: Call, *, max_attempts: int, retry_delay_s: float
) -> None:
    start = time.perf_counter()
    while call.attempts < max_attempts:
        call.attempts += 1
        ok, error = await attempt(client, call)
        if ok:
            call.ok = True
            break
        assert error is not None
        call.errors.append(error)
        if not retryable(error) or call.attempts >= max_attempts:
            break
        await asyncio.sleep(retry_delay_s)
    call.latency_ms = (time.perf_counter() - start) * 1000


async def run_steady(
    url: str,
    *,
    concurrency: int = 20,
    duration_s: float = 60.0,
    retry: bool = False,
    max_attempts: int = 10,
    retry_delay_s: float = 0.5,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Summary:
    """Run the load and return the summary with every call in it."""
    attempts = max_attempts if retry else 1
    summary = Summary(
        url=url,
        mode="retry" if retry else "no-retry",
        concurrency=concurrency,
        duration_s=duration_s,
    )
    deadline = time.monotonic() + duration_s
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(REQUEST_TIMEOUT_S, connect=5.0)

    async def worker(index: int, client: httpx.AsyncClient) -> None:
        n = 0
        while time.monotonic() < deadline:
            n += 1
            call = Call(key=f"steady-{uuid.uuid4().hex}", text=f"steady call {index}-{n}")
            summary.calls.append(call)
            await run_call(client, call, max_attempts=attempts, retry_delay_s=retry_delay_s)

    async with httpx.AsyncClient(
        base_url=url, limits=limits, timeout=timeout, transport=transport
    ) as client:
        await asyncio.gather(*(worker(i, client) for i in range(concurrency)))

    latencies = [c.latency_ms for c in summary.calls if c.ok]
    summary.sent = len(summary.calls)
    summary.ok = sum(1 for c in summary.calls if c.ok)
    summary.failed = summary.sent - summary.ok
    summary.attempts = sum(c.attempts for c in summary.calls)
    summary.retried = sum(1 for c in summary.calls if c.attempts > 1)
    summary.p50_ms = percentile(latencies, 50)
    summary.p95_ms = percentile(latencies, 95)
    summary.failures = [f"{c.key}: {c.errors[-1]}" for c in summary.calls if not c.ok and c.errors][
        :MAX_LISTED_FAILURES
    ]
    return summary


def print_summary(summary: Summary) -> None:
    print(
        f"steady_client: {summary.mode}, {summary.concurrency} workers, {summary.duration_s:g} s"
        f" -> sent {summary.sent}, ok {summary.ok}, failed {summary.failed},"
        f" attempts {summary.attempts}, retried {summary.retried},"
        f" p50 {summary.p50_ms} ms, p95 {summary.p95_ms} ms"
    )
    for line in summary.failures:
        print(f"  failed {line}")
    if summary.failed > len(summary.failures):
        print(f"  ... and {summary.failed - len(summary.failures)} more")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration-s", type=float, default=60.0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-retry", dest="retry", action="store_false", help="one attempt")
    mode.add_argument("--retry", dest="retry", action="store_true", help="resend, same key")
    parser.set_defaults(retry=False)
    parser.add_argument("--max-attempts", type=int, default=10, help="--retry only")
    parser.add_argument("--retry-delay-s", type=float, default=0.5, help="--retry only")
    parser.add_argument("--json", dest="json_path", help="also write the totals here as JSON")
    args = parser.parse_args(argv)
    if args.concurrency < 1 or args.duration_s <= 0 or args.max_attempts < 1:
        parser.error("--concurrency, --duration-s, and --max-attempts must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = asyncio.run(
        run_steady(
            args.url,
            concurrency=args.concurrency,
            duration_s=args.duration_s,
            retry=args.retry,
            max_attempts=args.max_attempts,
            retry_delay_s=args.retry_delay_s,
        )
    )
    print_summary(summary)
    if args.json_path:
        with Path(args.json_path).open("w", encoding="utf-8") as out:
            json.dump(summary.as_dict(), out, indent=2)
    return 1 if summary.failed or not summary.sent else 0


if __name__ == "__main__":
    sys.exit(main())
