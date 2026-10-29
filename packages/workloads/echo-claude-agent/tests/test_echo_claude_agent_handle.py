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


async def test_handle_streams_start_delta_metrics_end_when_the_smoke_task_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The smoke task: one reply gives start, delta, metrics, end with the usage and route."""
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


async def test_handle_keeps_the_facts_and_the_input_apart_when_the_simplifier_task_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The simplifier task: the facts survive, and the input is the prompt, not the system one."""
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


async def test_handle_passes_the_lookup_task_when_the_cli_replays_its_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lookup task: both MCP tool calls come out in order with their results, then the text."""
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


async def test_handle_keeps_builtin_tool_names_and_wraps_text_results_when_bash_and_read_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Built-in tools keep their names; a text result is `{"text"}`, an error one `{"error"}`."""
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


async def test_handle_streams_finer_deltas_without_duplicates_when_partial_messages_arrive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream chunks become the deltas; the full message after them adds no second copy."""

    def chunk(text: str) -> StreamEvent:
        delta = {"type": "text_delta", "text": text}
        event = {"type": "content_block_delta", "index": 0, "delta": delta}
        return StreamEvent(uuid="u", session_id="s-1", event=event)

    messages = [chunk("Plain "), chunk("words."), say("Plain words."), result()]
    install(monkeypatch, FakeQuery(messages))

    events = await run()

    check_shape(events)
    assert [e["text"] for e in events if e["type"] == "delta"] == ["Plain ", "words."]


async def test_handle_still_reports_a_tool_call_when_its_result_never_comes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool call with no result before the end is still one `tool_call`, with an error result."""
    install(monkeypatch, FakeQuery([use("tu_1", "Bash", command="sleep 9"), result()]))

    events = await run()

    check_shape(events)
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["call_id"] == "tu_1" and "error" in call["result"]


# --- the options and env the code builds ---------------------------------------------------


async def options_with_chassis_env(monkeypatch: pytest.MonkeyPatch) -> ClaudeAgentOptions:
    """Run once with the chassis URLs and token set and route `local-small`; return the options."""
    monkeypatch.setenv("CHASSIS_MODEL_URL", "http://chassis:8090/v1/")
    monkeypatch.setenv("CHASSIS_TOOL_URL", "http://chassis:8090/mcp")
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))
    await run(ctx={**CTX, "model_route": "local-small"})
    return fake.options


async def test_handle_points_the_cli_at_the_chassis_model_proxy_when_the_chassis_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI env gets the model URL without `/v1`, the route as both models, the traceparent."""
    opts = await options_with_chassis_env(monkeypatch)

    env = opts.env

    assert env["ANTHROPIC_BASE_URL"] == "http://chassis:8090"  # the CLI adds /v1/messages
    assert env["ANTHROPIC_MODEL"] == env["ANTHROPIC_SMALL_FAST_MODEL"] == "local-small"
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == f"traceparent: {TRACEPARENT}"


async def test_handle_gives_the_cli_the_chassis_token_when_chassis_api_token_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chassis token is the CLI's auth token."""
    opts = await options_with_chassis_env(monkeypatch)

    env = opts.env

    assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN


