"""PoC-1 fixtures shared by `test_serve.py` and `test_a2a_inprocess.py`."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from chassis.server.proxy_app import create_proxy_app


@pytest.fixture
def route_workload_to(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Any]:
    """Route `echo_python`'s model call into a chassis app's proxy app over an ASGI transport
    (the workload's test-only `transport` hook), so it reaches the chassis's model port with no
    socket. Returns the app. `monkeypatch` restores the prior `transport` after the test.
    """

    def route(app: Any) -> Any:
        workload: Any = importlib.import_module("echo_python.handle")
        monkeypatch.setattr(workload, "transport", httpx.ASGITransport(app=create_proxy_app(app)))
        return app

    return route
