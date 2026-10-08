"""PoC-1 walking skeleton: the first real adapter, `ModelPort` over OpenAI-compatible HTTP.

Each test names the exit criterion in docs/planning/poc/001-PoC-1-walking-skeleton.md it covers.
No network: the adapter talks to the fake model server over an ASGI transport.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from chassis.adapters.litellm import LiteLLMModel
from chassis.fakes import ScriptedModel
from chassis.ports.model import ModelMessage
from chassis.profiles import AdapterNotAvailable, AdapterSpec, build_ports
from chassis_contracts import ModelPortContract
from fake_model_server import Script, create_app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"


def _load(path: Path) -> ModuleType:
    """Import a test module by path; the chassis tests folder is not a package."""
    spec = importlib.util.spec_from_file_location(f"_poc01_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _litellm_over_fake_server() -> LiteLLMModel:
    app = create_app(Script.from_yaml(EXAMPLE_SCRIPT))
    return LiteLLMModel("http://fake/v1", agent="poc-01", transport=httpx.ASGITransport(app=app))


def test_real_adapter_binds_the_same_contract_suite_as_the_fake() -> None:
    """Exit criterion: the real `ModelPort` adapter passes the same contract suite as its fake.

    The bindings live in packages/chassis/tests/test_contracts.py; this checks both derive from
    one suite class.
    """
    bindings = _load(ROOT / "packages/chassis/tests/test_contracts.py")
    assert issubclass(bindings.TestScriptedModel, ModelPortContract)
    assert issubclass(bindings.TestLiteLLMModel, ModelPortContract)


async def test_real_adapter_streams_and_completes_the_same_text() -> None:
    """Exit criterion: the real `ModelPort` adapter passes the same contract suite as its fake.
    One representative case run here: streaming and complete agree through the litellm adapter.
    """
    model = _litellm_over_fake_server()
    messages: list[ModelMessage] = [{"role": "user", "content": "simplify: the quick brown fox"}]
    complete = await model.complete(messages, route="big-default")
    streamed = [c async for c in model.stream(messages, route="big-default")]
    expected = "Plain words. Short sentences. Same facts."
    assert complete.text == "".join(c.text for c in streamed) == expected
    assert streamed[-1].finish and streamed[-1].usage == complete.usage
    assert (complete.usage.input_tokens, complete.usage.output_tokens) == (42, 9)


def test_switching_the_model_adapter_is_a_config_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit criterion: switching the model adapter (`fake` or `litellm`) or the model route needs
    a config change only.
    """
    monkeypatch.setenv("LITELLM_BASE_URL", "http://router:4000/v1")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    assert isinstance(build_ports("fake").model, ScriptedModel)
    assert isinstance(build_ports("fake", AdapterSpec(model="litellm")).model, LiteLLMModel)


def test_litellm_without_a_base_url_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit criterion: switching the model adapter needs a config change only. The missing config
    is named, and no key is required to build the adapter.
    """
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    with pytest.raises(AdapterNotAvailable, match="model: litellm needs LITELLM_BASE_URL"):
        build_ports("fake", AdapterSpec(model="litellm"))
