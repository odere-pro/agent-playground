"""The service chassis. See CLAUDE.md in this package and docs/contracts/contract-v0.md."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

__version__ = "0.1.0"
CHASSIS_VERSION = __version__

IMAGE_TAG_ENV = "CHASSIS_IMAGE_TAG"
"""The chassis image tag, set by `agent-lib` from `chassis.image.tag` (056 CH-7): what the rings
roll out, so `/manifest` and `versions.chassis` report it. Unset off the chart."""

_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
"""The OCI tag grammar."""


def chassis_version(environ: Mapping[str, str] = os.environ) -> str:
    """The image tag in `CHASSIS_IMAGE_TAG` when set, else the package version.

    A set value that is not an OCI tag raises: a wrong version must not reach a rollout check.
    """
    tag = environ.get(IMAGE_TAG_ENV)
    if tag is None:
        return CHASSIS_VERSION
    if not _TAG.fullmatch(tag):
        raise ValueError(f"{IMAGE_TAG_ENV} is not an image tag: {tag!r}")
    return tag
