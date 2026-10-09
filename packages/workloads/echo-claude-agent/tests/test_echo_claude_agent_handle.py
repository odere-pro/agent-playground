"""`handle` with the SDK's `query` replaced by a scripted fake that yields SDK message objects
built with the SDK's own types. No CLI starts, no socket opens, no key is read. The event schema
is read from its file by path; the chassis is never imported.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk._errors import ResultError
from echo_claude_agent import PROMPT_VERSION, SYSTEM_PROMPT, handle

# `echo_claude_agent.handle` the attribute is the function; the module holds the test hook.
handle_module: Any = importlib.import_module("echo_claude_agent.handle")

ROOT = Path(__file__).resolve().parents[4]
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
TOKEN = "tok-remote-1"  # pragma: allowlist secret  (a fixture value, not a credential)

CTX: dict[str, Any] = {
    "request_id": "req-1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "model_route": "big-default",
    "traceparent": TRACEPARENT,
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The process under test may hold a live session's variables: remove every one, and give the
    run dirs a base of their own."""
    for name in list(os.environ):
        if name.startswith(("ANTHROPIC_", "CLAUDE", "CHASSIS_")):
            monkeypatch.delenv(name)
    base = tmp_path / "base"
    monkeypatch.setenv("CLAUDE_AGENT_HOME_BASE", str(base))
    return base


def result(**over: Any) -> ResultMessage:
    fields: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 120,
        "duration_api_ms": 100,
        "is_error": False,
        "num_turns": 1,
        "session_id": "s-1",
        "usage": {"input_tokens": 40, "output_tokens": 9},
    }
    fields.update(over)
    return ResultMessage(**fields)


def say(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text)], model="big-default")


def use(call_id: str, name: str, **args: Any) -> AssistantMessage:
    return AssistantMessage(content=[ToolUseBlock(call_id, name, args)], model="big-default")


def returned(call_id: str, payload: dict[str, Any], *, is_error: bool = False) -> UserMessage:
    block = ToolResultBlock(call_id, [{"type": "text", "text": json.dumps(payload)}], is_error)
    return UserMessage(content=[block])


def _is_dir(path: Path) -> bool:
    return path.is_dir()


def _entries(path: Path) -> list[Path]:
    return list(path.iterdir())


class FakeQuery:
    """Stands in for the SDK's `query`: records its arguments, yields scripted messages."""

    def __init__(
        self,
        messages: Iterable[Any] = (),
        *,
        raises: BaseException | None = None,
        hang: bool = False,
    ) -> None:
        self.messages = list(messages)
        self.raises = raises
        self.hang = hang
        self.calls: list[dict[str, Any]] = []
        self.run_dirs: list[tuple[Path, Path]] = []
        self.closed = False

    def __call__(self, *, prompt: Any, options: ClaudeAgentOptions | None = None) -> Any:
        assert options is not None
        self.calls.append({"prompt": prompt, "options": options})
        return self._run(options)

    async def _run(self, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        home, cwd = Path(options.env["HOME"]), Path(str(options.cwd))
        assert _is_dir(home) and _is_dir(cwd) and cwd.is_relative_to(home)
        self.run_dirs.append((home, cwd))
        try:
            for message in self.messages:
                yield message
            if self.hang:
                await asyncio.sleep(3600)
            if self.raises is not None:
                raise self.raises
        finally:
            self.closed = True

    @property
    def options(self) -> ClaudeAgentOptions:
        options: ClaudeAgentOptions = self.calls[-1]["options"]
        return options


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeQuery) -> FakeQuery:
    monkeypatch.setattr(handle_module, "query_fn", fake)
    return fake


