"""Contract suites. Bind one by subclassing it in a `Test*` class and providing its fixtures.

from chassis_contracts import ModelPortContract

class TestScriptedModel(ModelPortContract):
    @pytest.fixture
    def model_port(self):
        return ScriptedModel([...])
"""

from chassis_contracts.config import ConfigPortContract
from chassis_contracts.engine import EngineConnectorContract
from chassis_contracts.model import ModelPortContract
from chassis_contracts.telemetry import TelemetryPortContract

__all__ = [
    "ConfigPortContract",
    "EngineConnectorContract",
    "ModelPortContract",
    "TelemetryPortContract",
]
