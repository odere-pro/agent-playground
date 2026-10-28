"""`OidcAuth`: `AuthPort` over OAuth2/OIDC bearer tokens (PoC-7, backlog 022 H-6).

A token is a JWT the company IdP signed. The adapter checks it against the IdP's published keys
(JWKS) with pyjwt, and gives the caller's `Principal`: `sub`, the scopes (`scope`, space
separated, or `scp`, a list), and `iss`.

The rules, each one a refusal as `unauthenticated` (HTTP 401) when it fails:

- The header's `alg` is RS256 or ES256 (suggested). Never `none`, never an HMAC algorithm: an
  HMAC check with a public key as the secret is the algorithm confusion attack.
- The key is the JWKS key with the header's `kid` (or the only key, when the token has no `kid`
  and the JWKS holds one). The key's own `alg`, when the JWKS gives one, must match.
- The signature, `iss`, `aud`, `exp`, and `nbf` hold, with `LEEWAY_S` of clock skew (suggested:
  30 s). `sub` and `exp` must be there.

The JWKS is fetched with httpx (`trust_env=False`, so no proxy variable reroutes it) and cached for
`jwks_ttl_s` (suggested: 300 s). A token with a `kid` the cache does not hold refetches the JWKS
once (key rotation), at most once per `refetch_cooldown_s` (suggested: 10 s), so tokens with
made-up kids cannot hammer the IdP. A JWKS that cannot be read (transport error, non-200, not
JSON) is `auth_unavailable` (HTTP 503, retryable). No error message holds the token.

Config from the environment (suggested names): `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`.
`from_env` raises `LookupError` when one is unset (the registry's "not configured").
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx
import jwt

from chassis.ports.auth import AuthError, Principal

__all__ = [
    "ALGORITHMS",
    "AUDIENCE_VAR",
    "ISSUER_VAR",
    "JWKS_TTL_S",
    "JWKS_URL_VAR",
    "LEEWAY_S",
    "OidcAuth",
]

ISSUER_VAR = "OIDC_ISSUER"
AUDIENCE_VAR = "OIDC_AUDIENCE"
JWKS_URL_VAR = "OIDC_JWKS_URL"

ALGORITHMS: tuple[str, ...] = ("RS256", "ES256")
"""suggested: the only signing algorithms accepted."""
LEEWAY_S = 30.0
"""suggested: the clock skew allowed on `exp`, `nbf`, and `iat`."""
JWKS_TTL_S = 300.0
"""suggested: how long a fetched JWKS is used before the next fetch."""
REFETCH_COOLDOWN_S = 10.0
"""suggested: the shortest time between two fetches caused by an unknown `kid`."""
FETCH_TIMEOUT_S = 5.0
"""suggested: the JWKS fetch timeout."""


def _unauthenticated(message: str) -> AuthError:
    return AuthError("unauthenticated", message)


def _unavailable(message: str) -> AuthError:
    return AuthError("auth_unavailable", message, retryable=True)


def _scopes(claims: Mapping[str, Any]) -> frozenset[str]:
    """`scope` (a space-separated string) and `scp` (a list, or a string), merged."""
    found: set[str] = set()
    for name in ("scope", "scp"):
        value = claims.get(name)
        if isinstance(value, str):
            found.update(value.split())
        elif isinstance(value, list):
            found.update(str(v) for v in value if isinstance(v, str))
    return frozenset(found)


class OidcAuth:
    """`AuthPort` over JWTs checked against a JWKS. See the module docstring."""

    name = "oidc"

    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        jwks_ttl_s: float = JWKS_TTL_S,
        refetch_cooldown_s: float = REFETCH_COOLDOWN_S,
        leeway_s: float = LEEWAY_S,
        algorithms: Sequence[str] = ALGORITHMS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        unsafe = [a for a in algorithms if a.lower() == "none" or a.upper().startswith("HS")]
        if unsafe:
            raise ValueError(f"OidcAuth refuses the algorithms {unsafe}")
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url
        self.jwks_ttl_s = jwks_ttl_s
        self.refetch_cooldown_s = refetch_cooldown_s
        self.leeway_s = leeway_s
        self.algorithms = tuple(algorithms)
        self._clock = clock
        self._client = httpx.AsyncClient(
            transport=transport, timeout=FETCH_TIMEOUT_S, trust_env=False
        )
        self._keys: dict[str | None, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"OidcAuth(issuer={self.issuer!r}, audience={self.audience!r})"

    @classmethod
    def from_env(cls) -> OidcAuth:
        values = {var: os.environ.get(var) for var in (ISSUER_VAR, AUDIENCE_VAR, JWKS_URL_VAR)}
        missing = [var for var, value in values.items() if not value]
        if missing:
            raise LookupError(f"auth: oidc needs {', '.join(missing)}")
        return cls(values[ISSUER_VAR] or "", values[AUDIENCE_VAR] or "", values[JWKS_URL_VAR] or "")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise _unauthenticated("no bearer token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise _unauthenticated("the token is not a JWT") from exc
        alg = header.get("alg")
        if alg not in self.algorithms:
            raise _unauthenticated(f"algorithm {alg!r} is not accepted")
        kid = header.get("kid")
        key = await self._key(kid if isinstance(kid, str) else None)
        if key.algorithm_name and key.algorithm_name != alg:
            raise _unauthenticated("the token's algorithm does not match its key")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_s,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise _unauthenticated(f"the token is not valid ({type(exc).__name__})") from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise _unauthenticated("the token has no subject")
        return Principal(subject=subject, scopes=_scopes(claims), issuer=self.issuer)

    async def _key(self, kid: str | None) -> jwt.PyJWK:
        """The JWKS key for `kid`: from the cache while it is fresh; one refetch on a miss."""
        async with self._lock:
            now = self._clock()
            if self._fetched_at is None or now - self._fetched_at >= self.jwks_ttl_s:
                await self._fetch()
            found = self._lookup(kid)
            if found is not None:
                return found
            assert self._fetched_at is not None
            if self._clock() - self._fetched_at >= self.refetch_cooldown_s:
                await self._fetch()
                found = self._lookup(kid)
                if found is not None:
                    return found
        raise _unauthenticated("no key in the JWKS signs this token")

    def _lookup(self, kid: str | None) -> jwt.PyJWK | None:
        if kid is None:
            return next(iter(self._keys.values())) if len(self._keys) == 1 else None
        return self._keys.get(kid)

    async def _fetch(self) -> None:
        try:
            response = await self._client.get(self.jwks_url)
        except httpx.HTTPError as exc:
            raise _unavailable(f"the JWKS could not be read ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise _unavailable(f"the JWKS answered HTTP {response.status_code}")
        try:
            data = response.json()
            entries = data["keys"]
            if not isinstance(entries, list):
                raise TypeError("keys is not a list")
        except (ValueError, KeyError, TypeError) as exc:
            raise _unavailable("the JWKS is not a JSON key set") from exc
        keys: dict[str | None, jwt.PyJWK] = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("use", "sig") != "sig":
                continue
            if entry.get("alg") is not None and entry["alg"] not in self.algorithms:
                continue
            try:
                key = jwt.PyJWK(entry)
            except jwt.PyJWTError:
                continue  # a key type this adapter does not use; skip it, keep the others
            keys[entry.get("kid")] = key
        self._keys = keys
        self._fetched_at = self._clock()
