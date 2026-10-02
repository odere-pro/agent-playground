"""Exit criterion 7 against a real MinIO (`network`): the agent config comes from the bucket through
`S3Config`, a change reaches the next request with no restart, and a bad document (invalid,
restart-only, or not YAML) is refused while the agent keeps the last good config.

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


async def test_reload_and_refusal_through_s3(minio: MinioContainer) -> None:
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
        assert (await client.get("/ready")).status_code == 200
        assert (await versions(client))["config"].startswith(
            "minio-1+"
        )  # the store, not the bootstrap

        put_doc(s3, bucket, with_changes(version="minio-2", **{"spec.model.route": "route-2"}))

        async def reloaded() -> bool:
            return bool((await versions(client))["model_route"] == "route-2")

        await until(reloaded)
        assert (await versions(client))["config"].startswith("minio-2+")

        bad = [
            with_changes(version="bad", **{"spec.limits.body_bytes_max": 0}),
            with_changes(version="bad", **{"agent.version": "0.0.2"}),
        ]
        for n, doc in enumerate(bad, start=1):
            put_doc(s3, bucket, doc)

            async def refused(n: int = n) -> bool:
                return (
                    telemetry.counter_value("chassis.config.rejected", reason="invalid")
                    + (
                        telemetry.counter_value(
                            "chassis.config.rejected", reason="restart_required"
                        )
                    )
                    >= n
                )

            await until(refused)
            assert (await versions(client))["config"].startswith("minio-2+")

        failures = store.poll_failures
        put(s3, bucket, b"spec: [not: a mapping")

        async def poll_failed() -> bool:
            return store.poll_failures > failures

        await until(poll_failed)
        assert (await versions(client))["config"].startswith("minio-2+")
        assert telemetry.counter_value("chassis.config.reloaded") == 1
    await store.aclose()


async def test_a_bad_store_document_fails_startup(minio: MinioContainer) -> None:
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
