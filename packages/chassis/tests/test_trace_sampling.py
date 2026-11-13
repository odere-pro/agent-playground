"""Trace sampling is a Collector policy, and `trace_sample_rate` sets it (014 H-7, ADR-007 item 8).

Decided by the user on 2026-11-13. Two halves, both offline:

- **The chassis SDK samples nothing away.** `OtelTelemetry` records and exports every span, so the
  Collector sees whole traces. Neither `OTEL_TRACES_SAMPLER` nor a caller's unsampled
  `traceparent` (flags `00`) hides a chassis span.
- **The Collector holds the policy.** Every Collector config in `deploy/compose` runs
  `tail_sampling` first in its traces pipeline. Its rate policy reads `TRACE_SAMPLE_PERCENT`;
  `stack.sh` makes that from `TRACE_SAMPLE_RATE` (the epic's `trace_sample_rate`, 0 to 1,
  default 1.0) with `deploy/compose/trace_sample_percent.py`, the one conversion.
  suggested: traces with an error span are kept at any rate (ADR-007 does not say).

The processor keys are checked against `tailsamplingprocessor` v0.163.0's `config.go`, the module
the pinned `otel/opentelemetry-collector-contrib:0.163.2` image ships (the contrib distribution's
`manifest.yaml` at tag `v0.163.2`). No Collector runs here. The processor's real decision is a
live check, named in `docs/specs/observability.md`, "Sampling".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from chassis.adapters.otel import OtelTelemetry
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = ROOT / "deploy/compose"
STACKS = ("poc07", "poc08-litellm")
COLLECTORS = {stack: COMPOSE_DIR / stack / "otel-collector.yaml" for stack in STACKS}
CONVERTER = COMPOSE_DIR / "trace_sample_percent.py"
PERCENT_VAR = "TRACE_SAMPLE_PERCENT"

CONTRIB_IMAGE = "docker.io/otel/opentelemetry-collector-contrib:0.163.2@"
"""The pinned image. Its distribution lists `processor/tailsamplingprocessor v0.163.0`
(opentelemetry-collector-releases, `distributions/otelcol-contrib/manifest.yaml` at `v0.163.2`).
The core distribution (`otel/opentelemetry-collector`) has no tail sampling processor."""
PROCESSOR_KEYS = frozenset(
    {
        "decision_wait",
        "decision_wait_after_root_received",
        "num_traces",
        "block_on_overflow",
        "expected_new_traces_per_sec",
        "policies",
        "decision_cache",
        "sample_on_first_match",
        "sampling_strategy",
        "drop_pending_traces_on_shutdown",
        "maximum_trace_size_bytes",
        "num_shards",
    }
)
"""`tailsamplingprocessor.Config`'s top-level keys at v0.163.0 (`tail_storage` left out: it is
behind a feature gate)."""
POLICY_KEYS = {
    "probabilistic": {"hash_salt", "sampling_percentage"},
    "status_code": {"status_codes"},
}
"""The two policy types used, with their keys at v0.163.0 (`ProbabilisticCfg`, `StatusCodeCfg`)."""
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret
PARENT = "00f067aa0ba902b7"


def _converter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("trace_sample_percent", CONVERTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), path
    return data


def _sampling(stack: str, text: str | None = None) -> dict[str, Any]:
    config = yaml.safe_load(text) if text is not None else _yaml(COLLECTORS[stack])
    block = config["processors"]["tail_sampling"]
    assert isinstance(block, dict)
    return block


def _policies(block: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [p for p in block["policies"] if p["type"] == kind]


def _keeps(block: dict[str, Any], *, error: bool) -> bool:
    """The processor's decision for one trace at a rate of 0 or 100 percent.

    Policies are ORed: a trace is kept when any policy samples it (`sample_on_first_match` off).
    `status_code: [ERROR]` samples a trace with an error span. `probabilistic` samples every
    trace at 100 or more and none at 0 (`ProbabilisticCfg` at v0.163.0). Rates in between hash
    the trace id and are left to the live check.
    """
    kept = False
    for policy in block["policies"]:
        if policy["type"] == "status_code":
            kept |= error and "ERROR" in policy["status_code"]["status_codes"]
        elif policy["type"] == "probabilistic":
            percent = float(policy["probabilistic"]["sampling_percentage"])
            assert percent in (0.0, 100.0), "only 0 and 100 are decided without a hash"
            kept |= percent >= 100
        else:
            raise AssertionError(f"policy type not modeled: {policy['type']}")
    return kept


# --- The chassis SDK exports every span ---


def test_the_sdk_ignores_a_head_sampler_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OTEL_TRACES_SAMPLER=traceidratio` at 0 would drop every span before the Collector."""
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "traceidratio")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0")
    exporter = InMemorySpanExporter()
    otel = OtelTelemetry(span_exporters=[exporter], log_sink=lambda _record: None)

    with otel.span("chassis.run", trace_id=TRACE):
        pass

    assert [s.name for s in exporter.get_finished_spans()] == ["chassis.run"]


