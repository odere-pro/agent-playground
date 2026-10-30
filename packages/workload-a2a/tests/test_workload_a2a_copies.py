"""The two copies ADR-002 keeps equal: `mapping.py` and the vendored `events.v0.json`. Both files
are read from the repo by path; the chassis is not imported.
"""

from __future__ import annotations

import json
from pathlib import Path

import workload_a2a
from workload_a2a.server import SCHEMA_PATH, SUPPORTED_SCHEMA_VERSIONS

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(workload_a2a.__file__).resolve().parent


def test_mapping_equals_the_chassis_copy_byte_for_byte() -> None:
    """The workload's `mapping.py` equals the chassis copy byte for byte (ADR-002)."""
    chassis_copy = ROOT / "packages/chassis/src/chassis/adapters/a2a/mapping.py"
    assert (PACKAGE / "mapping.py").read_bytes() == chassis_copy.read_bytes()


def test_mapping_imports_nothing_from_the_chassis() -> None:
    """The workload's `mapping.py` imports nothing from the chassis."""
    source = (PACKAGE / "mapping.py").read_text()
    assert "from chassis" not in source and "import chassis" not in source


def test_vendored_schema_equals_the_published_schema() -> None:
    """The vendored `events.v0.json` equals the published schema."""
    published = ROOT / "packages/chassis/schemas/events.v0.json"
    assert SCHEMA_PATH.read_bytes() == published.read_bytes()


def test_schema_ships_inside_the_package() -> None:
    """The event schema ships inside the package."""
    assert SCHEMA_PATH.is_file()
    assert SCHEMA_PATH.resolve().is_relative_to(PACKAGE)
    assert json.loads(SCHEMA_PATH.read_text())["discriminator"]["propertyName"] == "type"


def test_supported_versions_come_from_the_schema() -> None:
    """The supported schema versions are exactly `("0",)`."""
    assert SUPPORTED_SCHEMA_VERSIONS == ("0",)
