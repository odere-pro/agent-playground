"""StaticTokens: fixed test tokens, each mapped to a `Principal` (the `AuthPort` fake)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from chassis.ports.auth import AuthError, Principal

ENV = "CHASSIS_STATIC_TOKENS"
"""suggested: a JSON object `{token: {"subject": str, "scopes": [str]}}` for local runs."""


class StaticTokens:
    name = "static"

    def __init__(self, tokens: Mapping[str, Principal] | None = None) -> None:
        self.tokens = dict(tokens or {})
        self.unavailable = False
        self.calls: list[str | None] = []

    @classmethod
    def from_env(cls) -> StaticTokens:
        raw = os.environ.get(ENV)
        if not raw:
            raise LookupError(f"{ENV} is not set")
        data = json.loads(raw)
        return cls(
            {
                token: Principal(
                    subject=entry["subject"], scopes=frozenset(entry.get("scopes", ()))
                )
                for token, entry in data.items()
            }
        )

    async def authenticate(self, token: str | None) -> Principal:
        self.calls.append(token)
        if self.unavailable:
            raise AuthError("auth_unavailable", "scripted outage", retryable=True)
        if not token:
            raise AuthError("unauthenticated", "no bearer token")
        principal = self.tokens.get(token)
        if principal is None:
            raise AuthError("unauthenticated", "unknown token")
        return principal
