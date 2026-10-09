"""The script: a list of rules; the first that matches wins, else `default_reply`.

A rule without `after_tool` matches only when the last message is not a `tool` message, and its
`match` is tested against the last user message. A rule with `after_tool: true` (suggested)
matches only when the last message is a `tool` message, and its `match` is tested against that
tool content. No `match` matches any text. So the rule that calls a tool is never picked again on
the tool's result, and a scripted tool loop ends.

Trailing turns (suggested): some clients, such as the Claude CLI, add turns after a tool result:
a `system` note (a `total_tokens` count) and sometimes a `user` reminder. The last `tool` message
is the trigger when only `system` or `user` messages follow it and none of those user messages
matches any plain rule (a plain rule with no `match` matches every text). A user message that
matches a plain rule is a new turn, and the plain rules answer it as before. A conversation that
ends in a `tool` message is unchanged.
"""

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
    after_tool: bool = False
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

    def _tool_trigger(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The last `tool` message when only system notes and non-matching user reminders follow."""
        for i in range(len(messages) - 1, -1, -1):
            role = messages[i].get("role")
            if role == "tool":
                return messages[i]
            if role == "system":
                continue
            if role == "user" and not self._matches_plain(_text(messages[i].get("content"))):
                continue
            return None
        return None

    def _matches_plain(self, text: str) -> bool:
        return any(not r.after_tool and (r.match is None or r.match in text) for r in self.rules)

    def pick(self, messages: list[dict[str, Any]]) -> Rule:
        trigger = self._tool_trigger(messages)
        after_tool = trigger is not None
        if trigger is not None:
            text = _text(trigger.get("content"))
        else:
            text = next(
                (_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"),
                "",
            )
        for rule in self.rules:
            if rule.after_tool == after_tool and (rule.match is None or rule.match in text):
                return rule
        return Rule(reply=self.default_reply)


def _text(content: Any) -> str:
    """A message's text: a string, or a list of text parts joined with a newline."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content)
