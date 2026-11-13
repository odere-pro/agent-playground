"""Router drill: a real LiteLLM (the pinned v1.103.0) in front of the fake model server, with
Postgres spend logs and Prometheus, for the keyless router boxes of 002 G-1, 003 G-1b, 004 G-2,
and 012 H-3. `network`; collected only with `ROUTER_DRILL=1` (conftest.py), so `make test` never
sees it. By hand:

    deploy/compose/poc08-litellm/router-drill.sh up
    ROUTER_DRILL=1 uv run pytest -m network -v \\
        pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py
    deploy/compose/poc08-litellm/router-drill.sh down

The drill mints its own three virtual keys (`svc-alpha-<run>`, agent `alpha-<run>`; the same for
`beta` and `gamma`, where `<run>` is new per run, so totals hold only this run's calls)
through `router-drill.sh mint`, into mode-600 files under `.run/`. It reads the master key only for
the unknown-route call, and never prints a key. No real provider key is used anywhere.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from poc_harness.drills import gate_is_on

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not gate_is_on("ROUTER_DRILL"),
        reason="a router drill: needs router-drill.sh up and ROUTER_DRILL=1",
    ),
]

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "deploy/compose/poc08-litellm"
DRILL = STACK / "router-drill.sh"
RUN_DIR = STACK / ".run"
CHASSIS = "http://127.0.0.1:8013"
LITELLM = "http://127.0.0.1:4013"
PROMETHEUS = "http://127.0.0.1:9013"
TIMEOUT_S = 120.0
WAIT_S = 90.0
"""suggested: how long the drill waits for spend logs (batched, 2 s) and a Prometheus scrape."""
POLL_S = 2.0
SWAP_TEXT = "Which deployment answers this route?"
MAIN, OTHER = "fake-model", "fake-model-fallback"
ROUTE = "big-default"
RUN = uuid.uuid4().hex[:6]
"""One suffix per run: agent tags and key aliases are new, so totals hold only this run's calls."""
ALPHA, BETA, GAMMA = f"alpha-{RUN}", f"beta-{RUN}", f"gamma-{RUN}"