@pytest.mark.parametrize(
    "flag",
    [
        "DISABLE_TELEMETRY",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "DISABLE_AUTOUPDATER",
        "DISABLE_ERROR_REPORTING",
    ],
)
async def test_handle_sets_a_cli_traffic_off_flag_when_it_builds_the_env(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """Each CLI telemetry, update, and error-report switch is set to `1`."""
    opts = await options_with_chassis_env(monkeypatch)

    env = opts.env

    assert env[flag] == "1"


async def test_handle_puts_the_cli_home_under_the_run_base_when_it_builds_the_env(
    monkeypatch: pytest.MonkeyPatch, clean_env: Path
) -> None:
    """The CLI's `HOME` is a child of `CLAUDE_AGENT_HOME_BASE`."""
    opts = await options_with_chassis_env(monkeypatch)

    env = opts.env

    assert Path(env["HOME"]).parent == clean_env


async def test_handle_gives_the_mcp_client_the_tool_url_and_both_headers_when_the_token_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One MCP server, `chassis`: the tool URL, the traceparent, and the token as a Bearer."""
    opts = await options_with_chassis_env(monkeypatch)

    servers: Any = opts.mcp_servers

    assert set(servers) == {"chassis"}
    assert servers["chassis"] == {
        "type": "http",
        "url": "http://chassis:8090/mcp",
        "headers": {"traceparent": TRACEPARENT, "Authorization": f"Bearer {TOKEN}"},
    }


async def test_handle_limits_turns_tools_and_prompts_when_it_builds_the_cli_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four turns at most, the shell, file, and chassis tools allowed, and no permission prompt."""
    opts = await options_with_chassis_env(monkeypatch)

    allowed = opts.allowed_tools

    assert opts.max_turns == 4
    assert {"Bash", "Read", "Write"} <= set(allowed)
    assert "mcp__chassis" in allowed
    assert opts.permission_mode == "dontAsk"  # headless, no prompt; only the allowed tools run


async def test_handle_replaces_the_system_prompt_and_asks_for_partials_when_it_builds_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The system prompt is ours in full, and partial messages are on for finer deltas."""
    opts = await options_with_chassis_env(monkeypatch)

    system_prompt = opts.system_prompt

    assert system_prompt == SYSTEM_PROMPT  # replaces the Claude Code prompt, not appended
    assert opts.include_partial_messages is True


async def test_handle_runs_the_cli_inside_its_home_when_it_builds_the_cli_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI's working directory is set and sits under its `HOME`."""
    opts = await options_with_chassis_env(monkeypatch)

    cwd = opts.cwd

    assert cwd is not None and Path(str(cwd)).is_relative_to(Path(opts.env["HOME"]))


async def test_handle_uses_local_defaults_and_a_placeholder_token_when_no_token_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No chassis env and no traceparent: local URLs, a placeholder token, no headers."""
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


async def test_handle_keeps_the_model_url_as_is_when_it_has_no_v1_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model URL without `/v1` only loses its trailing slash."""
    monkeypatch.setenv("CHASSIS_MODEL_URL", "http://proxy:9/")
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))

    await run()

    assert fake.options.env["ANTHROPIC_BASE_URL"] == "http://proxy:9"


async def test_handle_sends_each_runs_own_traceparent_when_two_runs_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs with two traceparents: each run's CLI and MCP headers carry its own."""
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))
    other = "00-11111111111111111111111111111111-2222222222222222-01"

    await run(ctx={**CTX, "traceparent": TRACEPARENT})
    await run(ctx={**CTX, "traceparent": other})

    first, second = (c["options"] for c in fake.calls)
    assert first.env["ANTHROPIC_CUSTOM_HEADERS"].endswith(TRACEPARENT)
    assert second.env["ANTHROPIC_CUSTOM_HEADERS"].endswith(other)
    assert second.mcp_servers["chassis"]["headers"]["traceparent"] == other


async def test_handle_gives_each_run_a_fresh_home_and_removes_it_when_the_run_ends(
    monkeypatch: pytest.MonkeyPatch, clean_env: Path
) -> None:
    """Two runs get two homes, and neither survives its run."""
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))

    await run()
    await run()

    (home1, _), (home2, _) = fake.run_dirs
    assert home1 != home2
    assert not home1.exists() and not home2.exists()
    assert _entries(clean_env) == []  # nothing survives between runs


async def test_handle_removes_the_home_and_closes_the_query_when_the_run_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that raises still removes its home and closes the SDK generator."""
    fake = install(monkeypatch, FakeQuery(raises=RuntimeError("boom")))

    events = await run()

    assert events[-1]["type"] == "error"
    assert not fake.run_dirs[0][0].exists() and fake.closed


# --- error codes ---------------------------------------------------------------------------


async def test_handle_emits_a_final_http_401_error_when_the_result_has_status_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error result with API status 401 is one `http_401` error, not retryable."""
    bad = result(is_error=True, api_error_status=401, result="API Error: 401 unauthorized")
    install(monkeypatch, FakeQuery([bad]))

    events = await run()

    check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[1]["code"] == "http_401" and events[1]["retryable"] is False
    assert "401" in events[1]["message"]


