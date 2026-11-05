"""Exit criterion 1, offline half: the files the real-service run (`poc07-stack.yml`) uses are
shaped right. The Collector fans traces out to Langfuse and Jaeger, so the swap is exporter config
only; the Compose file has no default secret and binds 127.0.0.1 only; the workflow pins every
action by SHA and holds no secret. The live half is `test_poc07_stack.py` (`network`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "deploy/compose/poc07"
COLLECTOR = STACK / "otel-collector.yaml"
COMPOSE = STACK / "docker-compose.yaml"
STACK_SH = STACK / "stack.sh"
WORKFLOW = ROOT / ".github/workflows/poc07-stack.yml"

_VAR = re.compile(r"\$\{([^}]*)\}")
_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _code(path: Path) -> str:
    """The file without its comment lines."""
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), path
    return data


def _exporter(config: dict[str, Any], kind: str) -> list[tuple[str, dict[str, Any]]]:
    """The traces-pipeline exporters whose type (the part before `/`) is `kind`."""
    exporters = config["exporters"]
    names = config["service"]["pipelines"]["traces"]["exporters"]
    return [(n, exporters[n] or {}) for n in names if n.split("/")[0] == kind]


def test_collector_traces_go_to_langfuse_and_jaeger() -> None:
    """Criterion 1: "by exporter config only": one exporter to Langfuse, one to Jaeger."""
    config = _yaml(COLLECTOR)
    langfuse = _exporter(config, "otlphttp")
    jaeger = _exporter(config, "otlp")
    assert len(langfuse) == 1, "one otlphttp exporter to Langfuse"
    assert len(jaeger) == 1, "one otlp exporter to Jaeger"
    _, lf = langfuse[0]
    assert lf["endpoint"] == "http://langfuse-web:3000/api/public/otel"
    assert lf["headers"]["Authorization"] == "Basic ${env:LANGFUSE_AUTH}"
    # Langfuse v4 processes OTLP in real time only with this header (langfuse 5.0.4,
    # api/opentelemetry/raw_client.py).
    assert str(lf["headers"]["x-langfuse-ingestion-version"]) == "4"
    _, jg = jaeger[0]
    assert jg["endpoint"] == "jaeger:4317"
    assert jg["tls"]["insecure"] is True


def test_collector_receives_otlp_http_and_reports_health() -> None:
    """Criterion 1: "by exporter config only": the Collector takes OTLP/HTTP on 4318 and
    serves a health check on 13133."""
    config = _yaml(COLLECTOR)
    assert config["receivers"]["otlp"]["protocols"]["http"]["endpoint"] == "0.0.0.0:4318"
    assert config["extensions"]["health_check"]["endpoint"] == "0.0.0.0:13133"
    assert "health_check" in config["service"]["extensions"]
    assert config["service"]["pipelines"]["traces"]["receivers"] == ["otlp"]


MASK_TOKENS = ("<EMAIL_ADDRESS>", "<PHONE_NUMBER>", "<CREDIT_CARD>", "<IBAN_CODE>", "<US_SSN>")
"""PoC-8: the chassis's entity set and tokens (`chassis.redaction`, ADR-008)."""


def _log_redaction_statements() -> str:
    """Every statement of the logs pipeline's first redaction transform, joined by spaces."""
    config = _yaml(COLLECTOR)
    processors = config["service"]["pipelines"]["logs"]["processors"]
    redactor = next(p for p in processors if p.startswith("transform/"))
    blocks = config["processors"][redactor]["log_statements"]
    return " ".join(s for block in blocks for s in block["statements"])


def test_collector_logs_pipeline_has_a_redaction_transform() -> None:
    """Redaction scope: "the OpenTelemetry Collector log pipeline" runs a transform."""
    processors = _yaml(COLLECTOR)["service"]["pipelines"]["logs"]["processors"]

    redactors = [p for p in processors if p.startswith("transform/")]

    assert redactors, "the logs pipeline has a redaction transform"