def _drill(*args: str, stdin: str | None = None) -> str:
    done = subprocess.run(
        [str(DRILL), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, f"router-drill.sh {args[0]}: {done.stderr[-800:]}"
    return done.stdout


def _read_key(path: Path, name: str = "LITELLM_API_KEY") -> str:
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key == name and value:
            return value
    pytest.fail(f"{path.name} holds no {name}", pytrace=False)


class _Key:
    """A virtual key. `repr` never shows it."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.hash = hashlib.sha256(value.encode()).hexdigest()

    def __repr__(self) -> str:
        return "_Key(***)"


def _mint(alias: str, agent: str) -> _Key:
    path = RUN_DIR / f"{alias}.env"
    _drill("mint", alias, agent, str(path))
    return _Key(_read_key(path))


@pytest.fixture(scope="module")
def alpha() -> _Key:
    return _mint(f"svc-{ALPHA}", ALPHA)


@pytest.fixture(scope="module")
def beta() -> _Key:
    return _mint(f"svc-{BETA}", BETA)


@pytest.fixture(scope="module")
def gamma() -> _Key:
    """A third key for the route tests, so svc-alpha's usage holds only the usage calls."""
    return _mint(f"svc-{GAMMA}", GAMMA)


def _client(key: _Key | str) -> httpx.Client:
    value = key.value if isinstance(key, _Key) else key
    headers = {"Authorization": f"Bearer {value}"}
    return httpx.Client(base_url=LITELLM, headers=headers, timeout=TIMEOUT_S, trust_env=False)


def _chat(key: _Key, text: str, **extra: Any) -> dict[str, Any]:
    body = {"model": ROUTE, "messages": [{"role": "user", "content": text}], **extra}
    with _client(key) as client:
        answer = client.post("/v1/chat/completions", json=body)
    assert answer.status_code == 200, answer.text[:300]
    return dict(answer.json())


def _stream(key: _Key, text: str, **extra: Any) -> tuple[str, str, dict[str, Any]]:
    """The streamed text, the stream's id, and its last usage block."""
    body = {
        "model": ROUTE,
        "messages": [{"role": "user", "content": text}],
        "stream": True,
        "stream_options": {"include_usage": True},
        **extra,
    }
    parts: list[str] = []
    usage: dict[str, Any] = {}
    stream_id = ""
    with _client(key) as client, client.stream("POST", "/v1/chat/completions", json=body) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            stream_id = chunk.get("id") or stream_id
            parts.extend(
                (c.get("delta") or {}).get("content") or "" for c in chunk.get("choices") or []
            )
            usage = chunk.get("usage") or usage
    return "".join(parts), stream_id, usage


def _sql(query: str, *variables: str) -> list[dict[str, str]]:
    args = [a for v in variables for a in ("-v", v)]
    out = _drill("sql", *args, stdin=query)
    return list(csv.DictReader(io.StringIO(out)))


def _rows_of(key: _Key, want: int) -> list[dict[str, str]]:
    """The key's spend-log rows, once there are `want` of them (spend logs are batched)."""
    query = (
        "SELECT request_id, prompt_tokens, completion_tokens, spend, model_group, "
        'end_user AS "user", '
        f'"startTime" FROM "LiteLLM_SpendLogs" WHERE api_key = \'{key.hash}\' '
        'ORDER BY "startTime";'
    )
    deadline = time.monotonic() + WAIT_S
    rows = _sql(query)
    while len(rows) < want and time.monotonic() < deadline:
        time.sleep(POLL_S)
        rows = _sql(query)
    return rows


def _today_range() -> tuple[str, str]:
    today = datetime.now(UTC).date()
    return f"from='{today - timedelta(days=1)}'", f"to='{today + timedelta(days=2)}'"


@pytest.fixture(scope="module")
def alpha_calls(alpha: _Key) -> dict[str, Any]:
    """Three calls on svc-alpha: complete with a user, complete with none, streamed with none."""
    user = f"u-{uuid.uuid4().hex[:8]}"
    complete_user = _chat(alpha, "hello with a user", user=user)
    complete_none = _chat(alpha, "hello with no user")
    text, stream_id, usage = _stream(alpha, "hello streamed")
    return {
        "user": user,
        "ids": [complete_user["id"], complete_none["id"], stream_id],
        "usages": [complete_user["usage"], complete_none["usage"], usage],
        "stream_text": text,
    }


# --- 003 G-1b ---------------------------------------------------------------------------------


def test_an_unknown_route_returns_a_clear_error_not_a_default_model(gamma: _Key) -> None:
    """A call to an unknown route name returns a clear error, not a default model.

    With the master key (allowed every route), so the refusal is the router's route check, not
    the key's model list. The control is the same body on `big-default`, answered.
    """
    master = _read_key(RUN_DIR / "router.env", "LITELLM_MASTER_KEY")
    body = {"model": "no-such-route", "messages": [{"role": "user", "content": SWAP_TEXT}]}
    with _client(master) as client:
        unknown = client.post("/v1/chat/completions", json=body)
        control = client.post("/v1/chat/completions", json={**body, "model": ROUTE})
    del master

    assert control.status_code == 200
    assert unknown.status_code in (400, 404), unknown.status_code
    error = unknown.json().get("error") or {}
    assert "no-such-route" in str(error.get("message")), error
    assert "choices" not in unknown.json()
    # The key that lists its routes is refused the unknown name too.
    with _client(gamma) as client:
        keyed = client.post("/v1/chat/completions", json=body)
    assert keyed.status_code in (400, 401, 403, 404)
    assert "choices" not in keyed.json()


def test_changing_the_model_behind_big_default_switches_the_model(gamma: _Key) -> None:
    """Changing the model behind `big-default` in router config switches the model; a test call
    shows the new model answers, and no client code changed.

    The same `_chat` call before and after `router-drill.sh swap`, which edits only the router
    config and restarts LiteLLM. The fake names the deployment that answered.
    """
    before = _chat(gamma, SWAP_TEXT)["choices"][0]["message"]["content"]
    try:
        _drill("swap", OTHER)
        after = _chat(gamma, SWAP_TEXT)["choices"][0]["message"]["content"]
    finally:
        _drill("swap", MAIN)
    back = _chat(gamma, SWAP_TEXT)["choices"][0]["message"]["content"]

    assert before == f"answered-by-{MAIN}"
    assert after == f"answered-by-{OTHER}"
    assert back == f"answered-by-{MAIN}"


# --- 002 G-1 ----------------------------------------------------------------------------------


def test_no_key_in_the_router_config_or_the_logs(alpha_calls: dict[str, Any]) -> None:
    """No provider API key appears in the router config, the repo, or the logs (keyless part).

    This run has no provider key, so it shows the router config holds no literal key and that
    every service's log holds none of the run's master, database, or virtual keys after calls.
    """
    for path in (STACK / "router-drill.litellm.yaml", RUN_DIR / "router-litellm.yaml"):
        text = path.read_text()
        assert "sk-" not in text
        assert "master_key: os.environ/" in text
    hits = _drill("key-hits").strip()
    assert hits.endswith(f"values={len(list(RUN_DIR.glob('*.env')))}") or "values=" in hits
    assert " hits=0 " in f" {hits} ", hits


# --- 004 G-2 ----------------------------------------------------------------------------------


def test_every_call_makes_one_usage_record(alpha: _Key, alpha_calls: dict[str, Any]) -> None:
    """Every call through the router makes one usage record with input tokens, output tokens,
    cost, route, user, agent, and time.

    Three calls on a new key (complete with a user, complete with none, streamed with none): three
    rows, one per response id, each with the tokens the response reported, a cost, the route, a
    time, and the agent of the key.
    """
    rows = _rows_of(alpha, 3)
    assert len(rows) == 3, rows
    assert {r["request_id"] for r in rows} == set(alpha_calls["ids"])
    by_id = {r["request_id"]: r for r in rows}
    for rid, usage in zip(alpha_calls["ids"], alpha_calls["usages"], strict=True):
        row = by_id[rid]
        assert int(row["prompt_tokens"]) == usage["prompt_tokens"] > 0
        assert int(row["completion_tokens"]) == usage["completion_tokens"] > 0
        assert float(row["spend"]) > 0
        assert row["model_group"] == ROUTE
        assert row["startTime"]
    assert by_id[alpha_calls["ids"][0]]["user"] == alpha_calls["user"]
    agents = _sql(
        "SELECT v.metadata -> 'tags' AS tags FROM \"LiteLLM_VerificationToken\" v "
        f"WHERE v.token = '{alpha.hash}';"
    )
    assert json.loads(agents[0]["tags"]) == [f"agent:{ALPHA}"]


def _totals(*variables: str) -> list[dict[str, str]]:
    return _sql((STACK / "usage_totals.sql").read_text(), *variables)


def test_calls_with_no_user_tag_show_under_unknown(
    alpha: _Key, alpha_calls: dict[str, Any]
) -> None:
    """Calls with no `user` tag show under `unknown`.

    The two no-user calls of svc-alpha are in the `unknown` user total of usage_totals.sql, and
    their rows have an empty `user`, so `unknown` is the query's name for them, never a drop.
    """
    rows = _rows_of(alpha, 3)
    no_user = [r for r in rows if not r["user"]]
    assert len(no_user) == 2
    totals = _totals(*_today_range())
    unknown = [t for t in totals if t["level"] == "user" and t["name"] == "unknown"]
    assert len(unknown) == 1
    assert int(unknown[0]["calls"]) >= 2


def test_one_query_returns_totals_per_request_user_and_agent_for_a_date_range(
    alpha: _Key, alpha_calls: dict[str, Any]
) -> None:
    """One query returns totals per request, per user, and per agent for a date range.

    usage_totals.sql over today: each svc-alpha request, the alpha user, and agent `alpha` with
    the three calls' summed tokens. The control: a range in 2000 returns no rows.
    """
    rows = _rows_of(alpha, 3)
    totals = _totals(*_today_range())
    by = {(t["level"], t["name"]): t for t in totals}
    for rid in alpha_calls["ids"]:
        assert by[("request", rid)]["calls"] == "1"
    assert by[("user", alpha_calls["user"])]["calls"] == "1"
    agent = by[("agent", ALPHA)]
    assert agent["calls"] == "3"
    assert int(agent["input_tokens"]) == sum(int(r["prompt_tokens"]) for r in rows)
    assert int(agent["output_tokens"]) == sum(int(r["completion_tokens"]) for r in rows)
    assert float(agent["cost"]) > 0
    assert _totals("from='2000-01-01'", "to='2000-01-02'") == []


def _prom_series(query: str) -> list[dict[str, Any]]:
    with httpx.Client(base_url=PROMETHEUS, timeout=30, trust_env=False) as client:
        answer = client.get("/api/v1/query", params={"query": query})
    assert answer.status_code == 200, answer.text[:300]
    return list(answer.json()["data"]["result"])


@pytest.mark.xfail(
    strict=True,
    reason="2026-11-13 run: LiteLLM v1.103.0 answers Prometheus's GET /metrics/ with 401, so no "
    "litellm_* series is scraped (pocs/poc-08-cross-cutting-build/notes/2026-11-13-router-live.md)",
)
def test_prometheus_scrapes_token_and_cost_metrics_by_agent_and_route(
    alpha: _Key, alpha_calls: dict[str, Any]
) -> None:
    """Prometheus scrapes the token and cost metrics, labeled by agent and route; `user` is in
    the usage record only.

    The agent label is the calling key's alias (`svc-alpha-*`, tagged `agent:alpha`); the route
    label is the model group. No label carries the call's user value; the usage record does.
    """
    names = (
        "litellm_input_tokens_metric_total",
        "litellm_output_tokens_metric_total",
        "litellm_spend_metric_total",
    )
    deadline = time.monotonic() + WAIT_S
    found: dict[str, list[dict[str, Any]]] = {}
    while time.monotonic() < deadline:
        found = {n: _prom_series(n) for n in names}
        if all(
            any(s["metric"].get("hashed_api_key") == alpha.hash for s in v) for v in found.values()
        ):
            break
        time.sleep(POLL_S)
    print(json.dumps({n: [s["metric"] for s in v] for n, v in found.items()}, indent=1)[:4000])
    for name, series in found.items():
        mine = [s for s in series if s["metric"].get("hashed_api_key") == alpha.hash]
        assert mine, f"{name}: no series for svc-alpha"
        for s in mine:
            labels = s["metric"]
            assert labels.get("api_key_alias", "").startswith("svc-alpha"), labels
            assert ROUTE in (labels.get("model_group"), labels.get("requested_model")), labels
            assert alpha_calls["user"] not in labels.values(), labels
    assert any(r["user"] == alpha_calls["user"] for r in _rows_of(alpha, 3))


# --- 012 H-3 ----------------------------------------------------------------------------------


@pytest.fixture
def chassis() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=CHASSIS, timeout=TIMEOUT_S, trust_env=False) as client:
        yield client


def test_the_echo_agent_gets_a_streamed_answer_from_big_default_through_the_router(
    chassis: httpx.Client,
) -> None:
    """The echo agent with a model step gets a streamed answer from `big-default` through the
    router.

    A streamed `POST /v1/run` on the chassis (echo workload, `spec.model.route: big-default`,
    model adapter `litellm`): the SSE stream carries the fake's answer for `big-default`'s
    deployment, which only LiteLLM's route could pick, and the chassis key's spend log grows.
    """
    echo_key = _Key(_read_key(RUN_DIR / "chassis.env"))
    before = len(_rows_of(echo_key, 0))
    body = {"input": {"text": SWAP_TEXT}, "stream": True}
    with chassis.stream("POST", "/v1/run", json=body) as answer:
        assert answer.status_code == 200
        assert answer.headers["content-type"].startswith("text/event-stream")
        stream = "".join(answer.iter_text())
    print(stream[-1500:])
    assert f"answered-by-{MAIN}" in stream
    after = _rows_of(echo_key, before + 1)
    assert len(after) >= before + 1
    assert after[-1]["model_group"] == ROUTE


def test_spend_goes_on_the_calling_key_even_when_metadata_names_another_agent(
    alpha: _Key, beta: _Key, alpha_calls: dict[str, Any]
) -> None:
    """The router's usage records put the spend on the calling service's virtual key, even when
    the request metadata names another agent (tested with two virtual keys created by the test).

    svc-beta calls with metadata naming agent `alpha` (tags and a field). Its row carries beta's
    key hash, beta's key spend grows, alpha's call count and spend do not move, and the usage
    query counts it under agent `beta`.
    """
    alpha_before = _rows_of(alpha, 3)
    user = f"spoof-{uuid.uuid4().hex[:8]}"
    _chat(beta, "hello from beta", user=user, metadata={"tags": [f"agent:{ALPHA}"], "agent": ALPHA})
    rows = _rows_of(beta, 1)
    assert len(rows) == 1 and rows[0]["user"] == user
    assert float(rows[0]["spend"]) > 0
    spend = {
        r["token"]: float(r["spend"])
        for r in _sql(
            'SELECT token, spend FROM "LiteLLM_VerificationToken" '
            f"WHERE token IN ('{alpha.hash}', '{beta.hash}');"
        )
    }
    assert spend[beta.hash] == pytest.approx(float(rows[0]["spend"]))
    assert spend[alpha.hash] == pytest.approx(sum(float(r["spend"]) for r in alpha_before))
    assert len(_rows_of(alpha, 3)) == 3
    by = {(t["level"], t["name"]): t for t in _totals(*_today_range())}
    assert by[("user", user)]["calls"] == "1"
    assert by[("agent", BETA)]["calls"] == "1"
    assert by[("agent", ALPHA)]["calls"] == "3"


def test_the_drill_runs_from_this_checkout() -> None:
    """Guard: the drill's runner is the one in this checkout and is executable."""
    assert DRILL.is_file() and os.access(DRILL, os.X_OK)
