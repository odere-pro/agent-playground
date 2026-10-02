"""ScriptedModel: it records what it receives, and a scripted tool loop ends. A rule with
`after_tool` answers only after a tool result; a rule without it never does; `default_reply` ends
the loop when nothing matches.
"""

from __future__ import annotations

import pytest
from chassis.fakes import ScriptedModel, ScriptRule
from chassis.ports.model import ModelMessage, ToolCallRequest

CALL = ToolCallRequest(call_id="c1", name="glossary_lookup", arguments={"term": "SLM"})
ASK = [ModelMessage(role="user", content="lookup SLM")]
LOOP = [
    *ASK,
    ModelMessage(role="assistant", tool_calls=[CALL]),
    ModelMessage(role="tool", tool_call_id="c1", content="SLM: small language model"),
]


def test_a_message_refuses_what_the_port_cannot_carry() -> None:
    with pytest.raises(ValueError, match="content"):
        ModelMessage(role="user")
    with pytest.raises(ValueError, match="content"):
        ModelMessage(role="assistant")
    with pytest.raises(ValueError, match="tool_call_id"):
        ModelMessage(role="tool", content="x")
    with pytest.raises(ValueError, match="tool_calls"):
        ModelMessage(role="user", content="x", tool_calls=[CALL])
    with pytest.raises(ValueError, match="tool_call_id"):
        ModelMessage(role="user", content="x", tool_call_id="c1")
    with pytest.raises(ValueError):
        ModelMessage(role="developer", content="x")
    with pytest.raises(ValueError):
        ModelMessage.model_validate({"role": "user", "content": "x", "refusal": None})
    message = ModelMessage(role="user", content="x")
    with pytest.raises(ValueError):
        message.content = "y"  # type: ignore[misc]


async def test_a_scripted_tool_loop_ends() -> None:
    model = ScriptedModel([ScriptRule(match="lookup", tool_call=CALL)], default_reply="done")
    first = await model.complete(ASK, route="r")
    assert first.tool_calls == [CALL]
    second = await model.complete(LOOP, route="r")
    assert (second.text, second.tool_calls) == ("done", [])
    assert model.calls == [ASK, LOOP]


async def test_after_tool_matches_the_tool_content_only_after_a_tool() -> None:
    model = ScriptedModel(
        [
            ScriptRule(match="lookup", tool_call=CALL),
            ScriptRule(after_tool=True, match="nothing here", reply="wrong"),
            ScriptRule(after_tool=True, match="small language", reply="An SLM is small."),
            ScriptRule(reply="catch-all"),
        ]
    )
    assert (await model.complete(LOOP, route="r")).text == "An SLM is small."
    streamed = [c async for c in model.stream(LOOP, route="r")]
    assert "".join(c.text for c in streamed) == "An SLM is small."
    assert not [c for c in streamed if c.tool_call]
    assert (await model.complete([ModelMessage(role="user", content="hi")], route="r")).text == (
        "catch-all"
    )