def test_collector_logs_pipeline_masks_emails_and_phones() -> None:
    """Redaction scope: "the OpenTelemetry Collector log pipeline" masks each entity."""
    statements = _log_redaction_statements()

    missing = [token for token in MASK_TOKENS if token not in statements]

    assert missing == []


def test_collector_log_rules_have_no_bare_dollar() -> None:
    """Redaction: "the OpenTelemetry Collector log pipeline": the Collector expands `$`, so a
    literal regex `$` would need `$$`; the rules have none."""
    statements = _log_redaction_statements()

    assert "$" not in statements.replace("$$", "")


def test_collector_has_no_literal_credential() -> None:
    """Criterion 1: "by exporter config only": the Collector config holds no Langfuse
    key, only `${env:LANGFUSE_AUTH}`."""
    text = _code(COLLECTOR)
    assert "Basic " not in text.replace("Basic ${env:LANGFUSE_AUTH}", "")
    assert not re.search(r"(pk|sk)-lf-", text)


_COMPOSE_VARS = _VAR.findall(COMPOSE.read_text())
_PUBLISHED = [
    str(p)
    for svc in yaml.safe_load(COMPOSE.read_text())["services"].values()
    for p in svc.get("ports", [])
]


def test_compose_reads_its_secrets_from_the_env_file() -> None:
    """Criterion 1: "by exporter config only": the Compose file reads variables, so its
    secrets come from the env file."""
    assert _COMPOSE_VARS


def test_compose_has_no_default_secret() -> None:
    """Criterion 1: "by exporter config only": every variable in the Compose file is
    `${NAME:?}`, so none has a default."""
    defaulted = [v for v in _COMPOSE_VARS if not re.fullmatch(r"[A-Z0-9_]+:\?", v)]

    assert defaulted == []


@pytest.mark.parametrize("port", _PUBLISHED)
def test_compose_ports_bind_localhost_only(port: str) -> None:
    """Criterion 1: "by exporter config only": every published port binds 127.0.0.1 only."""
    assert port.startswith("127.0.0.1:")


def test_compose_publishes_the_langfuse_otlp_jaeger_and_health_ports() -> None:
    """Criterion 1: "by exporter config only": the stack publishes Langfuse (3000), OTLP
    (4318), Jaeger (16686), and health (13133)."""
    assert _PUBLISHED
    by_host = {p.split(":")[1] for p in _PUBLISHED}

    assert {"3000", "4318", "16686", "13133"} <= by_host


def test_compose_collector_starts_after_its_backends() -> None:
    """Criterion 1: "by exporter config only": the Collector starts after Langfuse and
    Jaeger; `stack.sh wait` does the health polls."""
    collector = _yaml(COMPOSE)["services"]["otel-collector"]

    assert {"langfuse-web", "jaeger"} <= set(collector["depends_on"])


@pytest.mark.parametrize("service", ["otel-collector", "jaeger", "langfuse-web"])
def test_compose_service_has_no_in_container_healthcheck(service: str) -> None:
    """Criterion 1: "by exporter config only": no in-container healthcheck, because none
    of these images is known to hold a shell or wget; `stack.sh wait` polls the host.

    The first CI run's wget check on langfuse-web never passed."""
    assert "healthcheck" not in _yaml(COMPOSE)["services"][service]


def test_stack_sh_has_a_wait_verb() -> None:
    """Criterion 1: "by exporter config only": `stack.sh` has a `wait)` case."""
    assert re.search(r"^\s*wait\)", STACK_SH.read_text(), re.MULTILINE)


@pytest.mark.parametrize("url", ["/api/public/health", "127.0.0.1:16686", "127.0.0.1:13133"])
def test_stack_sh_has_a_host_side_wait(url: str) -> None:
    """Criterion 1: "by exporter config only": `stack.sh wait` polls Langfuse, Jaeger,
    and the Collector from the host."""
    assert url in STACK_SH.read_text()