async def run(
    text: str = "simplify: Hello.", ctx: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [e async for e in handle({"text": text, "data": {}}, CTX if ctx is None else ctx)]


def check_shape(events: list[dict[str, Any]]) -> None:
    for event in events:
        jsonschema.validate(event, EVENTS_SCHEMA)
        assert event["schema_version"] == "0"
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds.count("start") == 1
    assert kinds[-1] in ("end", "error")
    assert kinds.count("end") + kinds.count("error") == 1


def text_of(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")


# --- the three tasks -----------------------------------------------------------------------


async def test_smoke_task(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeQuery([say("Hello."), result()]))
    events = await run("simplify: Hello.")
    check_shape(events)
    kinds = [e["type"] for e in events]
    assert kinds == ["start", "delta", "metrics", "end"]
    assert events[0]["request_id"] == "req-1"
    assert events[-1]["status"] == "ok"
    assert (events[2]["input_tokens"], events[2]["output_tokens"]) == (40, 9)
    assert events[2]["model_route"] == "big-default" and events[2]["attempt"] == 1
    assert fake.calls[0]["prompt"] == "simplify: Hello."


async def test_simplifier_task(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = "Acme put out the SLM in 2026. It cut costs by 30 percent."
    fake = install(monkeypatch, FakeQuery([say(reply), result()]))
    text = "simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent."
    events = await run(text)
    check_shape(events)
    out = text_of(events)
    assert "2026" in out and "Acme" in out and "30" in out
    # The input is the prompt; the fixed instruction is the system prompt, and they stay apart.
    assert fake.calls[0]["prompt"] == text
    assert fake.options.system_prompt == SYSTEM_PROMPT
    assert text not in SYSTEM_PROMPT and PROMPT_VERSION == "simplifier-v1"


async def test_lookup_task(monkeypatch: pytest.MonkeyPatch) -> None:
    glossary = {"term": "SLM", "definition": "small language model"}
    acronym = {"acronym": "RAG", "expansion": "retrieval-augmented generation"}
    messages = [
        use("tu_1", "mcp__chassis__glossary_lookup", term="SLM"),
        returned("tu_1", glossary),
        use("tu_2", "mcp__chassis__acronym_expand", acronym="RAG"),
        returned("tu_2", acronym),
        say("SLM means a small language model. RAG stands for retrieval-augmented generation."),
        result(num_turns=3),
    ]
    install(monkeypatch, FakeQuery(messages))
    events = await run("lookup: Define SLM and expand RAG.")
    check_shape(events)
    calls = [e for e in events if e["type"] == "tool_call"]
    assert [(c["name"], c["arguments"]) for c in calls] == [
        ("glossary_lookup", {"term": "SLM"}),
        ("acronym_expand", {"acronym": "RAG"}),
    ]
    assert [c["call_id"] for c in calls] == ["tu_1", "tu_2"]
    assert [c["result"] for c in calls] == [glossary, acronym]
    kinds = [e["type"] for e in events]
    assert kinds == ["start", "tool_call", "tool_call", "delta", "metrics", "end"]
    out = text_of(events)
    assert "small language model" in out and "retrieval-augmented generation" in out


# --- mapping details -----------------------------------------------------------------------


async def test_builtin_tool_names_keep_their_name_and_text_results_are_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        use("tu_1", "Bash", command="echo hi"),
        UserMessage(content=[ToolResultBlock("tu_1", "hi\n", False)]),
        use("tu_2", "Read", file_path="/nope"),
        UserMessage(content=[ToolResultBlock("tu_2", "no such file", True)]),
        say("done"),
        result(),
    ]
    install(monkeypatch, FakeQuery(messages))
    events = await run()
    check_shape(events)
    calls = [e for e in events if e["type"] == "tool_call"]
    assert [c["name"] for c in calls] == ["Bash", "Read"]
    assert calls[0]["arguments"] == {"command": "echo hi"}
    assert calls[0]["result"] == {"text": "hi\n"}
    assert calls[1]["result"] == {"error": "no such file"}


async def test_partial_messages_stream_finer_deltas_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def chunk(text: str) -> StreamEvent:
        delta = {"type": "text_delta", "text": text}
        event = {"type": "content_block_delta", "index": 0, "delta": delta}
        return StreamEvent(uuid="u", session_id="s-1", event=event)

    messages = [chunk("Plain "), chunk("words."), say("Plain words."), result()]
    install(monkeypatch, FakeQuery(messages))
    events = await run()
    check_shape(events)
    assert [e["text"] for e in events if e["type"] == "delta"] == ["Plain ", "words."]


async def test_a_call_without_a_result_is_still_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeQuery([use("tu_1", "Bash", command="sleep 9"), result()]))
    events = await run()
    check_shape(events)
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["call_id"] == "tu_1" and "error" in call["result"]


# --- the options and env the code builds ---------------------------------------------------


async def test_options_and_env(monkeypatch: pytest.MonkeyPatch, clean_env: Path) -> None:
    monkeypatch.setenv("CHASSIS_MODEL_URL", "http://chassis:8090/v1/")
    monkeypatch.setenv("CHASSIS_TOOL_URL", "http://chassis:8090/mcp")
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))
    await run(ctx={**CTX, "model_route": "local-small"})
    opts = fake.options
    env = opts.env
    assert env["ANTHROPIC_BASE_URL"] == "http://chassis:8090"  # the CLI adds /v1/messages
    assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
    assert env["ANTHROPIC_MODEL"] == env["ANTHROPIC_SMALL_FAST_MODEL"] == "local-small"
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == f"traceparent: {TRACEPARENT}"
    for flag in (
        "DISABLE_TELEMETRY",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "DISABLE_AUTOUPDATER",
        "DISABLE_ERROR_REPORTING",
    ):
        assert env[flag] == "1"
    assert Path(env["HOME"]).parent == clean_env
    servers: Any = opts.mcp_servers
    assert set(servers) == {"chassis"}
    assert servers["chassis"] == {
        "type": "http",
        "url": "http://chassis:8090/mcp",
        "headers": {"traceparent": TRACEPARENT, "Authorization": f"Bearer {TOKEN}"},
    }
    assert opts.max_turns == 4
    assert {"Bash", "Read", "Write"} <= set(opts.allowed_tools)
    assert "mcp__chassis" in opts.allowed_tools
    assert opts.permission_mode == "dontAsk"  # headless, no prompt; only the allowed tools run
    assert opts.system_prompt == SYSTEM_PROMPT  # replaces the Claude Code prompt, not appended
    assert opts.include_partial_messages is True
    assert opts.cwd is not None and Path(str(opts.cwd)).is_relative_to(Path(env["HOME"]))


