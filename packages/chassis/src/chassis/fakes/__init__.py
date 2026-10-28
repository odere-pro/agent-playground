"""In-memory fakes, one per port. Each passes the same contract suite as the real adapter."""

from chassis.fakes.auth import StaticTokens
from chassis.fakes.config import InMemoryConfig
from chassis.fakes.engine import FakeEngine
from chassis.fakes.evaluator import ScoreRule, ScriptedEvaluator
from chassis.fakes.events import InMemoryBus
from chassis.fakes.feedback import InMemoryFeedback
from chassis.fakes.guardrail import KeywordGuardrails
from chassis.fakes.model import ScriptedModel, ScriptRule
from chassis.fakes.state import InMemoryState
from chassis.fakes.telemetry import InMemoryTelemetry
from chassis.fakes.tool import InMemoryTools, default_tools

__all__ = [
    "FakeEngine",
    "InMemoryBus",
    "InMemoryConfig",
    "InMemoryFeedback",
    "InMemoryState",
    "InMemoryTelemetry",
    "InMemoryTools",
    "KeywordGuardrails",
    "ScoreRule",
    "ScriptRule",
    "ScriptedEvaluator",
    "ScriptedModel",
    "StaticTokens",
    "default_tools",
]