@pytest.fixture
def workflow() -> dict[str, Any]:
    return _yaml(WORKFLOW)


def test_workflow_triggers_and_permissions(workflow: dict[Any, Any]) -> None:
    """Criterion 1: "by exporter config only": the stack workflow never uses
    `pull_request_target` and reads only."""
    text = _code(WORKFLOW)
    assert "pull_request_target" not in text
    assert "secrets." not in text
    # PyYAML reads the bare key `on` as True.
    triggers: dict[str, Any] = workflow["on"] if "on" in workflow else workflow[True]
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}


def test_workflow_pins_every_action_by_sha(workflow: dict[str, Any]) -> None:
    """Criterion 1: "by exporter config only": the stack workflow pins every action by a
    full commit SHA."""
    uses = [
        step["uses"] for job in workflow["jobs"].values() for step in job["steps"] if "uses" in step
    ]
    unpinned = [ref for ref in uses if not _SHA.match(ref)]

    assert uses
    assert unpinned == []


def test_workflow_runs_the_stack_and_always_tears_it_down(workflow: dict[str, Any]) -> None:
    """Criterion 1: "by exporter config only": the workflow runs the stack tests and
    always tears the stack down."""
    (job,) = workflow["jobs"].values()
    assert job["timeout-minutes"] <= 30
    runs = [(step.get("if"), step.get("run", "")) for step in job["steps"]]
    joined = "\n".join(r for _, r in runs)
    verbs = ("up", "wait", "digests")
    missing = [v for v in verbs if f"deploy/compose/poc07/stack.sh {v}" not in joined]
    assert missing == []
    assert "POC07_STACK=1" in joined or job.get("env", {}).get("POC07_STACK") == "1"
    assert "test_poc07_stack.py" in joined
    assert any(cond == "always()" and "stack.sh down" in run for cond, run in runs), (
        "stack.sh down runs always"
    )
    logs = [run for cond, run in runs if cond == "failure()"]
    assert logs and "logs" in logs[0] and "redact" in logs[0]
    # The env file path comes from `stack.sh env`; the workflow never names or reads it.
    assert ".env.poc07" not in WORKFLOW.read_text()


_DIGEST = re.compile(r"^[^@]+:[^@]+@sha256:[0-9a-f]{64}$")
_SERVICES: dict[str, Any] = _yaml(COMPOSE)["services"]


@pytest.mark.parametrize("service", sorted(_SERVICES))
def test_compose_image_is_pinned_by_digest_when_the_service_names_an_image(service: str) -> None:
    """Criterion 1: "by exporter config only": every image is `name:tag@sha256:<digest>`
    (SECURITY.md, section 2)."""
    image = _SERVICES[service]["image"]

    pinned = _DIGEST.match(image)

    assert pinned, f"{service}: {image}"


COMPOSE_README = ROOT / "deploy/compose/README.md"


def _readme_deviations() -> set[str]:
    """The service names listed as `- \\`name\\`` in the README's "PoC-7 stack" section."""
    text = COMPOSE_README.read_text()
    section = text.partition("## PoC-7 stack")[2].partition("\n## ")[0]
    return set(re.findall(r"^- `([a-z0-9-]+)`", section, re.MULTILINE))


@pytest.mark.parametrize("service", sorted(_SERVICES))
def test_compose_service_drops_all_capabilities_when_not_a_listed_deviation(service: str) -> None:
    """Criterion 1: "by exporter config only": a service has `cap_drop: [ALL]`, or the
    compose README names it as a deviation."""
    cap_drop = _SERVICES[service].get("cap_drop")

    hardened_or_listed = cap_drop == ["ALL"] or service in _readme_deviations()

    assert hardened_or_listed, f"{service}: no cap_drop [ALL] and not in the README deviations"


def _statements(block: list[dict[str, Any]], context: str) -> list[str]:
    """The statements of one OTTL context in a transform's statement list."""
    return [s for b in block if b["context"] == context for s in b["statements"]]