async def test_defaults_without_token_or_traceparent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))
    await run(ctx={"request_id": "r", "model_route": None})
    env = fake.options.env
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8090"
    assert env["ANTHROPIC_MODEL"] == "big-default"
    assert env["ANTHROPIC_AUTH_TOKEN"] and env["ANTHROPIC_AUTH_TOKEN"] != TOKEN  # a placeholder
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == ""
    servers: Any = fake.options.mcp_servers
    assert servers["chassis"]["url"] == "http://127.0.0.1:8090/mcp"
    assert servers["chassis"]["headers"] == {}
    monkeypatch.setenv("CHASSIS_API_TOKEN", "")  # set but empty counts as unset
    await run(ctx={"request_id": "r"})
    servers = fake.options.mcp_servers
    assert fake.options.env["ANTHROPIC_AUTH_TOKEN"] != ""
    assert "Authorization" not in servers["chassis"]["headers"]


async def test_the_model_url_without_v1_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHASSIS_MODEL_URL", "http://proxy:9/")
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))
    await run()
    assert fake.options.env["ANTHROPIC_BASE_URL"] == "http://proxy:9"


async def test_traceparent_is_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))
    other = "00-11111111111111111111111111111111-2222222222222222-01"
    await run(ctx={**CTX, "traceparent": TRACEPARENT})
    await run(ctx={**CTX, "traceparent": other})
    first, second = (c["options"] for c in fake.calls)
    assert first.env["ANTHROPIC_CUSTOM_HEADERS"].endswith(TRACEPARENT)
    assert second.env["ANTHROPIC_CUSTOM_HEADERS"].endswith(other)
    assert second.mcp_servers["chassis"]["headers"]["traceparent"] == other


async def test_home_is_fresh_per_run_and_removed(
    monkeypatch: pytest.MonkeyPatch, clean_env: Path
) -> None:
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))
    await run()
    await run()
    (home1, _), (home2, _) = fake.run_dirs
    assert home1 != home2
    assert not home1.exists() and not home2.exists()
    assert _entries(clean_env) == []  # nothing survives between runs


async def test_home_is_removed_after_an_error_too(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeQuery(raises=RuntimeError("boom")))
    events = await run()
    assert events[-1]["type"] == "error"
    assert not fake.run_dirs[0][0].exists() and fake.closed


# --- error codes ---------------------------------------------------------------------------


async def test_http_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = result(is_error=True, api_error_status=401, result="API Error: 401 unauthorized")
    install(monkeypatch, FakeQuery([bad]))
    events = await run()
    check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[1]["code"] == "http_401" and events[1]["retryable"] is False
    assert "401" in events[1]["message"]


