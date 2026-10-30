"""PoC-4 exit criterion 7 against a real MinIO (`network`), in the README's words:

"A bad config is rejected by the schema, and the agent keeps the last good one."

This file covers both clauses with the config in a bucket: the agent config comes from the bucket
through `S3Config`, a change reaches the next request with no restart, and a bad document
(invalid, restart-only, or not YAML) is refused while the agent keeps the last good config.

Run with `scripts/check_integration.sh pocs/poc-04-stateless-scalable/tests/test_config_minio.py`.
"""

from __future__ import annotations

import asyncio
import copy
import io
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import yaml
from chassis.adapters.s3.config import S3Config
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.config_loader import ConfigRejected
from chassis_contracts.containers.minio import make_bucket, minio_container
from minio import Minio
from testcontainers.community.minio import MinioContainer

pytestmark = pytest.mark.network

POLL_S = 0.1
KEY = "agents/echo.yaml"

GOOD: dict[str, Any] = {
    "version": "minio-1",
    "profile": "local",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "adapters": {"config": "minio", "model": "fake", "telemetry": "memory", "tools": "fake"},
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}


@pytest.fixture(scope="module")
def minio(request: pytest.FixtureRequest) -> Iterator[MinioContainer]:
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run `make test-integration`")
    with minio_container() as container:
        yield container


def put(client: Minio, bucket: str, body: bytes) -> None:
    client.put_object(bucket, KEY, io.BytesIO(body), len(body), content_type="application/yaml")


def put_doc(client: Minio, bucket: str, data: dict[str, Any]) -> None:
    put(client, bucket, yaml.safe_dump(data).encode())


def with_changes(**paths: Any) -> dict[str, Any]:
    out = copy.deepcopy(GOOD)
    for path, value in paths.items():
        *parents, leaf = path.split(".")
        node = out
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def ports(store: S3Config, telemetry: InMemoryTelemetry) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(), engine=FakeEngine(handle=echo), config=store, telemetry=telemetry
    )


async def versions(client: httpx.AsyncClient) -> dict[str, Any]:
    res = await client.post("/v1/run", json={"input": {"text": "hi"}})
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()["versions"]
    return out


async def until(check: Any, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not await check():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(POLL_S)


class Running:
    """A chassis whose agent config is in a fresh bucket, served over ASGI."""

    def __init__(
        self,
        s3: Minio,
        bucket: str,
        store: S3Config,
        telemetry: InMemoryTelemetry,
        client: httpx.AsyncClient,
    ) -> None:
        self.s3 = s3
        self.bucket = bucket
        self.store = store
        self.telemetry = telemetry
        self.client = client

    def rejected(self) -> int:
        """Store documents refused so far, for either reason."""
        return self.telemetry.counter_value(
            "chassis.config.rejected", reason="invalid"
        ) + self.telemetry.counter_value("chassis.config.rejected", reason="restart_required")


@pytest.fixture
async def running(minio: MinioContainer) -> Any:  # an async iterator of one `Running`
    """A chassis started from a bootstrap config, with `minio-1` in its bucket."""
    s3 = minio.get_client()
    bucket = f"cfg-{uuid.uuid4().hex[:12]}"
    make_bucket(s3, bucket, versioned=True)
    put_doc(s3, bucket, with_changes(version="minio-1"))
    store = S3Config(s3, bucket=bucket, poll_interval_s=POLL_S)
    telemetry = InMemoryTelemetry()
    # The bootstrap differs from the store document only in reloadable fields.
    bootstrap = ChassisConfig.model_validate(with_changes(version="bootstrap"))
    app = create_app(bootstrap, ports(store, telemetry))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        yield Running(s3, bucket, store, telemetry, client)
    await store.aclose()


async def _reload_to_minio_2(chassis: Running) -> None:
    """Put `minio-2` (route `route-2`) in the bucket and wait until a request sees it."""
    put_doc(
        chassis.s3,
        chassis.bucket,
        with_changes(version="minio-2", **{"spec.model.route": "route-2"}),
    )

    async def reloaded() -> bool:
        return bool((await versions(chassis.client))["model_route"] == "route-2")

    await until(reloaded)


@pytest.fixture
async def reloaded(running: Running) -> Running:
    """The running chassis after one good reload: `minio-2` is the last good config."""
    await _reload_to_minio_2(running)
    return running


BAD_DOCUMENTS = [
    pytest.param(
        with_changes(version="bad", **{"spec.limits.body_bytes_max": 0}), id="invalid-limit"
    ),
    pytest.param(with_changes(version="bad", **{"agent.version": "0.0.2"}), id="restart-only"),
]
"""A document the schema refuses, and one that only a restart could apply."""


async def test_a_store_change_reaches_the_next_request_when_it_comes_from_s3(
    running: Running,
) -> None:
    """Exit criterion 7, the last good one: the store's config wins over the bootstrap, and a
    change in the bucket reaches the next request with no restart."""
    assert (await running.client.get("/ready")).status_code == 200
    assert (await versions(running.client))["config"].startswith("minio-1+")  # not the bootstrap

    await _reload_to_minio_2(running)

    assert (await versions(running.client))["config"].startswith("minio-2+")
    assert running.telemetry.counter_value("chassis.config.reloaded") == 1


@pytest.mark.parametrize("document", BAD_DOCUMENTS)
async def test_a_bad_store_document_is_refused_when_it_comes_from_s3(
    reloaded: Running, document: dict[str, Any]
) -> None:
    """Exit criterion 7, both clauses: this bad document in the bucket is refused, and the next
    request still gets the last good config."""
    put_doc(reloaded.s3, reloaded.bucket, document)

    async def refused() -> bool:
        return reloaded.rejected() >= 1

    await until(refused)

    assert (await versions(reloaded.client))["config"].startswith("minio-2+")
    assert reloaded.telemetry.counter_value("chassis.config.reloaded") == 1


async def test_the_last_good_config_stays_when_the_store_document_is_not_yaml(
    reloaded: Running,
) -> None:
    """Exit criterion 7, the last good one: a document that is not YAML fails the poll, and the
    next request still gets the last good config."""
    failures = reloaded.store.poll_failures
    put(reloaded.s3, reloaded.bucket, b"spec: [not: a mapping")

    async def poll_failed() -> bool:
        return reloaded.store.poll_failures > failures

    await until(poll_failed)

    assert (await versions(reloaded.client))["config"].startswith("minio-2+")
    assert reloaded.telemetry.counter_value("chassis.config.reloaded") == 1


async def test_a_bad_store_document_fails_startup(minio: MinioContainer) -> None:
    """Exit criterion 7, at startup: a bad store document stops the start, without its value."""
    s3 = minio.get_client()
    bucket = f"cfg-{uuid.uuid4().hex[:12]}"
    make_bucket(s3, bucket, versioned=True)
    put_doc(s3, bucket, with_changes(**{"spec.limits.body_bytes_max": "s3cr3t"}))
    store = S3Config(s3, bucket=bucket, poll_interval_s=POLL_S)
    app = create_app(ChassisConfig.model_validate(GOOD), ports(store, InMemoryTelemetry()))
    with pytest.raises(ConfigRejected) as caught:
        async with app.router.lifespan_context(app):
            pass
    assert "spec.limits.body_bytes_max" in str(caught.value)
    assert "s3cr3t" not in str(caught.value)
    await store.aclose()
