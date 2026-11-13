"""Who holds which token in a `remote` lane, and the W3C trace values a run is named by.

Tokens are random per test (`new_token`); no file holds a value. The variable names are fixed
(`TOKEN_ENV` and the rest); a test sets them with `monkeypatch` (`Tokens.apply`).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

import pytest

TOKEN_ENV = "POC05_REMOTE_TOKEN"
"""`spec.engine.auth.token_env`: the chassis-to-workload key; the connector sends it."""
PREVIOUS_TOKEN_ENV = "POC05_REMOTE_TOKEN_PREVIOUS"
"""`spec.engine.auth.previous_token_env`: the outbound key being rotated out."""
PROXY_TOKEN_ENV = "POC05_REMOTE_PROXY_TOKEN"
"""`spec.engine.auth.proxy_token_env`: the workload-to-chassis key; the remote listener accepts
only it (055 CH-6)."""
PROXY_PREVIOUS_ENV = "POC05_REMOTE_PROXY_TOKEN_PREVIOUS"
"""`spec.engine.auth.proxy_previous_token_env`: the listener also accepts it during a rotation."""
WORKLOAD_TOKEN_ENV = "POC05_WORKLOAD_TOKEN"
"""The workload server's `--require-token-env`."""
WORKLOAD_PREVIOUS_ENV = "POC05_WORKLOAD_TOKEN_PREVIOUS"
"""The workload server's `--previous-token-env`."""
SENDS_ENV = "CHASSIS_API_TOKEN"
"""What `echo_python` sends as its bearer on every model and MCP call."""

REMOTE_URL = "http://echo-remote:9000"
"""`spec.engine.url`. Over `uds` it only fills the `Host` header and the path."""
REMOTE_PROXY_URL = "http://chassis-remote:8091"
"""Where the workload's model and MCP calls go. Over a Unix socket it only fills `Host`."""


def new_token() -> str:
    """A random per-test token. The prefix marks it as a test value, not a key."""
    return f"poc05-test-{secrets.token_hex(16)}"


def new_trace_id() -> str:
    """A W3C trace id (32 hex) for one run, so a test can name the run in a `traceparent`."""
    return secrets.token_hex(16)


def traceparent(trace_id: str) -> str:
    return f"00-{trace_id}-{secrets.token_hex(8)}-01"


@dataclass(frozen=True)
class Tokens:
    """Who holds which token. One key per direction (055 CH-6): in steady state `chassis` and
    `workload` are the chassis-to-workload key, and `sends` and `proxy` the workload-to-chassis
    key (`Tokens.one()`); during a rotation the current and previous slots differ.
    """

    chassis: str
    """`TOKEN_ENV`: the connector sends it to the workload."""
    workload: str
    """`WORKLOAD_TOKEN_ENV`: the workload server requires it."""
    sends: str
    """`SENDS_ENV`: the workload sends it to the remote listener."""
    chassis_previous: str | None = None
    workload_previous: str | None = None
    proxy: str | None = None
    """`PROXY_TOKEN_ENV`: the remote listener accepts it. Unset, the listener cannot start."""
    proxy_previous: str | None = None

    @classmethod
    def one(cls) -> Tokens:
        to_workload, to_chassis = new_token(), new_token()
        return cls(chassis=to_workload, workload=to_workload, sends=to_chassis, proxy=to_chassis)

    def values(self) -> set[str]:
        return {
            t
            for t in (
                self.chassis,
                self.workload,
                self.sends,
                self.chassis_previous,
                self.workload_previous,
                self.proxy,
                self.proxy_previous,
            )
            if t
        }

    def apply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set the variables; an unused optional slot is removed, not left from a former test."""
        monkeypatch.setenv(TOKEN_ENV, self.chassis)
        monkeypatch.setenv(WORKLOAD_TOKEN_ENV, self.workload)
        monkeypatch.setenv(SENDS_ENV, self.sends)
        for name, value in (
            (PREVIOUS_TOKEN_ENV, self.chassis_previous),
            (WORKLOAD_PREVIOUS_ENV, self.workload_previous),
            (PROXY_TOKEN_ENV, self.proxy),
            (PROXY_PREVIOUS_ENV, self.proxy_previous),
        ):
            if value:
                monkeypatch.setenv(name, value)
            else:
                monkeypatch.delenv(name, raising=False)