async def test_a_5xx_status_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeQuery([result(is_error=True, api_error_status=529, result="busy")]))
    events = await run()
    assert events[-1]["code"] == "http_529" and events[-1]["retryable"] is True


async def test_error_result_without_status_is_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = result(subtype="error_during_execution", is_error=True, errors=["it broke"])
    install(monkeypatch, FakeQuery([bad]))
    events = await run()
    check_shape(events)
    assert events[-1]["code"] == "model_error" and "it broke" in events[-1]["message"]


async def test_tool_loop_exceeded_by_result(monkeypatch: pytest.MonkeyPatch) -> None:
    over = result(
        subtype="error_max_turns", is_error=True, terminal_reason="max_turns", num_turns=5
    )
    install(monkeypatch, FakeQuery([use("a", "Bash", command="x"), returned("a", {}), over]))
    events = await run()
    check_shape(events)
    assert events[-1]["code"] == "tool_loop_exceeded" and events[-1]["retryable"] is False
    assert "metrics" not in [e["type"] for e in events]


async def test_tool_loop_exceeded_by_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"subtype": "error_max_turns", "terminal_reason": "max_turns", "is_error": True}
    install(monkeypatch, FakeQuery(raises=ResultError("max turns", data=data, exit_code=1)))
    events = await run()
    assert events[-1]["code"] == "tool_loop_exceeded"


async def test_result_error_exception_carries_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"subtype": "success", "is_error": True, "api_error_status": 500, "result": "API Error"}
    install(monkeypatch, FakeQuery(raises=ResultError("x", data=data, exit_code=1)))
    events = await run()
    assert events[-1]["code"] == "http_500" and events[-1]["retryable"] is True


@pytest.mark.parametrize(
    "exc", [CLINotFoundError("no cli"), ProcessError("died", exit_code=137, stderr="killed")]
)
async def test_cli_that_will_not_start_or_crashed(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    install(monkeypatch, FakeQuery(raises=exc))
    events = await run()
    check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "cli_error"


async def test_unexpected_exception_is_model_error_never_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, FakeQuery([say("partial")], raises=ValueError("odd")))
    events = await run()
    check_shape(events)
    assert events[-1]["code"] == "model_error" and "odd" in events[-1]["message"]


async def test_timeout_comes_from_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeQuery([say("slow")], hang=True))
    events = await run(ctx={**CTX, "budget": {"timeout_ms": 100}})
    check_shape(events)
    assert events[-1]["code"] == "timeout" and events[-1]["retryable"] is True
    assert fake.closed, "the SDK generator was closed, so the CLI process is stopped"
    assert not fake.run_dirs[0][0].exists()


async def test_the_error_message_never_holds_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    install(monkeypatch, FakeQuery(raises=ProcessError("failed", stderr=f"saw {TOKEN} here")))
    events = await run()
    assert TOKEN not in json.dumps(events)


# --- the environment guard -----------------------------------------------------------------


async def test_env_guard_refuses_to_start_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "value-one")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "value-two")
    fake = install(monkeypatch, FakeQuery([say("never"), result()]))
    events = await run()
    check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    error = events[1]
    assert error["code"] == "env_not_clean" and error["retryable"] is False
    assert "ANTHROPIC_API_KEY" in error["message"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" in error["message"]
    assert "value-one" not in error["message"] and "value-two" not in error["message"]
    assert fake.calls == [], "the CLI was never started"


async def test_env_guard_lets_the_names_the_run_overrides_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://elsewhere")  # overridden per run
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "overridden")  # overridden per run
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))
    events = await run()
    assert events[-1]["type"] == "end"
    assert fake.options.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8090"
    assert fake.options.env["ANTHROPIC_AUTH_TOKEN"] != "overridden"


async def test_the_default_hook_is_the_sdk_query() -> None:
    import claude_agent_sdk

    assert handle_module.query_fn is claude_agent_sdk.query


async def test_a_late_exit_error_after_the_result_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The CLI can exit non-zero after a result; the SDK then raises. The result already counts.
    install(monkeypatch, FakeQuery([say("ok"), result()], raises=ProcessError("exit", exit_code=1)))
    events = await run()
    check_shape(events)
    assert [e["type"] for e in events] == ["start", "delta", "metrics", "end"]
