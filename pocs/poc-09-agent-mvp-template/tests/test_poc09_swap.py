"""PoC-9 exit criterion 2.

Criterion 2: "Swap drill: one real component (for example LiteLLM for direct vLLM, or Langfuse for
another OpenTelemetry backend) is replaced in under a day, with no business logic change."

The drill runs on a scaffolded agent. One line of `config/agent.yaml` moves telemetry from `memory`
to the real `otel` adapter, and one environment variable moves its backend from Langfuse to Jaeger.
Every file of the business logic stays byte-identical. The same exporter swap against a real
Langfuse and a real Jaeger is PoC-7's `test_swap_to_direct_langfuse_by_exporter_config_only`, in
`poc07-stack` CI. Note: notes/2026-10-30-swap-drill.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from agentctl.scaffold import Answers, render
from chassis.adapters.otel import OtelTelemetry
from chassis.profiles import build_ports
from chassis.server.config import load_config

ROOT = Path(__file__).resolve().parents[3]
BACKENDS = ("http://langfuse:3000/api/public/otel", "http://jaeger:4318")


def _digest(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(folder).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.parametrize("framework", ["pydanticai", "langgraph"])
def test_c2_telemetry_swaps_by_config_with_no_business_logic_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, framework: str
) -> None:
    answers = Answers("swapped", "transformer", framework, "sidecar", ROOT)
    agent = render(answers, tmp_path)
    before = _digest(agent / "src")
    adapters = load_config(agent / "config/agent.yaml").spec.adapters
    assert adapters is not None and adapters.telemetry == "memory"

    path = agent / "config/agent.yaml"
    data = yaml.safe_load(path.read_text())
    data["spec"]["adapters"]["telemetry"] = "otel"
    path.write_text(yaml.safe_dump(data))

    config = load_config(path)
    for backend in BACKENDS:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", backend)
        monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000/v1")
        monkeypatch.setenv("LITELLM_API_KEY", "not-a-key")  # pragma: allowlist secret
        ports = build_ports(config.profile, config.spec.adapters)
        assert isinstance(ports.telemetry, OtelTelemetry), backend
    assert _digest(agent / "src") == before
