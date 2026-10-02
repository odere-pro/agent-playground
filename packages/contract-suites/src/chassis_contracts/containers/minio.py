"""MinIO in a testcontainer, for the `S3Config` binding (PoC-4). `network` tests only.

The official `minio/minio` image is gone from Docker Hub, so the helper runs `pgsty/minio`, a
community build of the same server with the same entrypoint. suggested: pin that tag. The
credentials are random per container and never leave the test process.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager

from minio import Minio
from minio.commonconfig import ENABLED
from minio.versioningconfig import VersioningConfig
from testcontainers.community.minio import MinioContainer

# suggested: the newest tag on the day (2026-10-01).
MINIO_IMAGE = "pgsty/minio:RELEASE.2026-08-04T00-00-00Z"


@contextmanager
def minio_container(image: str = MINIO_IMAGE) -> Iterator[MinioContainer]:
    """A started MinIO with random root credentials; stopped and removed on exit."""
    access_key = f"test-{secrets.token_hex(4)}"
    secret_key = secrets.token_urlsafe(24)
    container = MinioContainer(image=image, access_key=access_key, secret_key=secret_key)
    # Newer servers read MINIO_ROOT_*; the testcontainer sets only the old names.
    container.with_env("MINIO_ROOT_USER", access_key).with_env("MINIO_ROOT_PASSWORD", secret_key)
    with container:
        yield container


def make_bucket(client: Minio, name: str, *, versioned: bool = True) -> None:
    """Create `name`, with versioning on unless `versioned=False`."""
    client.make_bucket(name)
    if versioned:
        client.set_bucket_versioning(name, VersioningConfig(ENABLED))
