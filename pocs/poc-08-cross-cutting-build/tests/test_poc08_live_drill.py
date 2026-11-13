"""PoC-8 live drill: stock SDK clients in the workload container (013 CH-2) and the token and cost
dashboard on live router traffic (006 G-6), on `deploy/compose/poc08-litellm` with the overlay
`live-drill.yaml`. `network`; collected only with `LIVE_DRILL=1` (conftest.py), so `make test`
never sees it. By hand, on a fresh stack:

    export POC08_LITELLM_PROJECT=live2x1113 POC08_LITELLM_OVERLAYS=live-drill.yaml
    deploy/compose/poc08-litellm/stack.sh up && deploy/compose/poc08-litellm/stack.sh wait
    LIVE_DRILL=1 uv run pytest -m network -v -s \\
        pocs/poc-08-cross-cutting-build/tests/test_poc08_live_drill.py
    deploy/compose/poc08-litellm/stack.sh down

The path of every model call here: the chassis's model proxy -> LiteLLM (auth on, guardrails on
every call) -> the fake model server, which sits on the internal network only, so the chassis
cannot reach it except through LiteLLM. The fake answers `CONTROL_TEXT` with `CONTROL_REPLY`
(model_script.py), so a reply that reads `CONTROL_REPLY` came from the fake through the router.

The tests run in file order: the SDK calls and the runs give the dashboard its series; the
one-minute test comes last and measures one more run from a settled baseline.

Credentials: Grafana's admin password comes from stack.sh's git-ignored env file and is never
printed. LiteLLM's usage table (`LiteLLM_SpendLogs`) is read with `psql` inside the Postgres
container as the database user, so the drill holds no LiteLLM key at all.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from poc_harness.drills import gate_is_on
from poc_harness.metrics_stack import (
    GRAFANA,
    GRAFANA_PASSWORD_KEY,
    GRAFANA_USER,
    PROMETHEUS,
    dashboard_file,
    dashboard_files,
    grafana_query,
    panel_queries,
    prom_query,
    prom_value,
    read_env_file,
    run,
    wait_until,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not gate_is_on("LIVE_DRILL"),
        reason="a PoC-8 live drill: needs poc08-litellm up with live-drill.yaml and LIVE_DRILL=1",
    ),
]

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "deploy/compose/poc08-litellm"
ENV_FILE = STACK / ".env.poc08-litellm"
PROJECT = os.environ.get("POC08_LITELLM_PROJECT", "poc08-litellm")
COMPOSE = (
    "docker",
    "compose",
    "-p",
    PROJECT,
    "--env-file",
    str(ENV_FILE),
    "-f",
    str(STACK / "docker-compose.yaml"),
    "-f",
    str(STACK / "live-drill.yaml"),
)
CHASSIS = "http://127.0.0.1:8080"
DATASOURCE_UID = "poc08-prometheus"
TOKENS_PANEL = "Model tokens by direction"
COST_PANEL = "Model cost (USD per hour)"
DASHBOARD_WINDOW_S = 60.0
"""The box: a test call shows on the dashboard within one minute."""
WAIT_S = float(os.environ.get("POC08_LIVE_WAIT_S", "120"))
"""suggested: how long a test waits for Prometheus, Grafana, or LiteLLM's usage writer."""
RUN_TIMEOUT_S = 120.0
EXEC_TIMEOUT_S = 180.0
RUNS = 3
NOT_CREDENTIALS = {"GPG_KEY"}
"""Key-like names that hold no credential: the python base image sets `GPG_KEY`, the fingerprint
of the CPython release signing key, public."""
RAW_TOKENS = 'sum(chassis_model_tokens_total{agent="echo"})'
SPEND_QUERY = (
    "select count(*), coalesce(sum(prompt_tokens), 0), coalesce(sum(completion_tokens), 0) "
    'from "LiteLLM_SpendLogs"'
)


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "poc08_live_model_script", STACK / "model_script.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script_module()


def _compose(*args: str) -> str:
    done = subprocess.run(
        [*COMPOSE, *args], capture_output=True, text=True, timeout=EXEC_TIMEOUT_S, check=False
    )
    assert done.returncode == 0, (args[:3], done.stderr[-2000:])
    return done.stdout


def _panel_expr(title: str) -> str:
    [expr] = [e for t, _, e in panel_queries(dashboard_file()) if t == title]
    return expr


