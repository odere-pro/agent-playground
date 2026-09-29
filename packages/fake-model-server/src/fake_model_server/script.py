"""The script: a list of rules. The first rule whose `match` is in the last user message wins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ToolCallSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class UsageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_tokens: int = 10
    completion_tokens: int = 5


class ErrorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: int = 500
    message: str = "scripted error"


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: str | None = None
    reply: str = ""
    tool_call: ToolCallSpec | None = None
    usage: UsageSpec = Field(default_factory=UsageSpec)
    error: ErrorSpec | None = None


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "fake-model"
    rules: list[Rule] = Field(default_factory=list)
    default_reply: str = "ok"

    @classmethod
    def from_yaml(cls, path: str | Path) -> Script:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()) or {})

    def pick(self, messages: list[dict[str, Any]]) -> Rule:
        prompt = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content")
                prompt = content if isinstance(content, str) else str(content)
                break
        for rule in self.rules:
            if rule.match is None or rule.match in prompt:
                return rule
        return Rule(reply=self.default_reply)
