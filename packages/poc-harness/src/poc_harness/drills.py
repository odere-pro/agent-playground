"""The named drill marks: put one in a test file's `pytestmark`. See `poc_harness.plugin`."""

from __future__ import annotations

import pytest

POC04_STACK = pytest.mark.requires_env("POC04_STACK")
"""The PoC-4 Compose scale stack is up."""
POC04_KIND = pytest.mark.requires_env("POC04_KIND")
"""The PoC-4 kind cluster is up. `deploy/kind/run.sh` pins its own context."""
POC05_KIND = pytest.mark.requires_env(
    "POC05_KIND", context="kind-poc05", hint="make kind-poc05 ARGS=up"
)
"""The PoC-5 kind cluster is up."""
POC06_KIND = pytest.mark.requires_env(
    "POC06_KIND", context="kind-poc05", hint="make kind-poc06 ARGS=up"
)
"""The PoC-6 kind cluster is up. It is the PoC-5 cluster with PoC-6 installed on it."""
