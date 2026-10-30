"""agent-testkit: test a new agent offline from its first commit (PoC-9).

The kit starts the workload (`workload-a2a serve`) and the chassis (`fake` profile, `sidecar`
lane) as two processes and drives the workload over A2A. Nothing here imports `chassis`: the
chassis runs from its own environment (`agent_testkit.settings`).

- `agent_testkit.plugin`: the pytest fixtures `agent_handle_ref`, `workload`, `chassis`.
- `agent_testkit.contract`: `WorkloadContract`, the lane contract over A2A for one agent.
"""

from agent_testkit.processes import Chassis, StartError, Workload, start_chassis, start_workload
from agent_testkit.settings import AgentRef, ChassisSource, SettingsError, chassis_source

__all__ = [
    "AgentRef",
    "Chassis",
    "ChassisSource",
    "SettingsError",
    "StartError",
    "Workload",
    "chassis_source",
    "start_chassis",
    "start_workload",
]