def _panel_total(grafana: httpx.Client, title: str) -> float:
    """The sum of every series the panel's query returns through Grafana, as the panel runs it."""
    frames = grafana_query(grafana, DATASOURCE_UID, _panel_expr(title))
    total = 0.0
    for frame in frames:
        values = frame.get("data", {}).get("values") or []
        total += sum(float(v) for v in (values[-1] if values else []) if v is not None)
    return total


def _router_usage() -> tuple[int, int, int]:
    """LiteLLM's usage table: (records, prompt tokens, completion tokens)."""
    out = _compose("exec", "-T", "postgres", "psql", "-U", "litellm", "-At", "-c", SPEND_QUERY)
    count, prompt, completion = out.strip().split("|")
    return int(count), int(prompt), int(completion)


def _chassis_tokens(prom: httpx.Client) -> float:
    result = prom_query(prom, 'sum(chassis_model_tokens_total{agent="echo"})')
    return prom_value(result[0]) if result else 0.0


@pytest.fixture(scope="module")
def secrets() -> dict[str, str]:
    values = read_env_file(ENV_FILE)
    assert values.get(GRAFANA_PASSWORD_KEY), "the env file has no Grafana password"
    return values


@pytest.fixture(scope="module")
def grafana(secrets: dict[str, str]) -> Iterator[httpx.Client]:
    auth = (GRAFANA_USER, secrets[GRAFANA_PASSWORD_KEY])
    with httpx.Client(base_url=GRAFANA, timeout=10.0, auth=auth, trust_env=False) as client:
        yield client


@pytest.fixture(scope="module")
def prom() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=PROMETHEUS, timeout=10.0, trust_env=False) as client:
        yield client


@pytest.fixture(scope="module")
def sdk_answers() -> dict[str, Any]:
    """One run of sdk_clients.py inside the workload container: both SDKs, complete and streamed."""
    out = _compose(
        "exec", "-T", "workload", "python", "/opt/drill/sdk_clients.py", SCRIPT.CONTROL_TEXT
    )
    answers: dict[str, Any] = json.loads(out.strip().splitlines()[-1])
    print("sdk answers:", json.dumps(answers))
    return answers