def test_collector_traces_pipeline_redacts_before_the_batch() -> None:
    """Redaction: "the OpenTelemetry Collector log pipeline": spans pass a redaction transform
    before `batch`, as logs do (review R7-10)."""
    processors = _yaml(COLLECTOR)["service"]["pipelines"]["traces"]["processors"]

    redactors = [p for p in processors if p.startswith("transform/")]

    assert redactors == ["transform/redact_spans"]
    assert processors.index("transform/redact_spans") < processors.index("batch")


@pytest.mark.parametrize("context", ["span", "spanevent"])
def test_collector_span_rules_match_the_log_attribute_rules(context: str) -> None:
    """Redaction: "the OpenTelemetry Collector log pipeline": span and span-event attributes get
    the same masking rules as log attributes."""
    processors = _yaml(COLLECTOR)["processors"]
    logs = _statements(processors["transform/redact"]["log_statements"], "log")
    log_attributes = [s for s in logs if s.startswith("replace_all_patterns(attributes")]

    statements = _statements(processors["transform/redact_spans"]["trace_statements"], context)

    spans = [s for s in statements if s.startswith("replace_all_patterns(")]

    assert log_attributes
    assert spans == log_attributes


_SAVE = re.compile(
    r'^set\(cache\["([^"]+)"\], attributes\["\1"\]\) where attributes\["\1"\] != nil$'
)
_RESTORE = re.compile(r'^set\(attributes\["([^"]+)"\], cache\["\1"\]\) where cache\["\1"\] != nil$')
_REPLACE = re.compile(r'^replace_all_patterns\(attributes, "value", "(.*)", "(<[A-Z_]+>)"\)$')


def _span_statements() -> list[str]:
    processors = _yaml(COLLECTOR)["processors"]
    return _statements(processors["transform/redact_spans"]["trace_statements"], "span")


def _run_span_rules(attributes: dict[str, str]) -> dict[str, str]:
    """The span-context statements applied with Python's `re` (the patterns are RE2-safe)."""
    cache: dict[str, str] = {}
    out = dict(attributes)
    for statement in _span_statements():
        if save := _SAVE.match(statement):
            cache.update({k: out[k] for k in [save.group(1)] if k in out})
        elif restore := _RESTORE.match(statement):
            out.update({k: cache[k] for k in [restore.group(1)] if k in cache})
        else:
            match = _REPLACE.match(statement)
            if match is None:
                raise ValueError(f"not a save, restore, or replace statement: {statement}")
            out = {k: re.sub(match.group(1), match.group(2), v) for k, v in out.items()}
    return out


def test_collector_span_rules_save_every_chassis_id_key_around_the_masking() -> None:
    """Redaction: "the OpenTelemetry Collector log pipeline": each `ID_KEYS` entry is saved before
    the first mask and put back after the last."""
    from chassis.adapters.otel import ID_KEYS

    statements = _span_statements()
    saved = [m.group(1) for s in statements if (m := _SAVE.match(s))]
    restored = [m.group(1) for s in statements if (m := _RESTORE.match(s))]
    masks = [i for i, s in enumerate(statements) if _REPLACE.match(s)]

    assert sorted(saved) == sorted(ID_KEYS) == sorted(restored)
    assert statements[: masks[0]] == statements[: len(saved)]
    assert len(statements) == len(saved) + len(masks) + len(restored)


def test_collector_span_rules_keep_an_id_when_it_looks_like_a_card_number() -> None:
    """Redaction: "the OpenTelemetry Collector log pipeline": a trace id with 13 digits in a row
    survives; the same digits in a plain attribute do not."""
    digits = "4111111111111"
    attributes = {"trace_id": f"ab{digits}cdef0123456789ab", "note": f"card {digits}"}

    out = _run_span_rules(attributes)

    assert out["trace_id"] == attributes["trace_id"]
    assert out["note"] == "card <CREDIT_CARD>"
