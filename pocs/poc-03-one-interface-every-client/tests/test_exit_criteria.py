"""PoC-3 exit criteria: the scenario tests for each criterion in `README.md`, each docstring naming
its criterion word for word (skill `poc-iteration`). The helpers are in `poc03_exit_checks.py`.

Offline. The client scenarios serve the app built from `packages/chassis/configs/fake.yaml` on
uvicorn over a Unix socket (`poc_harness.sockets.on_unix_socket`), with no injected ports: the fake
profile builds them and each run goes through the real `inprocess` lane (A2A in memory) to a
wire-form `handle`. The SDKs and the MCP client speak real HTTP and real SSE to it; the gate allows
`AF_UNIX` only.

A criterion whose evidence has not landed yet is `xfail(strict=True)`, narrowed with `raises=` to
the missing file or module, so any other failure is a real one and the landing turns it into a
strict pass that the gate reports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, get_args

import pytest
from chassis.adapters.mcp import build_agent_mcp
from chassis.core.inbound import Interface
from chassis.fakes import InMemoryTelemetry
from fastapi import FastAPI
from fastmcp.server.providers.openapi.components import OpenAPITool
from poc03_exit_checks import (
    AGENT_MCP_SOURCES,
    BEHAVIORS,
    ENGINES,
    GAPS_CHECKED,
    HAND_WRITTEN_TOOL_APIS,
    LANES,
    MAKEFILE,
    OFFLINE_SCRIPT,
    SUITE_FILES,
    TEXT,
    agent_mcp_builders_on_the_spec,
    agent_mcp_callers,
    anthropic_both_ways,
    bound_interfaces,
    ci_run_steps,
    ci_triggers,
    collect_with_gate_flags,
    contract_binding,
    contract_cells,
    engine_name,
    gap_names,
    gap_rows,
    gaps_with_no_handling,
    gate_flags,
    has_network_marker,
    legitimate_skip,
    names_used,
    openai_both_ways,
    pytest_options,
    recipe,
    skips,
    spec_with_run_property,
)
from poc03_support import (
    AGENT,
    BASE,
    ROOT,
    TIMEOUT_S,
    lane_app,
    mcp_client,
    run_body_schema,
    uds_http_client,
)
from poc_harness.sockets import on_unix_socket

# --- 1 -----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gate_collection() -> dict[str, list[str]]:
    """What the gate's flags collect from the suite files: node id to marker names."""
    return collect_with_gate_flags()


@pytest.mark.slow
@pytest.mark.parametrize("suite", list(SUITE_FILES))
def test_exit_1_the_suites_run_offline_in_ci_on_every_commit(
    suite: str, gate_collection: dict[str, list[str]]
) -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    With the gate's flags, the suite's file is collected and none of its tests is marked `network`.
    """
    relative = str(SUITE_FILES[suite].relative_to(ROOT))

    collected = {
        node: marks for node, marks in gate_collection.items() if node.startswith(relative)
    }

    assert collected, f"the gate collects nothing from {relative}"
    assert not [node for node, marks in collected.items() if "network" in marks]


@pytest.mark.parametrize("suite", list(SUITE_FILES))
def test_exit_1_no_suite_file_uses_the_network_marker(suite: str) -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    The suite's source holds no `pytest.mark.network`, so no test can leave the gate that way.
    """
    assert not has_network_marker(SUITE_FILES[suite])


def test_exit_1_ci_triggers_on_every_push_and_every_pull_request() -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    CI runs on `push` and `pull_request`, with no branch filter on `push`.
    """
    triggers = ci_triggers()

    assert set(triggers) >= {"push", "pull_request"}
    assert not (triggers["push"] or {}).get("branches"), "every commit, on every branch"


def test_exit_1_ci_runs_make_check() -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    A CI step runs `make check`.
    """
    assert "make check" in ci_run_steps()


def test_exit_1_make_check_runs_the_offline_script_with_no_path_or_filter() -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    `make check` runs `make test`, which is `scripts/check_offline.sh` with no path and no `-m`.
    """
    makefile = MAKEFILE.read_text()

    assert "PYTEST := scripts/check_offline.sh" in makefile
    assert "test" in recipe(makefile, "check")[0].split()
    assert recipe(makefile, "test") == ["$(PYTEST)"], "no path, no -m: testpaths decide"


def test_exit_1_the_offline_script_strips_the_api_keys() -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    `scripts/check_offline.sh` unsets the `_API_KEY` variables before pytest runs.
    """
    script = OFFLINE_SCRIPT.read_text()

    assert "_API_KEY" in script
    assert 'unset "$v"' in script


