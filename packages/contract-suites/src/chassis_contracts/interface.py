"""InterfaceContract: every public interface answers every engine the same way, in every lane, end
to end (PoC-3 open note, section 9; exit criteria 1 and 2).

The matrix is interface {native, openai, anthropic, mcp} x engine x mode {stream, complete} x
lane. Each cell runs a real chassis app under uvicorn on a Unix socket, so a stream reaches the
client frame by frame, as it would over TCP; nothing is buffered by an ASGI test transport. The
suite builds the clients itself, the way a user of each format would:

- native: a plain `httpx2` client on `POST /v1/run`;
- openai: the official `openai.AsyncOpenAI`, `model` set to the agent, a placeholder key, and only
  the base URL changed (its `http_client` is an `httpx2` client on the Unix socket);
- anthropic: the official `anthropic.AsyncAnthropic`, the same way;
- mcp: `fastmcp.Client` over streamable HTTP at `/v1/mcp`, calling the agent's one tool with the
  native body as its arguments.

`max_retries` stays at each SDK's default, except in the two tests about errors and conflicts,
where a retry would hide what the test looks for; each says so.

Two kinds of cell skip, each with its reason, never silently:

- MCP x stream: an MCP tool call answers once; FastMCP's OpenAPI tool buffers the HTTP answer.
- an engine not in `inprocess_engines` x `inprocess`: that lane imports a Python `handle` by path.

A binder subclasses the contract as `Test*` and provides:

- the ClassVars `engine_labels`, `lane_labels` (two or more), and `inprocess_engines`; optionally
  `expected_answers` (engine -> `ExpectedAnswer`) to pin each engine's answer;
- `chassis_for`, a class-scoped (or wider) fixture returning `chassis_for(target, lane)`: a
  context manager that yields a running `Chassis`. `target` is one of `engine_labels` or a handle
  path, `module:attribute`, which the suite uses for its own probe and error handles (`PROBE`,
  `ERROR_HANDLE`): `inprocess` loads the path, `sidecar` serves it with the workload template
  server on a Unix socket. On exit it stops what it started. The suite enters each
  `(target, lane)` once per class and shares it across tests (`ChassisPool`), so a cell costs a
  call, not a server start. The context manager is synchronous on purpose: every server runs in
  a thread of its own, so the suite needs no class-scoped event loop.
- optionally `logical`, the `Logical` request every engine cell sends.

`serve_on_unix_sockets` runs several ASGI apps (the public app and the proxy app, say) with
uvicorn on Unix sockets, on one event loop in one thread, for a binder's `chassis_for`.

The serving and client harness lives in `interface_harness.py`; every name it exports is
re-exported here, so a binder imports only `chassis_contracts.interface`.

Spec: docs/contracts/contract-v0.md and the PoC-3 open note, sections 2 to 9.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterator, Mapping
from typing import Any, ClassVar

import anthropic
import openai
import pytest

from chassis_contracts.inbound import Logical

# Binders import these names from `chassis_contracts.interface`; `__all__` re-exports them.
from chassis_contracts.interface_harness import (
    BASE_URL,
    CALL_TIMEOUT_S,
    HOLD_S,
    HOLD_TEXT,
    INTERFACES,
    MCP_PATH,
    MCP_STREAM_SKIP,
    PER_RUN_CTX,
    PLACEHOLDER_KEY,
    Answer,
    Chassis,
    ChassisFor,
    ChassisPool,
    ExpectedAnswer,
    UnixApp,
    _get_json,
    _resolve,
    _uncorrelated,
    answer_text,
    anthropic_client,
    anthropic_messages,
    call,
    canonical_input,
    mcp_client,
    native_body,
    openai_client,
    openai_messages,
    probe_handle,
    serve_on_unix_sockets,
)

__all__ = [
    "BASE_URL",
    "CALL_TIMEOUT_S",
    "ERROR_HANDLE",
    "HOLD_S",
    "INTERFACES",
    "LOGICAL",
    "MODES",
    "PER_RUN_CTX",
    "PLACEHOLDER_KEY",
    "PROBE",
    "Answer",
    "Chassis",
    "ChassisFor",
    "ChassisPool",
    "ExpectedAnswer",
    "InterfaceContract",
    "UnixApp",
    "answer_text",
    "anthropic_client",
    "anthropic_messages",
    "call",
    "mcp_client",
    "native_body",
    "openai_client",
    "openai_messages",
    "probe_handle",
    "serve_on_unix_sockets",
]


MODES = ("stream", "complete")
REMINT_INTERFACES = ("openai", "anthropic", "mcp")
"""The interfaces that re-mint a trace id in use (section 6). Native keeps its 409 for a body
`trace_id` and is left out."""

FINISH: Mapping[str, str | None] = {
    "native": None,
    "mcp": None,
    "openai": "stop",
    "anthropic": "end_turn",
}
"""The one stop reason each format sends for a normal answer (section 4)."""
ID_PREFIX: Mapping[str, str] = {"openai": "chatcmpl-", "anthropic": "msg_"}


LOGICAL = Logical(
    "simplify: the quick brown fox",
    system="Answer in plain words.",
    history=(("user", "Hi."), ("assistant", "Hello. Ask me anything.")),
    max_tokens=300,
)
"""The logical request every engine cell sends (section 2): text, a system prompt, two turns of
history, and a token budget, written natively in each format."""


PROBE = f"{__name__}:probe_handle"
ERROR_HANDLE = "chassis_contracts.lane:error_handle"
"""One delta `partial`, then a retryable `model.overloaded` error."""
PARTIAL = "partial"
ERROR_CODE = "model.overloaded"


HTTP_OPERATIONS: Mapping[str, tuple[str, str, str]] = {
    "native": ("post", "/v1/run", "run"),
    "openai": ("post", "/v1/chat/completions", "chat_completions"),
    "anthropic": ("post", "/v1/messages", "messages"),
}
"""Each HTTP interface: method, path, and operation id (sections 5, 8, and 11)."""
INTERFACE_KEY = "x-chassis-interface"


@pytest.mark.contract
class InterfaceContract:
    """Subclass as `Test*`: set the ClassVars and provide `chassis_for` (module docstring)."""

    engine_labels: ClassVar[tuple[str, ...]] = ()
    lane_labels: ClassVar[tuple[str, ...]] = ()
    inprocess_engines: ClassVar[tuple[str, ...]] = ()
    """The engines `inprocess` can run (Python handles); the rest skip in that lane."""
    expected_answers: ClassVar[Mapping[str, ExpectedAnswer]] = {}
    """Optional: each engine's answer to `logical`, checked in every cell that runs it."""

    def pytest_generate_tests(self, metafunc: pytest.Metafunc) -> None:
        cls = type(self)
        names = metafunc.fixturenames
        if "interface" in names:
            metafunc.parametrize("interface", INTERFACES, ids=INTERFACES)
        if "remint_interface" in names:
            metafunc.parametrize("remint_interface", REMINT_INTERFACES, ids=REMINT_INTERFACES)
        if "engine" in names:
            if not cls.engine_labels:
                raise TypeError(f"{cls.__name__}: set engine_labels")
            metafunc.parametrize("engine", cls.engine_labels, ids=cls.engine_labels)
        if "mode" in names:
            metafunc.parametrize("mode", MODES, ids=MODES)
        if "lane" in names:
            if len(cls.lane_labels) < 2:
                raise TypeError(
                    f"{cls.__name__}: set lane_labels to two or more lanes; the matrix covers "
                    "both transports"
                )
            metafunc.parametrize("lane", cls.lane_labels, ids=cls.lane_labels)

    # --- Fixtures ---

    @pytest.fixture(scope="class")
    @classmethod
    def chassis_for(cls) -> ChassisFor:
        raise NotImplementedError(
            "provide a class-scoped chassis_for fixture (a classmethod): "
            "chassis_for(target, lane) -> a context manager yielding a running Chassis"
        )

    @pytest.fixture(scope="class")
    @classmethod
    def chassis(cls, chassis_for: ChassisFor) -> Iterator[ChassisPool]:
        pool = ChassisPool(chassis_for)
        try:
            yield pool
        finally:
            pool.close()

    @pytest.fixture
    def logical(self) -> Logical:
        return LOGICAL

    # --- Helpers ---

    def _skip_cell(self, *, engine: str, lane: str, interface: str = "", mode: str = "") -> None:
        if interface == "mcp" and mode == "stream":
            pytest.skip(MCP_STREAM_SKIP)
        if lane == "inprocess" and engine not in self.inprocess_engines:
            pytest.skip(f"inprocess imports a Python handle by path; {engine} has none")

    def _check_answer(self, answer: Answer, interface: str, engine: str | None, where: str) -> None:
        assert answer.text, f"{where}: no answer text"
        assert answer.finish == FINISH[interface], f"{where}: finish {answer.finish!r}"
        assert answer.run_status in (None, "ok"), f"{where}: run status {answer.run_status!r}"
        assert answer.request_id and answer.trace_id, where
        if interface in ID_PREFIX:
            want = f"{ID_PREFIX[interface]}{answer.request_id}"
            assert answer.reply_id == want, f"{where}: id {answer.reply_id!r}, want {want!r}"
        expected = self.expected_answers.get(engine) if engine else None
        if expected is not None:
            assert answer.text == expected.text, f"{where}: text {answer.text!r}"
            assert answer.usage == (expected.input_tokens, expected.output_tokens), (
                f"{where}: usage {answer.usage}"
            )

    # --- The matrix ---

    async def test_every_cell_answers(
        self,
        chassis: ChassisPool,
        logical: Logical,
        interface: str,
        engine: str,
        mode: str,
        lane: str,
    ) -> None:
        """Exit criterion 2: every interface x engine x mode x lane answers the logical request,
        read by that format's own client: the text, the usage, the one finish reason, the ids."""
        self._skip_cell(engine=engine, lane=lane, interface=interface, mode=mode)
        target = chassis.get(engine, lane)
        answer = await call(target, interface, logical, stream=mode == "stream")
        self._check_answer(answer, interface, engine, f"{interface}-{engine}-{mode}-{lane}")

    async def test_stream_and_complete_agree(
        self, chassis: ChassisPool, logical: Logical, interface: str, engine: str, lane: str
    ) -> None:
        """The streamed answer joins to the complete one: same text, usage, and finish."""
        self._skip_cell(engine=engine, lane=lane, interface=interface, mode="stream")
        target = chassis.get(engine, lane)
        complete = await call(target, interface, logical, stream=False)
        streamed = await call(target, interface, logical, stream=True)
        assert streamed.text == complete.text
        assert streamed.usage == complete.usage
        assert streamed.finish == complete.finish == FINISH[interface]
        assert streamed.request_id != complete.request_id, "two runs share a request id"

    async def test_every_interface_gives_the_same_answer(
        self, chassis: ChassisPool, logical: Logical, engine: str, lane: str
    ) -> None:
        """The same logical request through all four interfaces gives the same answer text and
        the same usage."""
        self._skip_cell(engine=engine, lane=lane)
        target = chassis.get(engine, lane)
        answers = {i: await call(target, i, logical, stream=False) for i in INTERFACES}
        texts = {i: a.text for i, a in answers.items()}
        usages = {i: a.usage for i, a in answers.items()}
        assert len(set(texts.values())) == 1, f"the interfaces disagree on the text: {texts}"
        assert len(set(usages.values())) == 1, f"the interfaces disagree on the usage: {usages}"

    async def test_model_calls_are_charged_to_the_run(
        self, chassis: ChassisPool, logical: Logical, interface: str, engine: str, lane: str
    ) -> None:
        """Every model call the engine makes is a `chassis.model.call` span correlated to the run
        the interface opened (its request id), the `chassis.run` span is labeled with the
        interface, and no model call is served uncorrelated."""
        self._skip_cell(engine=engine, lane=lane)
        target = chassis.get(engine, lane)
        telemetry = target.telemetry
        uncorrelated = _uncorrelated(telemetry)
        answer = await call(target, interface, logical, stream=False)
        calls = [
            s
            for s in telemetry.spans
            if s.name == "chassis.model.call"
            and s.attributes.get("request_id") == answer.request_id
        ]
        assert calls, f"no model call was charged to run {answer.request_id}"
        assert all(s.attributes.get("correlated") is True for s in calls), calls
        runs = [
            s
            for s in telemetry.spans
            if s.name == "chassis.run" and s.attributes.get("request_id") == answer.request_id
        ]
        assert [s.attributes.get("interface") for s in runs] == [interface], runs
        assert _uncorrelated(telemetry) == uncorrelated == 0, "a model call was not correlated"

    async def test_the_workload_cannot_tell_the_interface(
        self, chassis: ChassisPool, logical: Logical, interface: str, lane: str
    ) -> None:
        """011 H-2: the probe handle sees the same `input` and `ctx` (per-run ids aside) through
        every interface as through native, and `input` is the canonical one."""
        target = chassis.get(PROBE, lane)
        native = json.loads((await call(target, "native", logical, stream=False)).text)
        seen = json.loads((await call(target, interface, logical, stream=False)).text)
        assert seen["input"] == canonical_input(logical), seen["input"]
        assert seen == native, f"{interface} vs native: {seen} != {native}"

    async def test_one_traceparent_twice_at_once_never_conflicts(
        self, chassis: ChassisPool, remint_interface: str, lane: str
    ) -> None:
        """Section 6: two overlapping calls with one `traceparent` both answer; the second run
        gets a fresh trace id (`chassis.trace_id_reminted`), never a 409. `max_retries=0`: an
        SDK retries a 409, which would hide one."""
        target = chassis.get(PROBE, lane)
        trace = uuid.uuid4().hex
        headers = {"traceparent": f"00-{trace}-{'b' * 16}-01"}
        hold = Logical(HOLD_TEXT, max_tokens=100)
        counter = ("chassis.trace_id_reminted", (("interface", remint_interface),))
        before = target.telemetry.counters.get(counter, 0)
        first, second = await asyncio.gather(
            *(
                call(target, remint_interface, hold, stream=False, headers=headers, max_retries=0)
                for _ in range(2)
            )
        )
        assert {first.trace_id, second.trace_id} >= {trace}, (first.trace_id, second.trace_id)
        assert first.trace_id != second.trace_id, "two in-flight runs share a trace id"
        assert target.telemetry.counters.get(counter, 0) == before + 1

    async def test_an_error_event_is_the_formats_error(
        self, chassis: ChassisPool, interface: str, mode: str, lane: str
    ) -> None:
        """Section 3: the run's `error` (after one delta) reaches each client in its format's
        shape: an SDK error (HTTP in complete mode, an error frame mid-stream, with the text that
        streamed first), or a 200 envelope with `status: error` (native and MCP, the known MCP
        gap). `max_retries=0`: the error is retryable, and the SDK would retry it."""
        if interface == "mcp" and mode == "stream":
            pytest.skip(MCP_STREAM_SKIP)
        target = chassis.get(ERROR_HANDLE, lane)
        stream = mode == "stream"
        logical = Logical("x", max_tokens=100)
        if interface in ("native", "mcp"):
            answer = await call(target, interface, logical, stream=stream)
            assert answer.run_status == "error", answer
            assert answer.text == PARTIAL
            return
        sink: list[str] = []
        errors: dict[str, type[Exception]] = {
            "openai": openai.APIError,
            "anthropic": anthropic.APIError,
        }
        with pytest.raises(errors[interface]) as info:
            await call(target, interface, logical, stream=stream, max_retries=0, sink=sink)
        error: Any = info.value
        assert ERROR_CODE in str(error.body) + str(error), error
        if stream:
            assert "".join(sink) == PARTIAL, "the text before the error did not stream"
            return
        assert sink == [], "an HTTP error carries no answer"
        assert error.status_code == 503, error.status_code
        assert error.response.headers.get("x-should-retry") == "true"

    # --- MCP, OpenAPI, and the manifest ---

    async def test_mcp_lists_one_tool_named_after_the_agent(
        self, chassis: ChassisPool, lane: str
    ) -> None:
        """Exit criterion 4: an MCP client lists exactly one tool, the agent."""
        target = chassis.get(PROBE, lane)
        async with mcp_client(target.public_uds) as client:
            tools = await client.list_tools()
        assert [t.name for t in tools] == [target.agent]

    async def test_mcp_tool_schema_is_the_openapi_request_schema(
        self, chassis: ChassisPool
    ) -> None:
        """Exit criterion 5: the tool's input schema is the `/v1/run` body schema of the served
        OpenAPI spec; FastMCP drops only the body's own `title`, `description`, and
        `additionalProperties`."""
        target = chassis.get(PROBE, self.lane_labels[0])
        spec = await _get_json(target, "/openapi.json")
        async with mcp_client(target.public_uds) as client:
            (tool,) = await client.list_tools()
        content = spec["paths"]["/v1/run"]["post"]["requestBody"]["content"]["application/json"]
        body = _resolve(content["schema"], spec["components"]["schemas"])
        schema = _resolve(tool.input_schema, tool.input_schema.get("$defs", {}))
        assert schema["type"] == body["type"] == "object"
        assert schema["properties"] == body["properties"]
        assert schema["required"] == body["required"]
        assert set(body) - set(schema) <= {"title", "description", "additionalProperties"}

    async def test_openapi_is_3_1_and_names_every_interface(self, chassis: ChassisPool) -> None:
        """The spec is OpenAPI 3.1 and names each HTTP interface by its operation and the
        `x-chassis-interface` extension; `/v1/mcp` is not in it (the manifest lists MCP)."""
        target = chassis.get(PROBE, self.lane_labels[0])
        spec = await _get_json(target, "/openapi.json")
        assert str(spec["openapi"]).startswith("3.1"), spec["openapi"]
        for name, (method, path, operation_id) in HTTP_OPERATIONS.items():
            operation = spec["paths"].get(path, {}).get(method)
            assert operation is not None, f"{name}: {method.upper()} {path} is not in the spec"
            assert operation.get("operationId") == operation_id, (
                name,
                operation.get("operationId"),
            )
            assert operation.get(INTERFACE_KEY) == name, (name, operation.get(INTERFACE_KEY))
            assert "text/event-stream" in operation["responses"]["200"]["content"], name
        assert MCP_PATH not in spec["paths"]

    async def test_manifest_matches_config_and_openapi(self, chassis: ChassisPool) -> None:
        """Section 8, in every lane: `/manifest` names the configured agent, versions, and lane;
        lists the four interfaces as the spec and the MCP server give them; and hashes the spec
        it serves."""
        for lane in self.lane_labels:
            target = chassis.get(PROBE, lane)
            config = target.config
            manifest = await _get_json(target, "/manifest")
            spec = await _get_json(target, "/openapi.json")
            assert manifest["agent"] == {
                "name": config.agent.name,
                "version": config.agent.version,
                "trust": config.spec.trust,
            }, (lane, manifest["agent"])
            versions = manifest["versions"]
            assert versions["config"] == config.version, (lane, versions)
            assert versions["prompt"] == config.spec.prompt.version, (lane, versions)
            assert versions["model_route"] == config.spec.model.route, (lane, versions)
            assert manifest["lane"] == config.spec.engine.connector == lane
            by_name = {i["name"]: i for i in manifest["interfaces"]}
            assert sorted(by_name) == sorted(INTERFACES), (lane, sorted(by_name))
            for name, (method, path, operation_id) in HTTP_OPERATIONS.items():
                entry = by_name[name]
                assert (entry["method"], entry["path"], entry["operation_id"]) == (
                    method.upper(),
                    path,
                    operation_id,
                ), (lane, entry)
                assert entry["streaming"] is True, (lane, entry)
            mcp = by_name["mcp"]
            assert (mcp["path"], mcp["streaming"], mcp["tools"]) == (
                MCP_PATH,
                False,
                [config.agent.name],
            ), (lane, mcp)
            raw = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
            assert manifest["openapi"]["sha256"] == hashlib.sha256(raw).hexdigest(), lane
            assert manifest["openapi"]["version"] == spec["openapi"], lane
