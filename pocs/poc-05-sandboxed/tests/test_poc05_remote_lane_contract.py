"""PoC-5 exit criterion 2: CI runs a fake workload through the `remote` lane on every commit, and
it passes the same contract suite as the `sidecar` lane.

`chassis_contracts.lane.LaneContract`, the class PoC-2 binds for `inprocess` and `sidecar`, bound
here once over three lanes in one class, so one run compares all three pairwise:

- `inprocess`: the handle loaded by path, A2A in memory.
- `sidecar`: the workload's template A2A server (`workload-a2a serve`) on a Unix socket, no
  token, behind `SidecarConnector`.
- `remote`: the same template server built by the same CLI with `--require-token-env`, behind
  `RemoteConnector` with `spec.engine.auth` (a random token per test). Every request carries the
  bearer; a missing one would be a 401 and fail every case (H17, the allowed control).

No case is skipped for any lane. No TCP: Unix sockets only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import pytest
from chassis_contracts import LaneContract, LaneFactory
from poc05_harness import Tokens, inprocess_lane, remote_lane_connector, sidecar_lane


class TestPoc05ThreeLanes(LaneContract):
    """Exit criterion 2: the remote lane passes the same `LaneContract` as `sidecar` and
    `inprocess`, in the same run.
    """

    lane_labels: ClassVar[tuple[str, ...]] = ("inprocess", "sidecar", "remote")

    @pytest.fixture
    def lanes(self, monkeypatch: pytest.MonkeyPatch) -> Mapping[str, LaneFactory]:
        Tokens.one().apply(monkeypatch)
        return {
            "inprocess": inprocess_lane,
            "sidecar": sidecar_lane,
            "remote": remote_lane_connector,
        }