def test_exit_1_the_gate_flags_block_sockets_and_cassette_recording() -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    The gate passes `--disable-socket --allow-unix-socket --record-mode=none`.
    """
    assert {"--disable-socket", "--allow-unix-socket", "--record-mode=none"} <= set(gate_flags())


def test_exit_1_the_gate_flags_filter_no_test_out() -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    The gate passes no `-m`, `-k`, `--ignore`, or `--deselect`.
    """
    filters = ("-m", "-k", "--ignore", "--deselect")

    assert not [flag for flag in gate_flags() if flag.startswith(filters)]


def test_exit_1_pytest_collects_the_pocs_with_no_extra_options() -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    The root pytest `testpaths` holds `pocs`, and no `addopts` can narrow the run.
    """
    options = pytest_options()

    assert "pocs" in options["testpaths"]
    assert "addopts" not in options


# --- 2 -----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def binding() -> Any:
    """The one `InterfaceContract` binding in `test_interface_contract.py`."""
    return contract_binding()


def test_exit_2_the_contract_suite_covers_all_engines_interfaces_and_both_transports(
    binding: Any,
) -> None:
    """Exit criterion: "The contract suite passes for all engines and all interfaces, over both
    transports".

    The binding declares all four interfaces, all four engines, and both lanes; only the
    TypeScript echo is missing from `inprocess`. That the cells pass is the binding's own run.
    """
    interfaces = set(bound_interfaces(binding))
    engines = {engine_name(label) for label in binding.engine_labels}
    inprocess = {engine_name(label) for label in binding.inprocess_engines}

    assert interfaces == set(get_args(Interface)) == {"native", "openai", "anthropic", "mcp"}
    assert engines == ENGINES
    assert set(binding.lane_labels) == LANES
    assert inprocess == ENGINES - {"echo_typescript"}


def test_exit_2_only_the_two_legitimate_groups_of_cells_skip(binding: Any) -> None:
    """Exit criterion: "The contract suite passes for all engines and all interfaces, over both
    transports".

    Of the 64 cells, only MCP x stream and echo-typescript x inprocess skip: 15, so 49 run.
    """
    cells = contract_cells(binding)
    instance = binding()

    skipped = {cell for cell in cells if skips(instance, *cell)}

    assert skipped == {cell for cell in cells if legitimate_skip(*cell)}
    # 8 MCP x stream (4 engines x 2 lanes) + 8 TypeScript x inprocess (4 x 2) - 1 in both.
    # Section 9 of the open note says 16, 22, and 42; its arithmetic is off.
    assert (len(cells), len(skipped), len(cells) - len(skipped)) == (64, 15, 49)


# --- 3 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "both_ways",
    [
        pytest.param(openai_both_ways, id="openai"),
        pytest.param(anthropic_both_ways, id="anthropic"),
    ],
)
async def test_exit_3_the_sdks_work_with_only_a_base_url_change(
    both_ways: Callable[[str], Awaitable[None]],
) -> None:
    """Exit criterion: "The OpenAI and Anthropic SDKs work with only a base URL change".

    The official clients, unmodified, make a complete call and a streamed call over a Unix socket.
    The only non-default arguments are the base URL and its transport (`http_client`, because the
    gate has no TCP), a placeholder key, and `model` set to the agent's name.
    """
    app = lane_app()

    with on_unix_socket(app) as uds:
        async with asyncio.timeout(TIMEOUT_S):
            await both_ways(uds)


# --- 4 -----------------------------------------------------------------------------------------


async def test_exit_4_an_mcp_client_lists_the_agents_tool_and_runs_it() -> None:
    """Exit criterion: "An MCP client lists the agent's tool and runs it".

    A real `fastmcp.Client` at `/v1/mcp` lists one tool, named after the agent; calling it runs
    the agent through the lane, returns the native envelope, and labels the run `interface: mcp`.
    """
    app = lane_app()

    with on_unix_socket(app) as uds:
        async with asyncio.timeout(TIMEOUT_S), mcp_client(uds) as client:
            tools = await client.list_tools()
            result = await client.call_tool(AGENT, {"input": {"text": TEXT}})
            telemetry = app.state.ports.telemetry

    assert [tool.name for tool in tools] == [AGENT]
    assert result.is_error is False
    envelope = result.structured_content
    assert envelope is not None
    assert (envelope["status"], envelope["agent"]) == ("ok", AGENT)
    assert envelope["output"] == {"text": TEXT}
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.requests", agent=AGENT, interface="mcp") == 1


# --- 5 -----------------------------------------------------------------------------------------


async def test_exit_5_mcp_tools_and_rest_come_from_one_openapi_spec() -> None:
    """Exit criterion: "The MCP tools and the REST API come from the same OpenAPI spec (no
    hand-written tool definitions)".

    The tool's input schema is the `/v1/run` request body schema of the app's own spec. FastMCP
    keeps `properties` and `required` and drops only `title`, `description`, and
    `additionalProperties`.
    """
    app = lane_app()

    with on_unix_socket(app) as uds:
        async with asyncio.timeout(TIMEOUT_S), mcp_client(uds) as client:
            [listed] = await client.list_tools()

    body = run_body_schema(app.openapi())
    schema = listed.input_schema
    assert schema["type"] == body["type"] == "object"
    assert schema["properties"] == body["properties"]
    assert schema["required"] == body["required"]
    assert set(body) - set(schema) <= {"title", "description", "additionalProperties"}
    assert set(schema) <= set(body)


async def test_exit_5_the_agents_tool_is_an_openapi_tool() -> None:
    """Exit criterion: "The MCP tools and the REST API come from the same OpenAPI spec (no
    hand-written tool definitions)".

    The tool the agent's MCP server lists is FastMCP's `OpenAPITool`.
    """
    app = lane_app()

    [tool] = await app.state.agent_mcp.list_tools()

    assert isinstance(tool, OpenAPITool), type(tool)


async def test_exit_5_the_tool_follows_a_change_to_the_spec() -> None:
    """Exit criterion: "The MCP tools and the REST API come from the same OpenAPI spec (no
    hand-written tool definitions)".

    A property added to the `/v1/run` body in the spec shows up in a tool built from that spec.
    """
    changed = spec_with_run_property(lane_app().openapi(), "probe", {"type": "string"})
    other = FastAPI()
    other.openapi = lambda: changed  # type: ignore[method-assign]

    [rebuilt] = await build_agent_mcp(other, agent_name=AGENT).list_tools()

    assert rebuilt.parameters["properties"]["probe"] == {"type": "string"}


@pytest.mark.parametrize("source", AGENT_MCP_SOURCES, ids=["adapter", "interface"])
def test_exit_5_no_tool_is_written_by_hand(source: Path) -> None:
    """Exit criterion: "The MCP tools and the REST API come from the same OpenAPI spec (no
    hand-written tool definitions)".

    The source uses no FastMCP way to write a tool by hand and no `mcp_component_fn`.
    """
    names = names_used(source)

    assert not names & HAND_WRITTEN_TOOL_APIS, source.name
    assert "mcp_component_fn" not in names


def test_exit_5_build_agent_mcp_builds_from_the_spec_alone() -> None:
    """Exit criterion: "The MCP tools and the REST API come from the same OpenAPI spec (no
    hand-written tool definitions)".

    The one `build_agent_mcp` calls `FastMCP.from_openapi` (or `from_fastapi`).
    """
    assert agent_mcp_builders_on_the_spec() == [True]


def test_exit_5_only_the_mcp_interface_calls_build_agent_mcp() -> None:
    """Exit criterion: "The MCP tools and the REST API come from the same OpenAPI spec (no
    hand-written tool definitions)".

    No other chassis file builds the agent's MCP server.
    """
    assert agent_mcp_callers() == ["server/interfaces/mcp.py"]


# --- 6 -----------------------------------------------------------------------------------------


def test_exit_6_the_gaps_note_lists_each_gap_with_how_it_is_handled() -> None:
    """Exit criterion: "Any gaps between the formats are listed (for example tool calls or stop
    reasons that do not map one to one), with how each is handled".

    The note has one table, and every row has a non-empty "How it is handled" cell.
    """
    rows = gap_rows()

    empty = gaps_with_no_handling(rows)

    assert len(rows) >= len(GAPS_CHECKED)
    assert not empty, f"rows with no handling: {empty}"


@pytest.mark.parametrize("gap", list(GAPS_CHECKED))
def test_exit_6_the_gaps_note_lists_every_gap_the_behavior_test_checks(gap: str) -> None:
    """Exit criterion: "Any gaps between the formats are listed (for example tool calls or stop
    reasons that do not map one to one), with how each is handled".

    The note has a row for each gap that `test_exit_6_the_handled_gaps_behave_as_listed` checks.
    """
    names = gap_names(gap_rows())

    assert any(GAPS_CHECKED[gap] in name for name in names), names


@pytest.mark.parametrize("gap", list(BEHAVIORS))
async def test_exit_6_the_handled_gaps_behave_as_listed(gap: str) -> None:
    """Exit criterion: "Any gaps between the formats are listed (for example tool calls or stop
    reasons that do not map one to one), with how each is handled".

    The handling the gaps table gives (open note, section 4) is true of the running app.
    """
    handle, check = BEHAVIORS[gap]
    app = lane_app(handle)

    with on_unix_socket(app) as uds:
        async with asyncio.timeout(TIMEOUT_S), uds_http_client(uds, base_url=BASE) as http:
            await check(http, uds)
