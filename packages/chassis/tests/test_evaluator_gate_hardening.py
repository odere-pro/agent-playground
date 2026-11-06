"""The evaluator gate, hardened (finding R7-17): a salted sample and `evaluator_on_error`.

Sampling hashes a per-process salt with the `request_id`, so a caller who picks its own ids cannot
pick ids that are never gated. `evaluator_on_error: fail_closed` ends a sync run whose evaluator
is down with one `error {code: evaluator_gate_failed, retryable: false}` (contract v6), and never
sends that attempt's answer. A retryable `evaluator_unavailable` code is left for the contract
owner.
"""

from __future__ import annotations

from chassis.core.envelope import Context, Request
from chassis.core.events import Error, Metrics, Start
from chassis.fakes import InMemoryTelemetry, ScoreRule
from chassis.server.config import RELOADABLE
from chassis.server.evaluator_gate import (
    GATE_FAILED,
    GATE_FAILED_CLOSED,
    GATE_FAILED_OPEN,
    GATE_FALLBACKS,
    GatedEngine,
    sampled,
)
from test_evaluator_gate import (
    BAD,
    GOOD,
    FailsOnRetry,
    RouteEngine,
    config,
    evaluator,
    ports,
    run,
    telemetry,
)

RATE = 0.1
FIXED_SALT = bytes(range(32))
NO_SALT = b""


def attack_ids(count: int = 100) -> list[str]:
    """Ids a caller would pick: each one is not sampled under the unsalted hash at `RATE`."""
    ids: list[str] = []
    n = 0
    while len(ids) < count:
        candidate = f"attacker-{n}"
        if not sampled(candidate, RATE, NO_SALT):
            ids.append(candidate)
        n += 1
    return ids


def test_caller_chosen_ids_are_sampled_at_the_rate_once_salted() -> None:
    """Spec R7-17: about 10 percent of 100 caller-chosen ids are scored, whatever the ids."""
    ids = attack_ids()
    assert not any(sampled(i, RATE, NO_SALT) for i in ids)  # the attack works unsalted

    hits = sum(sampled(i, RATE, FIXED_SALT) for i in ids)

    assert 1 <= hits <= 25, hits


def test_the_same_salt_gives_the_same_decision_for_the_same_id() -> None:
    ids = [f"req-{i}" for i in range(2000)]
    first = [sampled(i, 0.25, FIXED_SALT) for i in ids]
    assert first == [sampled(i, 0.25, FIXED_SALT) for i in ids]
    assert 0.2 < sum(first) / len(ids) < 0.3
    assert not any(sampled(i, 0.0, FIXED_SALT) for i in ids)
    assert all(sampled(i, 1.0, FIXED_SALT) for i in ids)


async def test_the_gate_scores_about_the_rate_of_caller_chosen_ids(
    request_: Request, context: Context
) -> None:
    """Through `GatedEngine.run`: the attack ids are gated at about the rate."""
    ev = evaluator()
    cfg = config(evaluator_sample_rate=RATE)
    gate = GatedEngine(
        RouteEngine(answers=[GOOD]),
        ev,
        config=lambda: cfg,
        telemetry=InMemoryTelemetry(),
        salt=FIXED_SALT,
    )
    for request_id in attack_ids():
        request = request_.model_copy(update={"request_id": request_id})
        _ = [e async for e in gate.run(request, context)]

    assert 1 <= len(ev.cases) <= 25, len(ev.cases)


async def test_each_gate_draws_its_own_secret_salt(request_: Request, context: Context) -> None:
    """Two gates built without a salt decide differently for the same ids."""
    cfg = config(evaluator_sample_rate=0.5)
    ids = [f"req-{i}" for i in range(200)]
    decisions = []
    for _ in range(2):
        ev = evaluator()
        gate = GatedEngine(
            RouteEngine(answers=[GOOD]), ev, config=lambda: cfg, telemetry=InMemoryTelemetry()
        )
        seen: list[bool] = []
        for request_id in ids:
            before = len(ev.cases)
            request = request_.model_copy(update={"request_id": request_id})
            _ = [e async for e in gate.run(request, context)]
            seen.append(len(ev.cases) > before)
        decisions.append(seen)

    assert decisions[0] != decisions[1]


async def test_an_evaluator_outage_fails_closed_when_set() -> None:
    engine, ev = RouteEngine(), evaluator()
    ev.unavailable = True
    bundle = ports(engine, ev)

    res, _ = await run(config(evaluator_on_error="fail_closed"), bundle)

    body = res.json()
    assert body["status"] == "error", body
    assert body["output"]["error"]["code"] == GATE_FAILED
    assert BAD not in res.text  # the attempt's events never left
    assert engine.routes == ["big-default"]
    t = telemetry(bundle)
    assert t.counter_value(GATE_FAILED_CLOSED) == 1
    assert t.counter_value(GATE_FAILED_OPEN) == 0


async def test_an_outage_on_the_retry_fails_closed_without_the_retry_answer() -> None:
    engine = RouteEngine(answers=[BAD, GOOD])
    ev = FailsOnRetry([ScoreRule(match=BAD, score=0.2)], default=0.9)
    bundle = ports(engine, ev)

    res, _ = await run(config(evaluator_on_error="fail_closed"), bundle)

    body = res.json()
    assert body["status"] == "error" and body["output"]["error"]["code"] == GATE_FAILED
    assert GOOD not in res.text and BAD not in res.text
    assert telemetry(bundle).counter_value(GATE_FAILED_CLOSED) == 1
    assert telemetry(bundle).counter_value(GATE_FALLBACKS) == 0


async def test_async_mode_ignores_fail_closed() -> None:
    """Async scoring never changes the answer, so `fail_closed` changes nothing there."""
    engine, ev = RouteEngine(), evaluator()
    ev.unavailable = True
    bundle = ports(engine, ev)

    res, _ = await run(config(evaluator_mode="async", evaluator_on_error="fail_closed"), bundle)

    body = res.json()
    assert body["status"] == "ok" and body["output"]["text"] == BAD
    assert telemetry(bundle).counter_value(GATE_FAILED_CLOSED) == 0


async def test_the_fail_closed_error_keeps_the_contract_code_and_follows_only_the_start(
    request_: Request, context: Context
) -> None:
    ev = evaluator()
    ev.unavailable = True
    cfg = config(evaluator_on_error="fail_closed")
    gate = GatedEngine(
        RouteEngine(), ev, config=lambda: cfg, telemetry=InMemoryTelemetry(), salt=FIXED_SALT
    )

    events = [e async for e in gate.run(request_, context)]

    assert [type(e) for e in events] == [Start, Metrics, Error]
    error = events[-1]
    assert isinstance(error, Error)
    assert error.code == GATE_FAILED and error.retryable is False  # contract v6 line 277


def test_evaluator_on_error_defaults_to_fail_open_and_reloads() -> None:
    assert config().spec.harness.evaluator_on_error == "fail_open"
    assert "spec.harness.evaluator_on_error" in RELOADABLE
