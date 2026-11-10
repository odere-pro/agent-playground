"""Each fake adapter returns a scripted error, so retry and fallback paths can be tested (015 H-8).

One test per fake: script the error, the call fails with the port's own error type, code, and
`retryable`, and the next call (the retry) succeeds. `InMemoryTelemetry` is not here: the
`TelemetryPort` has no error, because telemetry never fails a run.
"""

from __future__ import annotations

import pytest
from chassis.core.events import End, Error, Start
from chassis.fakes import (
    FakeEngine,
    InMemoryBus,
    InMemoryConfig,
    InMemoryFeedback,
    InMemoryState,
    KeywordGuardrails,
    ScriptedEvaluator,
    ScriptedModel,
    ScriptRule,
    StaticTokens,
    default_tools,
)
from chassis.ports.auth import AuthError, Principal
from chassis.ports.config import ConfigUnavailable
from chassis.ports.evaluator import EvalCase, EvaluatorError
from chassis.ports.events import PublishFailed
from chassis.ports.feedback import FeedbackError
from chassis.ports.guardrail import GuardrailError
from chassis.ports.model import ModelError, ModelMessage
from chassis.ports.state import StateUnavailable
from chassis.ports.tool import ToolError
from chassis_contracts.events import make_event
from chassis_contracts.feedback import make_feedback
from chassis_contracts.helpers import make_context, make_request

ROUTE = "fake-route"
TOOL = "glossary_lookup"
ARGS = {"term": "RAG"}


async def test_the_model_fake_scripts_a_retryable_error_then_answers() -> None:
    model = ScriptedModel(
        [ScriptRule(match="busy", error="rate_limited", retryable=True)], default_reply="ok"
    )
    with pytest.raises(ModelError) as info:
        await model.complete([ModelMessage(role="user", content="busy now")], route=ROUTE)
    assert (info.value.code, info.value.retryable) == ("rate_limited", True)
    with pytest.raises(ModelError):
        async for _ in model.stream([ModelMessage(role="user", content="busy")], route=ROUTE):
            pass
    answer = await model.complete([ModelMessage(role="user", content="calm")], route=ROUTE)
    assert answer.text == "ok"


async def test_the_tool_fake_scripts_one_error_then_answers() -> None:
    tools = default_tools()
    tools.fail_next_call(ToolError("tool_unavailable", "scripted", retryable=True))
    with pytest.raises(ToolError) as info:
        await tools.call(TOOL, ARGS)
    assert (info.value.code, info.value.retryable) == ("tool_unavailable", True)
    assert (await tools.call(TOOL, ARGS)).content


async def test_the_event_fake_scripts_publish_failures_then_publishes() -> None:
    bus = InMemoryBus()
    bus.fail_next_publish(times=2)
    event = make_event("agents.test")
    for _ in range(2):
        with pytest.raises(PublishFailed):
            await bus.publish("agents.test", event)
    await bus.publish("agents.test", event)
    assert bus.published == [("agents.test", event)]


async def test_the_state_fake_scripts_one_outage_then_answers() -> None:
    state = InMemoryState()
    state.fail_next()
    with pytest.raises(StateUnavailable):
        await state.set("k", b"v")
    await state.set("k", b"v")
    assert await state.get("k") == b"v"


async def test_the_config_fake_scripts_one_outage_then_loads() -> None:
    config = InMemoryConfig({"agent": {"x": 1}})
    config.fail_next()
    with pytest.raises(ConfigUnavailable, match="scripted outage"):
        await config.load("agent")
    assert (await config.load("agent")).data == {"x": 1}


async def test_the_engine_fake_scripts_an_error_event() -> None:
    engine = FakeEngine(
        events=[
            Start(request_id="req-1"),
            Error(code="engine_error", message="scripted", retryable=True),
        ]
    )
    request = make_request()
    events = [e async for e in engine.run(request, make_context(request))]
    assert events[-1] == Error(code="engine_error", message="scripted", retryable=True)
    healthy = FakeEngine(events=[Start(request_id="req-1"), End()])
    assert [e.type async for e in healthy.run(request, make_context(request))] == ["start", "end"]


async def test_the_engine_fake_scripts_a_failed_probe() -> None:
    engine = FakeEngine(events=[])
    engine.healthy = False
    assert await engine.probe() is False
    engine.healthy = True
    assert await engine.probe() is True


async def test_the_auth_fake_scripts_an_outage() -> None:
    principal = Principal(subject="user-1", scopes=frozenset())
    auth = StaticTokens({"t": principal})
    auth.unavailable = True
    with pytest.raises(AuthError) as info:
        await auth.authenticate("t")
    assert (info.value.code, info.value.retryable) == ("auth_unavailable", True)
    auth.unavailable = False
    assert await auth.authenticate("t") == principal


async def test_the_guardrail_fake_scripts_an_outage() -> None:
    guard = KeywordGuardrails()
    guard.unavailable = True
    with pytest.raises(GuardrailError) as info:
        await guard.check("hello", "input")
    assert (info.value.code, info.value.retryable) == ("guardrail_unavailable", True)
    guard.unavailable = False
    assert (await guard.check("hello", "input")).allowed


async def test_the_evaluator_fake_scripts_an_outage() -> None:
    evaluator = ScriptedEvaluator()
    evaluator.unavailable = True
    case = EvalCase(input="q", output="a")
    with pytest.raises(EvaluatorError) as info:
        await evaluator.score(case)
    assert (info.value.code, info.value.retryable) == ("evaluator_unavailable", True)
    evaluator.unavailable = False
    assert (await evaluator.score(case)).value == 1.0


async def test_the_feedback_fake_scripts_an_outage() -> None:
    feedback = InMemoryFeedback()
    feedback.unavailable = True
    record = make_feedback()
    with pytest.raises(FeedbackError) as info:
        await feedback.submit(record)
    assert (info.value.code, info.value.retryable) == ("feedback_unavailable", True)
    feedback.unavailable = False
    await feedback.submit(record)
    assert feedback.get(record.feedback_id) == record
