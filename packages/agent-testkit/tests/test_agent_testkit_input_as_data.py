"""The input-as-data contract case (025 H-10): the input text reaches the model in its own message.

The case is `WorkloadContract.test_the_input_reaches_the_model_as_its_own_message`. It runs here
against two workloads, each with its own workload and chassis processes and the fake model:
`echo-python`, which sends the input as its own user message (the case passes), and a bad copy
that merges the input into the system prompt (the case fails). All offline.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from agent_testkit.contract import INPUT_AS_DATA_SCRIPT, WorkloadContract
from agent_testkit.processes import Chassis, start_chassis, start_workload
from agent_testkit.settings import CURRENT_PYTHON, AgentRef, source_for

pytestmark = pytest.mark.slow

GOOD = AgentRef(handle="echo_python:handle", name="echo-python")
BAD = AgentRef(handle="agent_testkit_merged_prompt:handle", name="merged-prompt")
BAD_DIR = Path(__file__).parent / "workloads"
START_S = 120.0
"""suggested: the workload start budget here; a loaded, shared Mac can take over 30 s."""


def _chassis_for(ref: AgentRef, env: dict[str, str] | None = None) -> Iterator[Chassis]:
    with start_workload(ref, timeout_s=START_S, env=env) as workload:
        source = source_for(CURRENT_PYTHON, Path.cwd())
        with start_chassis(workload, source, model_script=INPUT_AS_DATA_SCRIPT) as chassis:
            yield chassis


@pytest.fixture(scope="module")
def good_chassis() -> Iterator[Chassis]:
    yield from _chassis_for(GOOD)


@pytest.fixture(scope="module")
def bad_chassis() -> Iterator[Chassis]:
    path = os.pathsep.join(p for p in (str(BAD_DIR), os.environ.get("PYTHONPATH", "")) if p)
    yield from _chassis_for(BAD, {"PYTHONPATH": path})


def test_the_case_passes_when_the_input_is_its_own_message(good_chassis: Chassis) -> None:
    WorkloadContract().test_the_input_reaches_the_model_as_its_own_message(good_chassis)


def test_the_case_fails_when_the_input_is_merged_into_the_system_prompt(
    bad_chassis: Chassis,
) -> None:
    with pytest.raises(AssertionError, match="did not carry the input text as its own"):
        WorkloadContract().test_the_input_reaches_the_model_as_its_own_message(bad_chassis)
