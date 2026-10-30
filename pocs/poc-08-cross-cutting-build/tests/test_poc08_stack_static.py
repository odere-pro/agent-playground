"""PoC-8 scope item "Observability", offline half of the stack drill: the files
`poc08-stack.yml` runs are shaped right, before a runner starts Docker.

- `deploy/compose/poc08`: every pulled image pinned by digest (or named in `AWAITING_DIGEST`
  until the first CI run prints it), no default secret, ports on 127.0.0.1 only, the chassis
  built from `packages/chassis/Dockerfile`, each chassis config valid for `chassis serve`.
- Prometheus scrapes the chassis at the path the chassis serves its metrics on; Grafana
  provisions the Prometheus datasource the dashboards' `${datasource}` variable resolves to, and
  every file of `deploy/dashboards/`.
- The model script answers each drill input with the rule the drill needs, the judge included
  (the real `judge_messages` and `parse_verdict`).
- The workflow: read-only, every action pinned by SHA, no secret, the stack always torn down.
- The drill is collected only with `POC08_STACK=1`, and is `network`.
- The drill's and the latency script's helpers, on `httpx.MockTransport`.

The live half is `test_poc08_stack_metrics.py` (`network`) and `poc08_stack_latency.py`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import runpy
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from chassis.dashboards import DASHBOARD_DIR, panel_exprs
from chassis.ports.evaluator import EvalCase
from chassis.profiles import REGISTRY
from chassis.server.config import load_config
from chassis.server.observability import METRICS_PATH
from fake_model_server import Script
from poc08_stack import (
    AGENT,
    CHASSIS,
    CHASSIS_PLAIN,
    CHASSIS_STAGES,
    DASHBOARD_UID,
    DRILL_FILE,
    FAIL_INPUT,
    FALLBACK_ANSWER,
    FALLBACK_ROUTE,
    GATE_BAD,
    GATE_INPUT,
    GRAFANA,
    GRAFANA_PASSWORD_KEY,
    JUDGE_ROUTE,
    MAIN_ROUTE,
    PLAIN_INPUT,
    PROMETHEUS,
    ROOT,
    SCRAPE_JOB,
    STACK,
    STACK_VAR,
    TOKEN_KEY,
    TOOL_INPUT,
    dashboard_file,
    dashboard_files,
    frame_has_values,
    grafana_query,
    panel_queries,
    percentile,
    prom_query,
    read_env_file,
    summarize,
)
from poc08_stack_latency import STAGES_OFF, STAGES_ON, Target, measure, report

HERE = Path(__file__).resolve().parent
COMPOSE = STACK / "docker-compose.yaml"
STACK_SH = STACK / "stack.sh"
PROMETHEUS_YML = STACK / "prometheus/prometheus.yml"
DATASOURCES = STACK / "grafana/provisioning/datasources/prometheus.yaml"
PROVIDERS = STACK / "grafana/provisioning/dashboards/chassis.yaml"
MODEL_SCRIPT = STACK / "fake-model/poc08.yaml"
WORKFLOW = ROOT / ".github/workflows/poc08-stack.yml"
CHASSIS_SERVICES = {
    "chassis": CHASSIS,
    "chassis-stages": CHASSIS_STAGES,
    "chassis-plain": CHASSIS_PLAIN,
}
CONFIG_OF = {
    "chassis": "drill.yaml",
    "chassis-stages": "stages.yaml",
    "chassis-plain": "plain.yaml",
}
OURS = "agent-platform/"
"""Images built from this repo by `stack.sh up`; never pulled, so no registry digest."""
AWAITING_DIGEST = {"docker.io/prom/prometheus", "docker.io/grafana/grafana"}
"""suggested: tags until the first CI run's `stack.sh digests` prints their digests (no registry
was reachable where this was written). Pin each and remove it here; the test fails if a name here
is pinned or a name not here is not."""

_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")
_VAR = re.compile(r"\$\{([^}]*)\}")


def _yaml(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), path
    return data


def _services() -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = _yaml(COMPOSE)["services"]
    return services


def _code(path: Path) -> str:
    """The file without its comment lines."""
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def _port(url: str) -> str:
    return url.rsplit(":", 1)[1]


# --- Compose ---------------------------------------------------------------------------------


def test_every_pulled_image_is_pinned_by_digest() -> None:
    unpinned: set[str] = set()
    for name, service in _services().items():
        image = str(service["image"])
        if image.startswith(OURS):
            assert "build" in service or service.get("pull_policy") == "never", name
            continue
        if not _DIGEST.match(image):
            unpinned.add(image.split(":")[0])
    assert unpinned == AWAITING_DIGEST, "pin by digest, or update AWAITING_DIGEST on purpose"


def test_the_compose_file_has_no_default_secret() -> None:
    found = _VAR.findall(_code(COMPOSE))
    assert found
    for inner in found:
        assert re.fullmatch(r"[A-Z0-9_]+:\?[^}]*", inner), f"${{{inner}}} must be ${{NAME:?...}}"
    names = {inner.split(":")[0] for inner in found}
    assert {TOKEN_KEY, GRAFANA_PASSWORD_KEY, "VALKEY_PASSWORD"} <= names
    for name in names:
        assert f'echo "{name}=' in STACK_SH.read_text(), f"stack.sh does not make {name}"


def test_ports_bind_localhost_only_and_match_the_drill() -> None:
    services = _services()
    published = {
        name: [str(p) for p in service.get("ports", [])] for name, service in services.items()
    }
    for ports in published.values():
        for port in ports:
            assert port.startswith("127.0.0.1:"), port
    wanted = {**CHASSIS_SERVICES, "prometheus": PROMETHEUS, "grafana": GRAFANA}
    for name, url in wanted.items():
        assert published[name] and published[name][0].split(":")[1] == _port(url), name
    for name, service in services.items():
        assert service.get("network_mode") != "host", name


def test_the_chassis_is_built_from_its_dockerfile_and_hardened() -> None:
    services = _services()
    built = services["chassis"]["build"]
    assert built["dockerfile"] == "packages/chassis/Dockerfile"
    assert (STACK / built["context"]).resolve() == ROOT
    images = {services[name]["image"] for name in CHASSIS_SERVICES}
    assert len(images) == 1, "one chassis image for all three"
    for name in (*CHASSIS_SERVICES, "fake-model-server", "valkey", "prometheus", "grafana"):
        service = services[name]
        assert service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
    model = services["fake-model-server"]
    assert model["build"]["dockerfile"] == "packages/fake-model-server/Dockerfile"


@pytest.mark.parametrize("service", sorted(CHASSIS_SERVICES))
def test_each_chassis_serves_a_valid_config_against_valkey(service: str) -> None:
    spec = _services()[service]
    command = [str(c) for c in spec["command"]]
    path = command[command.index("--config") + 1]
    assert path == f"/etc/chassis/{CONFIG_OF[service]}"
    assert any(str(v).startswith("./chassis:/etc/chassis") for v in spec["volumes"])
    config = load_config(STACK / "chassis" / CONFIG_OF[service])
    adapters = config.spec.adapters
    assert adapters is not None
    for port, adapter in adapters.model_dump(exclude_none=True).items():
        assert adapter in REGISTRY[port], f"{port}: {adapter}"
    assert adapters.state == "valkey"
    assert adapters.model == "litellm"
    assert config.spec.engine.connector == "inprocess"
    env = spec["environment"]
    assert env["LITELLM_BASE_URL"] == "http://fake-model-server:8081/v1"
    assert env["VALKEY_URL"].startswith("valkey://valkey:")
    if adapters.auth == "static":
        assert "CHASSIS_STATIC_TOKENS" in env
    assert config.spec.observability.metrics_scope is None, "Prometheus scrapes with no token"


def test_the_drill_chassis_has_every_poc08_stage_on() -> None:
    config = load_config(STACK / "chassis/drill.yaml")
    spec, adapters = config.spec, config.spec.adapters
    assert config.agent.name == AGENT
    assert adapters is not None
    assert (adapters.auth, adapters.guardrails, adapters.evaluator, adapters.feedback) == (
        "static",
        "fake",
        "judge",
        "memory",
    )
    assert spec.limits.rate_limit and spec.limits.rate_limit_on_unavailable == "fail_closed"
    assert spec.guardrails.input and spec.guardrails.tool_output and spec.guardrails.output
    assert spec.harness.evaluator_gate and spec.harness.evaluator_mode == "sync"
    assert spec.model.route == MAIN_ROUTE and spec.model.fallback_route == FALLBACK_ROUTE
    assert MAIN_ROUTE in spec.observability.prices, "the cost panel needs a price"
    assert spec.governance.risk_class != "minimal"
    assert _services()["chassis"]["environment"]["CHASSIS_JUDGE_ROUTE"] == JUDGE_ROUTE


def test_the_latency_pair_differs_only_in_the_stages() -> None:
    on = load_config(STACK / "chassis/stages.yaml")
    off = load_config(STACK / "chassis/plain.yaml")
    assert on.spec.engine == off.spec.engine and on.spec.model == off.spec.model
    assert on.spec.adapters is not None and off.spec.adapters is not None
    for port in ("model", "config", "telemetry", "tools", "state", "events", "evaluator"):
        assert getattr(on.spec.adapters, port) == getattr(off.spec.adapters, port), port
    assert (on.spec.adapters.auth, on.spec.adapters.guardrails) == ("static", "fake")
    assert (off.spec.adapters.auth, off.spec.adapters.guardrails) == ("none", "none")
    assert on.spec.limits.rate_limit and not off.spec.limits.rate_limit
    assert not on.spec.harness.evaluator_gate and not off.spec.harness.evaluator_gate
    services = _services()
    assert services["chassis-stages"]["cpus"] == services["chassis-plain"]["cpus"]


# --- Prometheus and Grafana --------------------------------------------------------------------


def test_prometheus_scrapes_every_chassis_at_its_metrics_path() -> None:
    config = _yaml(PROMETHEUS_YML)
    [job] = [j for j in config["scrape_configs"] if j["job_name"] == SCRAPE_JOB]
    assert job["metrics_path"] == METRICS_PATH == "/metrics"
    targets = {t for group in job["static_configs"] for t in group["targets"]}
    assert targets == {f"{name}:8080" for name in CHASSIS_SERVICES}
    services = _services()
    mounts = services["prometheus"]["volumes"]
    assert "./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" in mounts
    assert "--config.file=/etc/prometheus/prometheus.yml" in services["prometheus"]["command"]


def test_grafana_provisions_the_datasource_the_dashboards_name() -> None:
    [source] = _yaml(DATASOURCES)["datasources"]
    kinds = {
        v["query"]
        for path in dashboard_files()
        for v in json.loads(path.read_text())["templating"]["list"]
        if v["type"] == "datasource"
    }
    assert kinds == {source["type"]} == {"prometheus"}
    assert source["isDefault"] is True, "the `datasource` variable takes the default"
    assert source["url"] == "http://prometheus:9090"
    assert "--web.listen-address=:9090" in _services()["prometheus"]["command"]
    for path in dashboard_files():
        for panel in json.loads(path.read_text())["panels"]:
            assert panel["datasource"] == {"type": "prometheus", "uid": "${datasource}"}


def test_grafana_provisions_every_file_of_deploy_dashboards() -> None:
    [provider] = _yaml(PROVIDERS)["providers"]
    assert provider["type"] == "file"
    inside = provider["options"]["path"]
    grafana = _services()["grafana"]
    mounts = {str(v).split(":")[1]: str(v).split(":")[0] for v in grafana["volumes"]}
    assert (STACK / mounts[inside]).resolve() == (ROOT / DASHBOARD_DIR).resolve()
    assert (STACK / mounts["/etc/grafana/provisioning"]).resolve() == STACK / "grafana/provisioning"
    assert grafana["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert len(dashboard_files()) >= 2 and DASHBOARD_UID in {
        json.loads(p.read_text())["uid"] for p in dashboard_files()
    }


def test_the_drill_asks_every_panel_query_of_the_agents_dashboard() -> None:
    board = dashboard_file()
    queries = panel_queries(board)
    assert [expr for _, _, expr in queries] == panel_exprs(board)
    assert len({title for title, _, _ in queries}) == len(board["panels"]) == 7
    assert "User feedback by label" in {title for title, _, _ in queries}


# --- The model script ------------------------------------------------------------------------


def _msgs(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"role": role, "content": content} for role, content in pairs]


def _judge(answer: str, text: str) -> float:
    from chassis.adapters.evaluators import judge_messages, parse_verdict

    script = Script.from_yaml(MODEL_SCRIPT)
    messages = [
        {"role": m.role, "content": m.content}
        for m in judge_messages(EvalCase(input=text, output=answer))
    ]
    rule = script.pick(messages, model=JUDGE_ROUTE)
    score, _ = parse_verdict(rule.reply)
    return score


def test_the_model_script_answers_each_drill_input_as_the_drill_needs() -> None:
    script = Script.from_yaml(MODEL_SCRIPT)
    system = ("system", "Rewrite in plain words. Short sentences. Keep every fact.")
    plain = script.pick(_msgs(system, ("user", PLAIN_INPUT)), model=MAIN_ROUTE)
    assert plain.reply and plain.tool_call is None and plain.error is None
    tool = script.pick(_msgs(system, ("user", TOOL_INPUT)), model=MAIN_ROUTE)
    assert tool.tool_call is not None and tool.tool_call.name == "glossary_lookup"
    after = [
        *_msgs(system, ("user", TOOL_INPUT)),
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "content": "SLM: small language model", "tool_call_id": "c1"},
    ]
    assert script.pick(after, model=MAIN_ROUTE).reply.startswith("From the glossary")
    gate = script.pick(_msgs(system, ("user", GATE_INPUT)), model=MAIN_ROUTE)
    assert gate.reply == GATE_BAD
    fallback = script.pick(_msgs(system, ("user", GATE_INPUT)), model=FALLBACK_ROUTE)
    assert fallback.reply == FALLBACK_ANSWER
    fail = script.pick(_msgs(system, ("user", FAIL_INPUT)), model=MAIN_ROUTE)
    assert fail.error is not None and fail.error.status >= 500


def test_the_judge_fails_only_the_gate_answer() -> None:
    threshold = load_config(STACK / "chassis/drill.yaml").spec.harness.threshold
    assert _judge(GATE_BAD, GATE_INPUT) < threshold
    for text, answer in (
        (PLAIN_INPUT, "Plain words. Short sentences. Same facts."),
        (TOOL_INPUT, "From the glossary: SLM means small language model."),
        (FAIL_INPUT, ""),
    ):
        assert _judge(answer, text) >= threshold, text


# --- stack.sh and the workflow ---------------------------------------------------------------


def test_stack_sh_runs_project_poc08_and_never_prints_the_env_file() -> None:
    text = STACK_SH.read_text()
    assert "docker compose -p poc08" in text
    for verb in ("up", "wait", "down", "digests", "env", "logs"):
        assert re.search(rf"^\s*{verb}\)", text, re.MULTILINE), verb
    for url in (CHASSIS, CHASSIS_STAGES, CHASSIS_PLAIN):
        assert f"{url}/ready" in text
    assert f"{PROMETHEUS}/-/ready" in text and f"{GRAFANA}/api/health" in text
    assert "umask 077" in text
    code = _code(STACK_SH)
    assert not re.search(r"cat\s+\"?\$env_file", code)
    assert "set -x" not in code


@pytest.fixture
def workflow() -> dict[Any, Any]:
    return _yaml(WORKFLOW)


def test_the_workflow_is_read_only_pinned_and_has_no_secret(workflow: dict[Any, Any]) -> None:
    text = _code(WORKFLOW)
    assert "pull_request_target" not in text
    assert "secrets." not in text
    triggers: dict[str, Any] = workflow["on"] if "on" in workflow else workflow[True]
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    assert "deploy/compose/poc08/**" in triggers["push"]["paths"]
    assert "deploy/dashboards/**" in triggers["push"]["paths"]
    assert workflow["permissions"] == {"contents": "read"}
    for name, job in workflow["jobs"].items():
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}, name
        for step in job["steps"]:
            if "uses" in step:
                assert _SHA.match(step["uses"]), step["uses"]
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_the_workflow_runs_the_drill_and_the_measurement_then_tears_down(
    workflow: dict[Any, Any],
) -> None:
    (job,) = workflow["jobs"].values()
    assert job["timeout-minutes"] <= 30
    steps = job["steps"]
    runs = [(step.get("if"), str(step.get("run", ""))) for step in steps]
    joined = "\n".join(r for _, r in runs)
    order = [f"deploy/compose/poc08/stack.sh {verb}" for verb in ("up", "wait", "digests")]
    positions = [joined.index(verb) for verb in order]
    assert positions == sorted(positions)
    assert f"{STACK_VAR}=1" in joined and "-m network" in joined and DRILL_FILE in joined
    assert "poc08_stack_latency.py" in joined and "--out" in joined
    assert joined.index(DRILL_FILE) > positions[-1]
    assert any(cond == "always()" and "stack.sh down" in run for cond, run in runs)
    logs = [run for cond, run in runs if cond == "failure()"]
    assert logs and "stack.sh logs" in logs[0]
    uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert uploads and uploads[0].get("if") == "always()"
    assert ".env.poc08" not in WORKFLOW.read_text()


# --- The drill's collection ------------------------------------------------------------------


def test_the_drill_is_collected_only_with_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(STACK_VAR, raising=False)
    off = runpy.run_path(str(HERE / "conftest.py"))["collect_ignore"]
    assert DRILL_FILE in off
    monkeypatch.setenv(STACK_VAR, "1")
    on = runpy.run_path(str(HERE / "conftest.py"))["collect_ignore"]
    assert DRILL_FILE not in on


def test_the_drill_is_marked_network_and_skips_without_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(STACK_VAR, raising=False)
    spec = importlib.util.spec_from_file_location("poc08_stack_drill_probe", HERE / DRILL_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # a dataclass looks its module up
    spec.loader.exec_module(module)
    marks = {mark.name: mark for mark in module.pytestmark}
    assert "network" in marks
    assert marks["skipif"].args == (True,)


# --- The helpers, offline --------------------------------------------------------------------


def test_read_env_file_reads_values_and_needs_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env.test"
    env.write_text(f"{TOKEN_KEY}=t0k\n# a comment\n{GRAFANA_PASSWORD_KEY}=pw=with=eq\n")
    assert read_env_file(env) == {TOKEN_KEY: "t0k", GRAFANA_PASSWORD_KEY: "pw=with=eq"}
    monkeypatch.delenv("POC08_ENV_FILE", raising=False)
    with pytest.raises(LookupError):
        read_env_file()


def test_prom_query_returns_the_result_and_fails_on_an_error() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params["query"] == "bad(":
            return httpx.Response(400, json={"status": "error", "error": "parse"})
        sample = {"metric": {"label": "thumbs-down"}, "value": [0, "2"]}
        return httpx.Response(200, json={"status": "success", "data": {"result": [sample]}})

    with httpx.Client(base_url=PROMETHEUS, transport=httpx.MockTransport(answer)) as client:
        assert prom_query(client, "up")[0]["metric"]["label"] == "thumbs-down"
        with pytest.raises(AssertionError):
            prom_query(client, "bad(")


def test_grafana_query_posts_one_instant_query_on_the_datasource() -> None:
    seen: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        frame = {"schema": {}, "data": {"values": [[1], [2.0]]}}
        return httpx.Response(200, json={"results": {"A": {"status": 200, "frames": [frame]}}})

    with httpx.Client(base_url=GRAFANA, transport=httpx.MockTransport(answer)) as client:
        frames = grafana_query(client, "poc08-prometheus", "sum(up)")
    assert frames and frame_has_values(frames[0])
    [query] = seen[0]["queries"]
    assert query["datasource"] == {"type": "prometheus", "uid": "poc08-prometheus"}
    assert query["instant"] is True and query["expr"] == "sum(up)"


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile([3.0], 95) == 3.0
    with pytest.raises(ValueError):
        percentile([], 50)
    assert summarize([1.0, 2.0, 3.0, 4.0])["p50_ms"] == 2.0


def test_the_latency_script_interleaves_counts_errors_and_writes_the_overhead() -> None:
    calls: list[str] = []

    def chassis(name: str, refuse_every: int = 0) -> httpx.MockTransport:
        def answer(request: httpx.Request) -> httpx.Response:
            calls.append(name)
            assert json.loads(request.content) == {"input": {"text": PLAIN_INPUT}}
            if name == STAGES_ON:
                assert request.headers["authorization"] == "Bearer t0k"
            else:
                assert "authorization" not in request.headers
            if refuse_every and len(calls) % refuse_every == 0:
                return httpx.Response(429, json={"detail": {"code": "rate_limited"}})
            return httpx.Response(200, json={"status": "ok"})

        return httpx.MockTransport(answer)

    ticks = iter(float(i) / 1000 for i in range(10_000))
    targets = [Target(STAGES_ON, CHASSIS_STAGES, "t0k"), Target(STAGES_OFF, CHASSIS_PLAIN, None)]
    clients = {
        STAGES_ON: httpx.Client(base_url=CHASSIS_STAGES, transport=chassis(STAGES_ON)),
        STAGES_OFF: httpx.Client(base_url=CHASSIS_PLAIN, transport=chassis(STAGES_OFF, 7)),
    }
    measure(clients, targets, requests=20, warmup=2, timer=lambda: next(ticks))
    assert calls[:4] == [STAGES_ON, STAGES_OFF, STAGES_ON, STAGES_OFF], "interleaved"
    assert len(calls) == 2 * (20 + 2)
    on, off = targets
    assert len(on.timings_ms) == 20 and not on.errors
    assert off.errors and len(off.timings_ms) + len(off.errors) == 20
    data = report(targets, requests=20, warmup=2, text=PLAIN_INPUT)
    assert data["targets"][STAGES_ON]["p50_ms"] == pytest.approx(1.0)
    assert data["targets"][STAGES_OFF]["errors"] == len(off.errors)
    assert set(data["overhead_ms"]) == {"p50", "p95"}
    assert "suggested" in data and data["setup"]["requests_per_target"] == 20
    assert "t0k" not in json.dumps(data), "the token never reaches the JSON"
