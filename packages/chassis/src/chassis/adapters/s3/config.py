"""S3Config: `ConfigPort` over an S3 bucket, on the `minio` SDK (sync, so every call runs in
`asyncio.to_thread`).

`load(name)` reads `<prefix><name>.yaml` and returns its YAML mapping. The version is the S3
version id when the bucket keeps versions, else the ETag without quotes. A missing object is
`ConfigNotFound`; any other store failure, and a document that is not a YAML mapping, is
`ConfigUnavailable`. No message or log line holds a credential.

`subscribe(name, callback)` polls `stat_object` on the running loop every `poll_interval_s`,
with jitter so replicas do not poll together. A changed version is loaded and handed to
`callback`. The baseline is the version this instance last loaded for `name`, else the first
stat. A failed poll is logged and counted (`chassis.config.poll_failed`), never raised.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Callable, Mapping
from typing import Any

import urllib3
import yaml
from minio import Minio
from minio.error import S3Error

from chassis.ports.config import (
    ConfigCallback,
    ConfigNotFound,
    ConfigUnavailable,
    LoadedConfig,
    Unsubscribe,
)

log = logging.getLogger("chassis.config")

POLL_FAILED = "chassis.config.poll_failed"
DEFAULT_BUCKET = "agent-configs"  # suggested
DEFAULT_PREFIX = "agents/"  # suggested
DEFAULT_POLL_INTERVAL_S = 5.0  # suggested
DEFAULT_JITTER = 0.1  # suggested: +-10% of the interval
# suggested: short timeouts and few retries, so a hung store fails a poll, not the replica.
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 10.0
RETRIES = 2
NOT_FOUND = frozenset({"NoSuchKey"})

PollFailed = Callable[[str, BaseException], None]


def _flag(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "")
    if not value:
        raise LookupError(f"{key} is not set")
    return value


def _http_client() -> urllib3.PoolManager:
    return urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
        retries=urllib3.Retry(
            total=RETRIES, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]
        ),
    )


def _version(version_id: str | None, etag: str | None) -> str:
    if version_id and version_id != "null":
        return version_id
    return (etag or "").strip('"')


def _why(exc: BaseException) -> str:
    """A safe description: the S3 error code or the exception type, never a header or a key."""
    if isinstance(exc, S3Error):
        return f"S3 error {exc.code}"
    return type(exc).__name__


class S3Config:
    """`ConfigPort` over an S3 bucket. Build it with `from_env()`, or pass a `Minio` client."""

    def __init__(
        self,
        client: Minio,
        *,
        bucket: str = DEFAULT_BUCKET,
        prefix: str = DEFAULT_PREFIX,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        jitter: float = DEFAULT_JITTER,
        on_poll_failed: PollFailed | None = None,
    ) -> None:
        self._client = client
        self.bucket = bucket
        self.prefix = prefix
        self.poll_interval_s = poll_interval_s
        self.jitter = jitter
        self.poll_failures = 0
        self.on_poll_failed: PollFailed | None = on_poll_failed
        """Called with the name and the error after a failed poll; settable after build (the
        config reloader sets it to count `chassis.config.poll_failed` in telemetry)."""
        self._seen: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def __repr__(self) -> str:
        return f"S3Config(bucket={self.bucket!r}, prefix={self.prefix!r})"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> S3Config:
        """From `CONFIG_S3_*` and `CONFIG_POLL_INTERVAL_S`. `LookupError` names a missing one."""
        env = os.environ if env is None else env
        client = Minio(
            _required(env, "CONFIG_S3_ENDPOINT"),
            access_key=_required(env, "CONFIG_S3_ACCESS_KEY"),
            secret_key=_required(env, "CONFIG_S3_SECRET_KEY"),
            secure=_flag(env.get("CONFIG_S3_SECURE", "true")),
            region=env.get("CONFIG_S3_REGION") or None,
            http_client=_http_client(),
        )
        return cls(
            client,
            bucket=env.get("CONFIG_S3_BUCKET") or DEFAULT_BUCKET,
            prefix=env.get("CONFIG_S3_PREFIX", DEFAULT_PREFIX),
            poll_interval_s=float(env.get("CONFIG_POLL_INTERVAL_S") or DEFAULT_POLL_INTERVAL_S),
        )

    def _key(self, name: str) -> str:
        return f"{self.prefix}{name}.yaml"

    def _get(self, name: str) -> tuple[str, bytes]:
        response = self._client.get_object(self.bucket, self._key(name))
        try:
            body = response.read()
            version = _version(
                response.headers.get("x-amz-version-id"), response.headers.get("ETag")
            )
        finally:
            response.close()
            response.release_conn()
        return version, body

    def _stat(self, name: str) -> str:
        found = self._client.stat_object(self.bucket, self._key(name))
        return _version(found.version_id, found.etag)

    async def _call[T](self, name: str, fn: Callable[[str], T]) -> T:
        try:
            return await asyncio.to_thread(fn, name)
        except S3Error as exc:
            if exc.code in NOT_FOUND:
                raise ConfigNotFound(name) from exc
            raise ConfigUnavailable(f"config store failed for {name!r}: {_why(exc)}") from exc
        except Exception as exc:
            raise ConfigUnavailable(f"config store failed for {name!r}: {_why(exc)}") from exc

    async def load(self, name: str) -> LoadedConfig:
        version, body = await self._call(name, self._get)
        try:
            data: Any = yaml.safe_load(body)
        except yaml.YAMLError as exc:
            raise ConfigUnavailable(f"config {name!r} is not valid YAML") from exc
        if not isinstance(data, dict):
            raise ConfigUnavailable(f"config {name!r} is not a YAML mapping")
        self._seen[name] = version
        return LoadedConfig(name=name, version=version, data=data)

    def subscribe(self, name: str, callback: ConfigCallback) -> Unsubscribe:
        """Start one polling task on the running loop. The returned function cancels it."""
        task = asyncio.get_running_loop().create_task(
            self._poll(name, callback, self._seen.get(name)), name=f"config-poll:{name}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        def unsubscribe() -> None:
            task.cancel()

        return unsubscribe

    def _delay(self) -> float:
        spread = self.poll_interval_s * self.jitter
        # Not security sensitive: jitter only spreads the polls of replicas.
        return max(0.0, self.poll_interval_s + random.uniform(-spread, spread))

    def _failed(self, name: str, exc: BaseException) -> None:
        self.poll_failures += 1
        log.warning("%s name=%s reason=%s", POLL_FAILED, name, _why(exc))
        hook = self.on_poll_failed
        if hook is not None:
            try:
                hook(name, exc)
            except Exception:
                log.exception("on_poll_failed hook raised")

    async def _poll(self, name: str, callback: ConfigCallback, last: str | None) -> None:
        if last is None:
            try:
                last = await self._call(name, self._stat)
            except (ConfigNotFound, ConfigUnavailable) as exc:
                self._failed(name, exc)
        while True:
            await asyncio.sleep(self._delay())
            try:
                if await self._call(name, self._stat) == last:
                    continue
                loaded = await self.load(name)
                last = loaded.version
                await callback(loaded)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failed(name, exc)

    async def aclose(self) -> None:
        """Cancel every polling task and wait for them to end."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
