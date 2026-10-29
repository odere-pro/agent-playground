"""AuthPort: who the caller is (PoC-7, backlog 022 H-6). The real adapter is `OidcAuth`
(`chassis.adapters.oidc`), which checks a JWT against the IdP's JWKS.

`authenticate(token)` takes the bearer value without the `Bearer ` prefix, or None when the call
carried none. It returns a `Principal` or raises `AuthError`. The port only says who the caller
is and which scopes the token holds. Which scope a route needs is the chassis's rule
(`spec.security`), never the port's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

ANY_SCOPE = "*"
"""A scope that allows every route. Only `NoAuth` holds it."""


@dataclass(frozen=True)
class Principal:
    """A checked caller. `subject` is the token's `sub`; `scopes` are its granted scopes."""

    subject: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    issuer: str | None = None

    def allows(self, scope: str) -> bool:
        return ANY_SCOPE in self.scopes or scope in self.scopes


class AuthError(Exception):
    """A call the port refused or could not check. `code`, and whether a retry may help:

    - `unauthenticated`: no token, or a token that is not valid (forged, expired, wrong issuer or
      audience, malformed). HTTP 401. Not retryable.
    - `auth_unavailable`: the IdP's keys could not be read. HTTP 503. `retryable=True`.

    The message never holds the token.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class AuthPort(Protocol):
    name: str

    async def authenticate(self, token: str | None) -> Principal: ...


class NoAuth:
    """Auth off: every call is the anonymous principal with every scope. The `PortBundle`
    default and `spec.adapters.auth: none`, so bundles built before PoC-7 still build.
    """

    name = "none"

    async def authenticate(self, token: str | None) -> Principal:
        return Principal(subject="anonymous", scopes=frozenset({ANY_SCOPE}))
