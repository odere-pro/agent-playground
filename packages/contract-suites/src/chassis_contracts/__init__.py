"""Contract suites. Bind one by subclassing it in a `Test*` class and providing its fixtures.

from chassis_contracts import ModelPortContract

class TestScriptedModel(ModelPortContract):
    @pytest.fixture
    def model_port(self):
        return ScriptedModel([...])
"""

from chassis_contracts.auth import AuthPortContract, GoodToken
from chassis_contracts.config import ConfigPortContract
from chassis_contracts.engine import (
    JSON_VALUE,
    JSON_VALUES_HANDLE,
    EngineConnectorContract,
    json_values_handle,
)
from chassis_contracts.evaluator import EvaluatorPortContract
from chassis_contracts.events import EventPortContract
from chassis_contracts.feedback import FeedbackPortContract, make_feedback
from chassis_contracts.guardrail import GuardrailPortContract, PiiCase
from chassis_contracts.inbound import InboundAdapterContract
from chassis_contracts.lane import CASES, LaneCase, LaneContract, LaneFactory
from chassis_contracts.model import ModelPortContract
from chassis_contracts.state import StatePortContract
from chassis_contracts.telemetry import TelemetryPortContract
from chassis_contracts.tool import ToolPortContract

__all__ = [
    "CASES",
    "JSON_VALUE",
    "JSON_VALUES_HANDLE",
    "AuthPortContract",
    "ConfigPortContract",
    "EngineConnectorContract",
    "EvaluatorPortContract",
    "EventPortContract",
    "FeedbackPortContract",
    "GoodToken",
    "GuardrailPortContract",
    "InboundAdapterContract",
    "LaneCase",
    "LaneContract",
    "LaneFactory",
    "ModelPortContract",
    "PiiCase",
    "StatePortContract",
    "TelemetryPortContract",
    "ToolPortContract",
    "json_values_handle",
    "make_feedback",
]