def test_the_sdk_exports_a_span_under_an_unsampled_callers_traceparent() -> None:
    """A caller's flags `00` is not a sampling decision for the chassis: the Collector decides."""
    exporter = InMemorySpanExporter()
    otel = OtelTelemetry(span_exporters=[exporter], log_sink=lambda _record: None)

    with otel.span("chassis.run", traceparent=f"00-{TRACE}-{PARENT}-00"):
        pass

    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["chassis.run"]
    context = spans[0].get_span_context()
    assert context is not None and context.trace_flags.sampled
    assert f"{context.trace_id:032x}" == TRACE


# --- trace_sample_rate to the Collector's percent: one conversion ---


@pytest.mark.parametrize(
    ("rate", "percent"),
    [("1.0", "100"), ("1", "100"), ("0", "0"), ("0.0", "0"), ("0.1", "10"), ("0.25", "25")],
)
def test_a_rate_becomes_the_collectors_percent(rate: str, percent: str) -> None:
    assert _converter().percent(rate) == percent


@pytest.mark.parametrize("rate", ["1.5", "-0.1", "nan", "inf", "ten", ""])
def test_a_rate_outside_zero_to_one_is_refused(rate: str) -> None:
    with pytest.raises(ValueError, match="TRACE_SAMPLE_RATE"):
        _converter().percent(rate)


def test_an_unset_rate_keeps_every_trace() -> None:
    """suggested: the default rate is 1.0, as the chassis marked every run sampled before."""
    module = _converter()
    assert module.DEFAULT_RATE == "1.0"
    assert module.from_env({}) == "100"
    assert module.from_env({"TRACE_SAMPLE_RATE": "0.1"}) == "10"


# --- The Collector policy ---


@pytest.mark.parametrize("stack", STACKS)
def test_the_traces_pipeline_samples_first(stack: str) -> None:
    """Sampling runs before redaction and the batch: a dropped trace costs neither."""
    processors = _yaml(COLLECTORS[stack])["service"]["pipelines"]["traces"]["processors"]
    assert processors[0] == "tail_sampling"
    assert processors[-1] == "batch"


@pytest.mark.parametrize("stack", STACKS)
def test_the_rate_policy_reads_the_percent_from_the_environment(stack: str) -> None:
    (rate,) = _policies(_sampling(stack), "probabilistic")
    assert rate["probabilistic"]["sampling_percentage"] == f"${{env:{PERCENT_VAR}}}"


@pytest.mark.parametrize("stack", STACKS)
def test_error_traces_are_kept_at_any_rate(stack: str) -> None:
    """suggested: an error span keeps its trace (ADR-007 item 8 names no exemption)."""
    (errors,) = _policies(_sampling(stack), "status_code")
    assert errors["status_code"]["status_codes"] == ["ERROR"]


def test_both_collectors_carry_one_sampling_policy() -> None:
    assert _sampling("poc07") == _sampling("poc08-litellm")


@pytest.mark.parametrize("stack", STACKS)
def test_the_policy_uses_only_keys_the_pinned_processor_has(stack: str) -> None:
    block = _sampling(stack)
    assert set(block) <= PROCESSOR_KEYS
    for policy in block["policies"]:
        kind = policy["type"]
        assert set(policy) == {"name", "type", kind}, policy["name"]
        assert set(policy[kind]) <= POLICY_KEYS[kind], policy["name"]


@pytest.mark.parametrize(
    ("rate", "keeps_ok", "keeps_error"),
    [("1.0", True, True), ("0", False, True)],
)
@pytest.mark.parametrize("stack", STACKS)
def test_the_rate_decides_which_traces_are_kept(
    stack: str, rate: str, keeps_ok: bool, keeps_error: bool
) -> None:
    """Rate 1.0 keeps every trace. Rate 0 keeps only error traces (suggested exemption)."""
    percent = _converter().percent(rate)
    text = COLLECTORS[stack].read_text().replace(f"${{env:{PERCENT_VAR}}}", percent)
    block = _sampling(stack, text)

    assert _keeps(block, error=False) is keeps_ok
    assert _keeps(block, error=True) is keeps_error


# --- How the rate reaches the Collector in Compose ---


@pytest.mark.parametrize("stack", STACKS)
def test_compose_gives_the_collector_the_percent_and_the_pinned_contrib_image(stack: str) -> None:
    services = _yaml(COMPOSE_DIR / stack / "docker-compose.yaml")["services"]
    collector = services["otel-collector"]
    assert collector["image"].startswith(CONTRIB_IMAGE)
    assert collector["environment"][PERCENT_VAR] == f"${{{PERCENT_VAR}:?}}"


@pytest.mark.parametrize("stack", STACKS)
def test_stack_sh_makes_the_percent_from_the_rate(stack: str) -> None:
    text = (COMPOSE_DIR / stack / "stack.sh").read_text()
    assert 'python3 -I "$here/../trace_sample_percent.py"' in text
    assert f"export {PERCENT_VAR}" in text
