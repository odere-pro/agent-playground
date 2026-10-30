"""The Ryuk reaper image, pinned by digest. testcontainers starts Ryuk with the Docker socket
mounted, so it is the most trusted image of a test run.

testcontainers 4.15 starts `testcontainers/ryuk:0.8.1` by tag (`RYUK_CONTAINER_IMAGE` overrides
it). `pin_ryuk()` sets `testcontainers_config.ryuk_image`, which is read when the first container
starts, not at import. `containers/__init__.py` calls it, so every helper gets the pin.
The pin wins over `RYUK_CONTAINER_IMAGE`, so a registry mirror set there is ignored. To use a
mirror, change `RYUK_IMAGE` to the mirror's name with the same digest.
"""

from __future__ import annotations

from testcontainers.core.config import testcontainers_config

RYUK_IMAGE = (
    "testcontainers/ryuk:0.8.1"
    "@sha256:bf3f74a47dee0acda89aba4b2fc9c7fdcf994a084db02a2d06566f07baae022e"
)
"""The testcontainers 4.15 default Ryuk, pinned to its multi-arch index digest (resolved
2026-10-29 with `docker buildx imagetools inspect`). Listed in `deploy/compose/SECURITY.md`,
section 7.
"""


def pin_ryuk() -> None:
    """Make testcontainers start `RYUK_IMAGE`, whatever the environment names."""
    testcontainers_config.ryuk_image = RYUK_IMAGE
