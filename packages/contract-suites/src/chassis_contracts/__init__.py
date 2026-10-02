"""Contract suites. Bind one by subclassing it in a `Test*` class and providing its fixtures.

from chassis_contracts import ModelPortContract

class TestScriptedModel(ModelPortContract):
    @pytest.fixture
    def model_port(self):
        return ScriptedModel([...])
"""

from chassis_contracts.config import ConfigPortContract
from chassis_contracts.engine import (
    JSON_VALUE,
    JSON_VALUES_HANDLE,
    EngineConnectorContract,
    json_values_handle,
)
from chassis_contracts.inbound import InboundAdapterContract
from chassis_contracts.lane import CASES, LaneCase, LaneContract, LaneFactory
from chassis_contracts.model import ModelPortContract
from chassis_contracts.telemetry import TelemetryPortContract
from chassis_contracts.tool import ToolPortContract

__all__ = [
    "CASES",
    "JSON_VALUE",
    "JSON_VALUES_HANDLE",
    "ConfigPortContract",
    "EngineConnectorContract",
    "InboundAdapterContract",
    "LaneCase",
    "LaneContract",
    "LaneFactory",
    "ModelPortContract",
    "TelemetryPortContract",
    "ToolPortContract",
    "json_values_handle",
]
