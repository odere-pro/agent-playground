"""`ValkeyState`: `StatePort` over Valkey (valkey-py 6, `valkey.asyncio`).

`set` is `SET key value [PX ms]`, `set_if_absent` is `SET key value NX PX ms`, and
`compare_and_set` is one Lua script, so the compare and the write are one atomic step on the
server. Every store error becomes `StateUnavailable` with the error's class name only: no URL,
no credential, no server text.
"""

from __future__ import annotations

import math
import os
from typing import Any, Final
from urllib.parse import urlsplit

from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from chassis.ports.state import StateUnavailable

URL_VAR: Final = "VALKEY_URL"
USERNAME_VAR: Final = "VALKEY_USERNAME"
PASSWORD_VAR: Final = "VALKEY_PASSWORD"

SOCKET_TIMEOUT_S: Final = 1.0
"""suggested: per-command socket timeout."""
CONNECT_TIMEOUT_S: Final = 1.0
"""suggested: connect timeout."""
HEALTH_CHECK_INTERVAL_S: Final = 10
"""suggested: seconds between pings on an idle pooled connection."""

# KEYS[1] the key. ARGV[1] expected, ARGV[2] mode (`del`, `px`, or `keep`), ARGV[3] the new
# value, ARGV[4] the TTL in ms when the mode is `px`. Returns 1 on a match, else 0.
COMPARE_AND_SET_LUA: Final = """
local current = redis.call('GET', KEYS[1])
if current == false or current ~= ARGV[1] then
  return 0
end
if ARGV[2] == 'del' then
  redis.call('DEL', KEYS[1])
elseif ARGV[2] == 'px' then
  redis.call('SET', KEYS[1], ARGV[3], 'PX', ARGV[4])
else
  redis.call('SET', KEYS[1], ARGV[3])
end
return 1
"""

_ERRORS = (ValkeyError, OSError)


def ttl_ms(ttl_s: float | None) -> int | None:
    """Seconds to whole milliseconds, rounded up, at least 1. `None` stays `None`."""
    if ttl_s is None:
        return None
    if ttl_s <= 0:
        raise ValueError("ttl_s must be greater than 0")
    return max(1, math.ceil(ttl_s * 1000))


def _unavailable(exc: BaseException) -> StateUnavailable:
    return StateUnavailable(f"state: valkey {type(exc).__name__}")


class ValkeyState:
    """`StatePort` over one `valkey.asyncio.Valkey` client and its pool."""

    def __init__(self, client: Valkey) -> None:
        self._client = client
        self._cas = client.register_script(COMPARE_AND_SET_LUA)

    def __repr__(self) -> str:
        return "ValkeyState()"

    @classmethod
    def from_url(
        cls, url: str, *, username: str | None = None, password: str | None = None
    ) -> ValkeyState:
        """Build a client; no connection is made until the first call. A password in the URL is
        refused: credentials come as arguments, so the URL is safe to log."""
        if urlsplit(url).password is not None:
            raise ValueError(f"state: {URL_VAR} must not hold a password; set {PASSWORD_VAR}")
        kwargs: dict[str, Any] = {
            "socket_timeout": SOCKET_TIMEOUT_S,
            "socket_connect_timeout": CONNECT_TIMEOUT_S,
            "health_check_interval": HEALTH_CHECK_INTERVAL_S,
        }
        if username:
            kwargs["username"] = username
        if password:
            kwargs["password"] = password
        return cls(Valkey.from_url(url, **kwargs))

    @classmethod
    def from_env(cls) -> ValkeyState:
        """Build from `VALKEY_URL` (required), `VALKEY_USERNAME` and `VALKEY_PASSWORD`
        (optional). A missing URL raises `LookupError`."""
        url = os.environ.get(URL_VAR)
        if not url:
            raise LookupError(f"state: valkey needs {URL_VAR}")
        return cls.from_url(
            url,
            username=os.environ.get(USERNAME_VAR) or None,
            password=os.environ.get(PASSWORD_VAR) or None,
        )

    async def get(self, key: str) -> bytes | None:
        try:
            value = await self._client.get(key)
        except _ERRORS as exc:
            raise _unavailable(exc) from None
        return None if value is None else bytes(value)

    async def set(self, key: str, value: bytes, *, ttl_s: float | None = None) -> None:
        px = ttl_ms(ttl_s)
        try:
            await self._client.set(key, value, px=px)
        except _ERRORS as exc:
            raise _unavailable(exc) from None

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        px = ttl_ms(ttl_s)
        try:
            claimed = await self._client.set(key, value, nx=True, px=px)
        except _ERRORS as exc:
            raise _unavailable(exc) from None
        return bool(claimed)

    async def compare_and_set(
        self, key: str, expected: bytes, value: bytes | None, *, ttl_s: float | None = None
    ) -> bool:
        px = ttl_ms(ttl_s)
        if value is None:
            args: list[bytes | int] = [expected, b"del", b"", 0]
        elif px is None:
            args = [expected, b"keep", value, 0]
        else:
            args = [expected, b"px", value, px]
        try:
            replaced = await self._cas(keys=[key], args=args)
        except _ERRORS as exc:
            raise _unavailable(exc) from None
        return bool(replaced)

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except _ERRORS as exc:
            raise _unavailable(exc) from None

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except _ERRORS as exc:
            raise _unavailable(exc) from None