async def test_handle_emits_a_retryable_http_error_when_the_result_has_a_5xx_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error result with API status 529 is a retryable `http_529`."""
    install(monkeypatch, FakeQuery([result(is_error=True, api_error_status=529, result="busy")]))

    events = await run()

    assert events[-1]["code"] == "http_529" and events[-1]["retryable"] is True


async def test_handle_emits_model_error_when_an_error_result_has_no_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error result with no API status is `model_error` with the result's error text."""
    bad = result(subtype="error_during_execution", is_error=True, errors=["it broke"])
    install(monkeypatch, FakeQuery([bad]))

    events = await run()

    check_shape(events)
    assert events[-1]["code"] == "model_error" and "it broke" in events[-1]["message"]


async def test_handle_emits_tool_loop_exceeded_without_metrics_when_the_result_hits_max_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `max_turns` result is a final `tool_loop_exceeded`, with no `metrics` event."""
    over = result(
        subtype="error_max_turns", is_error=True, terminal_reason="max_turns", num_turns=5
    )
    install(monkeypatch, FakeQuery([use("a", "Bash", command="x"), returned("a", {}), over]))

    events = await run()

    check_shape(events)
    assert events[-1]["code"] == "tool_loop_exceeded" and events[-1]["retryable"] is False
    assert "metrics" not in [e["type"] for e in events]


async def test_handle_emits_tool_loop_exceeded_when_the_sdk_raises_a_max_turns_result_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `ResultError` for `max_turns` is `tool_loop_exceeded` too."""
    data = {"subtype": "error_max_turns", "terminal_reason": "max_turns", "is_error": True}
    install(monkeypatch, FakeQuery(raises=ResultError("max turns", data=data, exit_code=1)))

    events = await run()

    assert events[-1]["code"] == "tool_loop_exceeded"


async def test_handle_emits_the_http_status_when_the_sdk_raises_a_result_error_with_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `ResultError` with API status 500 is a retryable `http_500`."""
    data = {"subtype": "success", "is_error": True, "api_error_status": 500, "result": "API Error"}
    install(monkeypatch, FakeQuery(raises=ResultError("x", data=data, exit_code=1)))

    events = await run()

    assert events[-1]["code"] == "http_500" and events[-1]["retryable"] is True


@pytest.mark.parametrize(
    "exc", [CLINotFoundError("no cli"), ProcessError("died", exit_code=137, stderr="killed")]
)
async def test_handle_emits_cli_error_when_the_cli_will_not_start_or_crashes(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    """A missing CLI or a CLI that dies is one `cli_error` right after `start`."""
    install(monkeypatch, FakeQuery(raises=exc))

    events = await run()

    check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "cli_error"


async def test_handle_emits_model_error_and_never_raises_when_an_unexpected_exception_occurs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any other exception mid-run is a `model_error` event, never raised out of `handle`."""
    install(monkeypatch, FakeQuery([say("partial")], raises=ValueError("odd")))

    events = await run()

    check_shape(events)
    assert events[-1]["code"] == "model_error" and "odd" in events[-1]["message"]


async def test_handle_emits_a_retryable_timeout_and_stops_the_cli_when_the_budget_runs_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run past `budget.timeout_ms` is a retryable `timeout`; the CLI stops, the home goes."""
    fake = install(monkeypatch, FakeQuery([say("slow")], hang=True))

    events = await run(ctx={**CTX, "budget": {"timeout_ms": 100}})

    check_shape(events)
    assert events[-1]["code"] == "timeout" and events[-1]["retryable"] is True
    assert fake.closed, "the SDK generator was closed, so the CLI process is stopped"
    assert not fake.run_dirs[0][0].exists()


async def test_handle_scrubs_the_token_from_the_error_when_the_cli_stderr_holds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI failure whose stderr shows the token: no event holds the token."""
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    install(monkeypatch, FakeQuery(raises=ProcessError("failed", stderr=f"saw {TOKEN} here")))

    events = await run()

    assert TOKEN not in json.dumps(events)