@pytest.fixture(scope="module")
def router_traffic(sdk_answers: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """`RUNS` runs through the chassis, after the SDK calls; every model call goes to LiteLLM."""
    with httpx.Client(base_url=CHASSIS, timeout=RUN_TIMEOUT_S, trust_env=False) as chassis:
        runs = [run(chassis, SCRIPT.CONTROL_TEXT, None) for _ in range(RUNS)]
    print("runs:", [(code, body.get("status")) for code, body in runs])
    return runs


# --- 013 CH-2: stock clients in the workload container ---


@pytest.mark.parametrize("sdk", ["openai", "anthropic"])
def test_a_stock_client_in_the_workload_container_gets_a_complete_answer(
    sdk_answers: dict[str, Any], sdk: str
) -> None:
    """A stock client in the workload container gets the router's complete answer via the proxy."""
    answer = sdk_answers[sdk]

    assert answer["complete"] == SCRIPT.CONTROL_REPLY, answer


@pytest.mark.parametrize("sdk", ["openai", "anthropic"])
def test_a_stock_client_in_the_workload_container_gets_a_streamed_answer(
    sdk_answers: dict[str, Any], sdk: str
) -> None:
    """A stock client in the workload container gets the router's answer streamed via the proxy."""
    answer = sdk_answers[sdk]

    assert answer["streamed"], answer
    assert "".join(answer["streamed"]) == SCRIPT.CONTROL_REPLY, answer


def test_the_workload_container_calls_the_proxy_on_loopback_and_holds_no_key(
    sdk_answers: dict[str, Any],
) -> None:
    """The SDKs' only model URL is the chassis's loopback proxy, and no key-like variable is set."""
    assert sdk_answers["model_url"] == "http://127.0.0.1:8090/v1"
    assert [name for name in sdk_answers["key_like_env"] if name not in NOT_CREDENTIALS] == []


def test_the_router_recorded_the_sdk_calls(sdk_answers: dict[str, Any]) -> None:
    """LiteLLM's usage table holds a record for each of the four SDK calls."""
    assert sdk_answers

    wait_until(lambda: _router_usage()[0] >= 4, timeout_s=WAIT_S, poll_s=3.0, what="4 records")


# --- 006 G-6: the token and cost dashboard ---


def test_grafana_loads_every_dashboard_file_with_no_manual_step(grafana: httpx.Client) -> None:
    """The PoC-8 stack loads every dashboard with no manual steps."""
    expected = {json.loads(path.read_text())["uid"] for path in dashboard_files()}

    def loaded() -> bool:
        found = grafana.get("/api/search", params={"type": "dash-db", "limit": 5000}).json()
        return expected <= {d["uid"] for d in found}

    wait_until(loaded, timeout_s=WAIT_S, poll_s=2.0, what="Grafana to load every dashboard")
    provisioned = {
        uid: grafana.get(f"/api/dashboards/uid/{uid}").json()["meta"].get("provisioned")
        for uid in expected
    }
    assert set(provisioned.values()) == {True}, provisioned


def test_the_dashboard_shows_token_spend_and_cost_from_live_router_traffic(
    router_traffic: list[tuple[int, dict[str, Any]]], grafana: httpx.Client, prom: httpx.Client
) -> None:
    """The dashboard shows token spend and cost from live router traffic."""
    assert [code for code, _ in router_traffic] == [200] * RUNS, router_traffic

    wait_until(
        lambda: _panel_total(grafana, TOKENS_PANEL) > 0 and _panel_total(grafana, COST_PANEL) > 0,
        timeout_s=WAIT_S,
        poll_s=2.0,
        what="the token and cost panels to show the traffic",
    )
    wait_until(
        lambda: _router_usage()[0] >= 4 + RUNS,
        timeout_s=WAIT_S,
        poll_s=3.0,
        what="LiteLLM's usage records of the runs",
    )
    records, prompt, completion = _router_usage()
    print(
        "router usage:",
        {"records": records, "prompt": prompt, "completion": completion},
        "chassis tokens:",
        _chassis_tokens(prom),
        "panels:",
        {t: _panel_total(grafana, t) for t in (TOKENS_PANEL, COST_PANEL)},
    )


def test_the_dashboard_counts_the_tokens_the_router_counted(
    router_traffic: list[tuple[int, dict[str, Any]]], prom: httpx.Client
) -> None:
    """The tokens behind the dashboard equal the tokens in LiteLLM's usage table for the calls."""
    assert router_traffic

    wait_until(
        lambda: _settled(prom), timeout_s=WAIT_S, poll_s=3.0, what="chassis tokens == router tokens"
    )


def _settled(prom: httpx.Client) -> bool:
    """Prometheus holds every token LiteLLM recorded: nothing is in flight."""
    _, prompt, completion = _router_usage()
    return _chassis_tokens(prom) == prompt + completion


def _raw_tokens(grafana: httpx.Client) -> float:
    """The token counter itself through the dashboard's datasource: it moves only on new data."""
    frames = grafana_query(grafana, DATASOURCE_UID, RAW_TOKENS)
    values = frames[0].get("data", {}).get("values") if frames else None
    return float(values[-1][-1]) if values and values[-1] else 0.0


def test_a_test_call_through_the_router_shows_on_the_dashboard_within_one_minute(
    router_traffic: list[tuple[int, dict[str, Any]]], grafana: httpx.Client, prom: httpx.Client
) -> None:
    """A test call through the router shows on the dashboard within one minute."""
    assert router_traffic
    wait_until(lambda: _settled(prom), timeout_s=WAIT_S, poll_s=3.0, what="a settled baseline")
    raw_before = _raw_tokens(grafana)
    tokens_before = _panel_total(grafana, TOKENS_PANEL)
    cost_before = _panel_total(grafana, COST_PANEL)

    started = time.monotonic()
    with httpx.Client(base_url=CHASSIS, timeout=RUN_TIMEOUT_S, trust_env=False) as chassis:
        code, body = run(chassis, SCRIPT.CONTROL_TEXT, None)
    assert code == 200, body
    wait_until(
        lambda: (
            _raw_tokens(grafana) > raw_before
            and _panel_total(grafana, TOKENS_PANEL) > tokens_before
            and _panel_total(grafana, COST_PANEL) > cost_before
        ),
        timeout_s=DASHBOARD_WINDOW_S,
        poll_s=1.0,
        what="the test call on the token and cost panels",
    )
    elapsed = time.monotonic() - started

    print(f"the test call showed on the dashboard after {elapsed:.1f} s")
    assert elapsed <= DASHBOARD_WINDOW_S
