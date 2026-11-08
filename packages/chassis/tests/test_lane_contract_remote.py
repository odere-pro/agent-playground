"""ADR-001 hard requirement 2: "CI also runs a fake workload through the `remote` lane."

`chassis_contracts.lane.LaneContract` over `inprocess`, `sidecar`, and `remote` in one class, so
one run compares all three pairwise. `remote` is the template server built with
`--require-token-env`, behind `RemoteConnector` with a random bearer token per test; a missing
token would be a 401 and fail every case. Unix sockets only, no TCP.

PoC-5 binds the same suite the same way (`test_poc05_remote_lane_contract.py`). That file is PoC
evidence and runs locally (`make test-pocs-done`); this binding keeps the rule in the CI gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import pytest
from chassis_contracts import LaneContract, LaneFactory
from poc_harness.lane_tokens import Tokens
from poc_harness.remote_lane import inprocess_lane, remote_lane_connector, sidecar_lane


class TestChassisRemoteLane(LaneContract):
    """The `remote` lane passes the same lane contract as `sidecar` and `inprocess`."""

    lane_labels: ClassVar[tuple[str, ...]] = ("inprocess", "sidecar", "remote")

    @pytest.fixture
    def lanes(self, monkeypatch: pytest.MonkeyPatch) -> Mapping[str, LaneFactory]:
        Tokens.one().apply(monkeypatch)
        return {
            "inprocess": inprocess_lane,
            "sidecar": sidecar_lane,
            "remote": remote_lane_connector,
        }
