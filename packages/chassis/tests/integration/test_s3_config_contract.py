"""`S3Config` against a real MinIO: the `ConfigPort` contract suite, and the parts only S3 has
(the version id or the ETag, `from_env`, a failed poll that is counted and not raised).
"""

from __future__ import annotations

import asyncio
import io
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import yaml
from chassis.adapters.s3.config import S3Config
from chassis.ports.config import ConfigUnavailable, LoadedConfig
from chassis_contracts import ConfigPortContract
from chassis_contracts.config import Bump, wait_for_version
from chassis_contracts.containers.minio import make_bucket, minio_container
from minio import Minio
from testcontainers.community.minio import MinioContainer

POLL_S = 0.1
PREFIX = "agents/"


@pytest.fixture(scope="module")
def minio(request: pytest.FixtureRequest) -> Iterator[MinioContainer]:
    # A module fixture is set up before the conftest's autouse skip, and the MinIO wait strategy
    # needs TCP, so skip here too when sockets are off.
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run `make test-integration`")
    with minio_container() as container:
        yield container


@pytest.fixture
def client(minio: MinioContainer) -> Minio:
    return minio.get_client()


def put_yaml(client: Minio, bucket: str, key: str, data: dict[str, Any]) -> None:
    body = yaml.safe_dump(data).encode()
    client.put_object(bucket, key, io.BytesIO(body), len(body), content_type="application/yaml")


def new_bucket(client: Minio, *, versioned: bool = True) -> str:
    bucket = f"cfg-{uuid.uuid4().hex[:12]}"
    make_bucket(client, bucket, versioned=versioned)
    put_yaml(client, bucket, f"{PREFIX}echo.yaml", {"spec": {"kind": "transformer"}})
    return bucket


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


class TestS3Config(ConfigPortContract):
    @pytest.fixture
    def bucket(self, client: Minio) -> str:
        return new_bucket(client)

    @pytest.fixture
    async def config_port(self, client: Minio, bucket: str) -> AsyncIterator[S3Config]:
        port = S3Config(client, bucket=bucket, prefix=PREFIX, poll_interval_s=POLL_S)
        yield port
        await port.aclose()

    @pytest.fixture
    def bump_config(self, client: Minio, bucket: str, config_name: str) -> Bump:
        async def bump() -> None:
            data = {"spec": {"kind": "transformer", "v": uuid.uuid4().hex}}
            await asyncio.to_thread(put_yaml, client, bucket, f"{PREFIX}{config_name}.yaml", data)

        return bump

    @pytest.fixture
    def notify_timeout_s(self) -> float:
        return 5.0

    @pytest.fixture
    async def unavailable_config_port(self) -> AsyncIterator[S3Config]:
        dead = Minio(f"127.0.0.1:{closed_port()}", "nobody", "nothing", secure=False)
        port = S3Config(dead, poll_interval_s=POLL_S)
        yield port
        await port.aclose()


async def test_version_is_the_version_id_when_the_bucket_is_versioned(
    client: Minio,
) -> None:
    bucket = new_bucket(client)
    port = S3Config(client, bucket=bucket, prefix=PREFIX)
    loaded = await port.load("echo")
    stat = client.stat_object(bucket, f"{PREFIX}echo.yaml")
    assert stat.version_id
    assert loaded.version == stat.version_id
    assert loaded.data == {"spec": {"kind": "transformer"}}


async def test_version_is_the_etag_without_quotes_when_versioning_is_off(
    client: Minio,
) -> None:
    bucket = new_bucket(client, versioned=False)
    port = S3Config(client, bucket=bucket, prefix=PREFIX)
    loaded = await port.load("echo")
    stat = client.stat_object(bucket, f"{PREFIX}echo.yaml")
    assert loaded.version == (stat.etag or "").strip('"')
    assert '"' not in loaded.version


async def test_a_document_that_is_not_a_mapping_is_unavailable(client: Minio) -> None:
    bucket = new_bucket(client)
    body = b"- just\n- a list\n"
    client.put_object(bucket, f"{PREFIX}echo.yaml", io.BytesIO(body), len(body))
    with pytest.raises(ConfigUnavailable, match="not a YAML mapping"):
        await S3Config(client, bucket=bucket, prefix=PREFIX).load("echo")


async def test_a_failed_poll_is_counted_not_raised_and_polling_goes_on(client: Minio) -> None:
    bucket = new_bucket(client)
    failures: list[str] = []
    port = S3Config(
        client,
        bucket=bucket,
        prefix=PREFIX,
        poll_interval_s=POLL_S,
        on_poll_failed=lambda name, exc: failures.append(name),
    )
    seen: list[LoadedConfig] = []

    async def on_change(loaded: LoadedConfig) -> None:
        seen.append(loaded)

    await port.load("echo")
    port.subscribe("echo", on_change)
    try:
        client.remove_object(bucket, f"{PREFIX}echo.yaml")  # a delete marker: polls fail
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while True:
            if port.poll_failures >= 2 or loop.time() >= deadline:
                break
            await asyncio.sleep(0.05)
        assert port.poll_failures >= 2
        assert failures and set(failures) == {"echo"}
        await asyncio.to_thread(put_yaml, client, bucket, f"{PREFIX}echo.yaml", {"v": 3})
        after = await port.load("echo")
        await wait_for_version(seen, after.version, 5)
        assert seen and seen[-1].data == {"v": 3}
    finally:
        await port.aclose()


def test_from_env_reads_the_plan_variables(minio: MinioContainer) -> None:
    cfg = minio.get_config()
    env = {
        "CONFIG_S3_ENDPOINT": cfg["endpoint"],
        "CONFIG_S3_SECURE": "false",
        "CONFIG_S3_ACCESS_KEY": cfg["access_key"],
        "CONFIG_S3_SECRET_KEY": cfg["secret_key"],
        "CONFIG_S3_BUCKET": "b1",
        "CONFIG_S3_PREFIX": "p/",
        "CONFIG_POLL_INTERVAL_S": "0.1",
    }
    port = S3Config.from_env(env)
    assert (port.bucket, port.prefix, port.poll_interval_s) == ("b1", "p/", 0.1)
    assert cfg["secret_key"] not in repr(port)
    defaults = S3Config.from_env({k: env[k] for k in list(env)[:4]})
    assert (defaults.bucket, defaults.prefix, defaults.poll_interval_s) == (
        "agent-configs",
        "agents/",
        5.0,
    )


@pytest.mark.parametrize(
    "missing", ["CONFIG_S3_ENDPOINT", "CONFIG_S3_ACCESS_KEY", "CONFIG_S3_SECRET_KEY"]
)
def test_from_env_needs_endpoint_and_keys(missing: str) -> None:
    env = {
        "CONFIG_S3_ENDPOINT": "localhost:9000",
        "CONFIG_S3_ACCESS_KEY": "a",
        "CONFIG_S3_SECRET_KEY": "b",
    }
    del env[missing]
    with pytest.raises(LookupError, match=missing):
        S3Config.from_env(env)