async def test_handle_scrubs_the_token_from_every_event_when_bash_prints_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Bash `env` puts the token in a tool result and the model echoes it: no event holds it."""
    monkeypatch.setenv("CHASSIS_API_TOKEN", "t0k")
    delta = {"type": "text_delta", "text": "token t0k"}
    chunk = {"type": "content_block_delta", "index": 0, "delta": delta}
    messages = [
        use("tu_1", "Bash", command="env; echo t0k"),
        UserMessage(content=[ToolResultBlock("tu_1", "ANTHROPIC_AUTH_TOKEN=t0k\n", False)]),
        use("tu_2", "mcp__chassis__glossary_lookup", term="t0k"),
        returned("tu_2", {"term": "t0k", "seen": ["t0k"]}),
        StreamEvent(uuid="u", session_id="s-1", event=chunk),
        say("token t0k"),
        result(),
    ]
    install(monkeypatch, FakeQuery(messages))

    events = await run()

    check_shape(events)
    assert "t0k" not in json.dumps(events)
    assert [e["type"] for e in events].count("tool_call") == 2


class StderrQuery(FakeQuery):
    """A `FakeQuery` whose CLI writes lines to stderr before the scripted messages."""

    def __init__(self, lines: Iterable[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.lines = list(lines)

    async def _run(self, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        assert options.stderr is not None
        for line in self.lines:
            options.stderr(line)
        async for message in super()._run(options):
            yield message


async def test_handle_cuts_the_cli_error_message_after_the_stderr_tail_is_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI that dies with three long stderr lines: the `error` message is 500 chars at most."""
    lines = [c * 400 for c in "xyz"]
    install(monkeypatch, StderrQuery(lines, raises=ProcessError("died", exit_code=1)))

    events = await run()

    check_shape(events)
    assert events[-1]["code"] == "cli_error"
    assert len(events[-1]["message"]) <= 500


# --- the environment guard -----------------------------------------------------------------


async def test_handle_refuses_to_start_the_cli_when_an_unlisted_claude_variable_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlisted `ANTHROPIC_*` and `CLAUDE_*` names: `env_not_clean` names them, shows no value."""
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


async def test_handle_starts_and_sets_its_own_values_when_only_overridden_names_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Names the run sets itself pass the guard, and the run's values replace the inherited ones."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://elsewhere")  # overridden per run
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "overridden")  # overridden per run
    fake = install(monkeypatch, FakeQuery([say("ok"), result()]))

    events = await run()

    assert events[-1]["type"] == "end"
    assert fake.options.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8090"
    assert fake.options.env["ANTHROPIC_AUTH_TOKEN"] != "overridden"


async def test_query_fn_is_the_sdk_query_when_no_test_replaces_it() -> None:
    """The test hook defaults to the SDK's own `query`."""
    import claude_agent_sdk

    hook = handle_module.query_fn

    assert hook is claude_agent_sdk.query


async def test_handle_ends_ok_when_the_cli_exits_non_zero_after_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late exit error after the result changes nothing: the result already counts."""
    install(monkeypatch, FakeQuery([say("ok"), result()], raises=ProcessError("exit", exit_code=1)))

    events = await run()

    check_shape(events)
    assert [e["type"] for e in events] == ["start", "delta", "metrics", "end"]


async def test_handle_scrubs_the_token_before_the_cut_when_it_spans_char_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI error with the token across char 500: it is scrubbed on the full text, then cut."""
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    install(monkeypatch, FakeQuery(raises=ProcessError("x" * 495 + TOKEN + " tail")))

    events = await run()

    check_shape(events)
    message = events[-1]["message"]
    assert events[-1]["code"] == "cli_error"
    assert TOKEN[:5] not in message
    assert len(message) <= 500


async def test_handle_scrubs_the_token_when_it_is_split_across_delta_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two stream chunks that split the token: the joined delta text never rebuilds it."""
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)

    def chunk(text: str) -> StreamEvent:
        delta = {"type": "text_delta", "text": text}
        event = {"type": "content_block_delta", "index": 0, "delta": delta}
        return StreamEvent(uuid="u", session_id="s-1", event=event)

    full = f"see {TOKEN} now"
    messages = [chunk(full[:10]), chunk(full[10:]), say(full), result()]
    install(monkeypatch, FakeQuery(messages))

    events = await run()

    check_shape(events)
    assert TOKEN not in json.dumps(events)
    assert text_of(events) == full.replace(TOKEN, "[redacted]")
    kinds = [e["type"] for e in events]
    assert kinds.index("metrics") > max(i for i, k in enumerate(kinds) if k == "delta")
