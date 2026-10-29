"""PoC-8: the red-team runner runs the chassis with the PoC-7 controls (the default, so the PoC-7
record still reproduces) or with the PoC-8 defaults (`controls="poc08"`): the streamed key and
the markdown image are then stopped on both guardrail stacks."""

from __future__ import annotations

import pytest
from chassis_contracts.redteam import CONFIGS, CONTROLS, load_cases, run_all

CLOSED_IN_POC08 = ("secret-04-streamed-answer-has-key", "exfil-04-markdown-image")


def test_the_controls_are_poc07_and_poc08() -> None:
    assert set(CONTROLS) == {"poc07", "poc08"}


@pytest.mark.parametrize("config", sorted(CONFIGS))
async def test_poc08_stops_the_two_cases_poc07_missed(config: str) -> None:
    cases = [c for c in load_cases() if c.id in CLOSED_IN_POC08]
    before = await run_all(config, cases)
    after = await run_all(config, cases, controls="poc08")
    assert [o.stopped for o in before] == [False, False]
    assert [o.stopped for o in after] == [True, True]
