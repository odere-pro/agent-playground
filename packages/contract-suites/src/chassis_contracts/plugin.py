"""pytest plugin entry point. Registers nothing but the marker names the suites use."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "contract: a case from a chassis contract suite")
