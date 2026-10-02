"""ToolPortContract: the tool list is stable and well formed, a listed tool answers, an unknown
tool and bad arguments raise `ToolError` with their codes, and a read-only tool repeats.

PoC-5 write mode (plan section 2.6), each case skipped when the binding does not provide its
fixture: a write tool without a key is `idempotency_key_required` and has no effect; the same key
twice is one effect and the same result; two keys are two effects; a tool outside the allow-list
is not listed and its call raises `unknown_tool` or `tool_denied`; an unavailable port raises
`tool_unavailable` with `retryable=True`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from chassis.ports.tool import ToolError, ToolPort, ToolResult

DENIED_CODES = frozenset({"unknown_tool", "tool_denied"})
"""A call outside the allow-list raises one of these: whichever the gateway gives (section 2.6)."""


class KnownCall:
    """A call the port must answer: a listed tool and valid arguments for it."""

    def __init__(self, name: str, arguments: Mapping[str, Any]) -> None:
        self.name = name
        self.arguments = dict(arguments)


@pytest.mark.contract
class ToolPortContract:
    """Subclass as `Test*`, provide `tool_port` and `known_call`. Override `bad_arguments` when
    `{}` is valid for the known tool.

    Write mode, optional: `write_call` (a listed write tool and valid arguments; its result must
    differ per effect, for example a fresh note id), `denied_name` (a tool that exists behind the
    port but is outside this credential's allow-list; called with `{}`), `count_effects` (how
    many effects the write tool has had so far), `make_unavailable` (makes the next call fail as
    unavailable). A case whose fixture is not provided is skipped.
    """

    @pytest.fixture
    def tool_port(self) -> ToolPort:
        raise NotImplementedError("provide a tool_port fixture")

    @pytest.fixture
    def known_call(self) -> KnownCall:
        raise NotImplementedError("provide a known_call fixture: a listed tool and valid args")

    @pytest.fixture
    def bad_arguments(self) -> dict[str, Any]:
        """Arguments the known tool must refuse. `{}` fails any tool with a required field."""
        return {}

    def test_list_is_stable_and_well_formed(self, tool_port: ToolPort) -> None:
        first = list(tool_port.list_tools())
        assert first, "a tool port that lists nothing cannot be checked"
        assert first == list(tool_port.list_tools())
        names = [d.name for d in first]
        assert len(names) == len(set(names)), "tool names must be unique"
        for definition in first:
            assert definition.name and definition.description
            assert definition.parameters.get("type") == "object"

    async def test_known_call_answers(self, tool_port: ToolPort, known_call: KnownCall) -> None:
        assert known_call.name in [d.name for d in tool_port.list_tools()]
        result = await tool_port.call(known_call.name, known_call.arguments)
        assert isinstance(result, ToolResult)
        assert result.is_error is False

    async def test_unknown_tool_raises(self, tool_port: ToolPort) -> None:
        with pytest.raises(ToolError) as info:
            await tool_port.call("no_such_tool_ever", {})
        assert info.value.code == "unknown_tool"

    async def test_bad_arguments_raise(
        self, tool_port: ToolPort, known_call: KnownCall, bad_arguments: dict[str, Any]
    ) -> None:
        with pytest.raises(ToolError) as info:
            await tool_port.call(known_call.name, bad_arguments)
        assert info.value.code == "bad_arguments"

    async def test_read_only_tool_repeats(self, tool_port: ToolPort, known_call: KnownCall) -> None:
        (definition,) = [d for d in tool_port.list_tools() if d.name == known_call.name]
        if not definition.read_only:
            pytest.skip(f"{known_call.name} is not read-only")
        first = await tool_port.call(known_call.name, known_call.arguments)
        second = await tool_port.call(known_call.name, known_call.arguments)
        assert first == second

    # Write mode (PoC-5). Optional fixtures: skipped when the binding does not override them.

    @pytest.fixture
    def write_call(self) -> KnownCall:
        pytest.skip("provide a write_call fixture: a listed write tool and valid args")

    @pytest.fixture
    def denied_name(self) -> str:
        pytest.skip("provide a denied_name fixture: a tool outside the allow-list")

    @pytest.fixture
    def count_effects(self) -> Callable[[], int] | None:
        """How many effects the write tool has had. None: the binding cannot count them, and the
        cases compare results only."""
        return None

    @pytest.fixture
    def make_unavailable(self) -> Callable[[], None]:
        pytest.skip("provide a make_unavailable fixture: the next call fails as unavailable")

    @pytest.fixture
    def other_write_arguments(self) -> dict[str, Any]:
        pytest.skip("provide other_write_arguments: valid write_call arguments of another value")

    @staticmethod
    def _fresh_key() -> str:
        """A key no earlier run used, so a shared real tool server never dedups across tests."""
        return f"contract-{uuid.uuid4().hex}"

    def test_write_tool_is_listed_as_write(
        self, tool_port: ToolPort, write_call: KnownCall
    ) -> None:
        (definition,) = [d for d in tool_port.list_tools() if d.name == write_call.name]
        assert definition.read_only is False

    async def test_write_without_key_is_refused_with_no_effect(
        self,
        tool_port: ToolPort,
        write_call: KnownCall,
        count_effects: Callable[[], int] | None,
    ) -> None:
        before = count_effects() if count_effects is not None else None
        with pytest.raises(ToolError) as info:
            await tool_port.call(write_call.name, write_call.arguments)
        assert info.value.code == "idempotency_key_required"
        assert info.value.retryable is False
        if count_effects is not None:
            assert count_effects() == before

    async def test_same_key_twice_is_one_effect_and_the_same_result(
        self,
        tool_port: ToolPort,
        write_call: KnownCall,
        count_effects: Callable[[], int] | None,
    ) -> None:
        key = self._fresh_key()
        before = count_effects() if count_effects is not None else None
        first = await tool_port.call(write_call.name, write_call.arguments, idempotency_key=key)
        second = await tool_port.call(write_call.name, write_call.arguments, idempotency_key=key)
        assert first.is_error is False
        assert first == second
        if count_effects is not None and before is not None:
            assert count_effects() == before + 1

    async def test_a_reused_key_with_other_arguments_returns_the_first_result(
        self,
        tool_port: ToolPort,
        write_call: KnownCall,
        other_write_arguments: dict[str, Any],
        count_effects: Callable[[], int] | None,
    ) -> None:
        """Plan section 2.6: at most one effect per key; a second call with the same key returns
        the first result, whatever its arguments. The derived key covers the arguments, so this
        happens only on a collision, but every tool server must agree on it."""
        assert dict(other_write_arguments) != dict(write_call.arguments)
        key = self._fresh_key()
        before = count_effects() if count_effects is not None else None
        first = await tool_port.call(write_call.name, write_call.arguments, idempotency_key=key)
        again = await tool_port.call(write_call.name, other_write_arguments, idempotency_key=key)
        assert first.is_error is False
        assert again == first
        if count_effects is not None and before is not None:
            assert count_effects() == before + 1

    async def test_two_keys_are_two_effects(
        self,
        tool_port: ToolPort,
        write_call: KnownCall,
        count_effects: Callable[[], int] | None,
    ) -> None:
        before = count_effects() if count_effects is not None else None
        first = await tool_port.call(
            write_call.name, write_call.arguments, idempotency_key=self._fresh_key()
        )
        second = await tool_port.call(
            write_call.name, write_call.arguments, idempotency_key=self._fresh_key()
        )
        assert first.is_error is False and second.is_error is False
        assert first != second, "the write tool's result must differ per effect"
        if count_effects is not None and before is not None:
            assert count_effects() == before + 2

    async def test_a_tool_outside_the_allow_list_is_not_listed_and_not_called(
        self, tool_port: ToolPort, denied_name: str
    ) -> None:
        assert denied_name not in [d.name for d in tool_port.list_tools()]
        with pytest.raises(ToolError) as info:
            await tool_port.call(denied_name, {})
        assert info.value.code in DENIED_CODES
        assert info.value.retryable is False

    async def test_unavailable_is_retryable(
        self,
        tool_port: ToolPort,
        known_call: KnownCall,
        make_unavailable: Callable[[], None],
    ) -> None:
        make_unavailable()
        with pytest.raises(ToolError) as info:
            await tool_port.call(known_call.name, known_call.arguments)
        assert info.value.code == "tool_unavailable"
        assert info.value.retryable is True
