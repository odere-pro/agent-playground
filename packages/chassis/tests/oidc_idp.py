"""A test IdP for `OidcAuth`, offline: generated signing keys, the JWKS they publish, and an
httpx `MockTransport` handler that serves it. No socket, no key from outside. Used by the port
tests (`test_oidc_adapter.py`) and the route tests (`test_security_auth_oidc.py`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

__all__ = ["AUDIENCE", "ISSUER", "JWKS_URL", "Idp", "new_rsa"]

ISSUER = "https://idp.invalid/realms/agents"
AUDIENCE = "agent-simplifier"
JWKS_URL = "https://idp.invalid/realms/agents/protocol/openid-connect/certs"


def new_rsa() -> rsa.RSAPrivateKey:
    """A fresh 2048-bit RSA signing key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class Idp:
    """A test IdP: signing keys by `kid`, the JWKS it serves, and a scripted outage."""

    keys: dict[str, Any] = field(default_factory=dict)
    published: list[str] = field(default_factory=list)
    """The kids the JWKS lists now."""
    status: int = 200
    fetches: int = 0

    def add_rsa(self, kid: str, *, publish: bool = True) -> None:
        self.keys[kid] = new_rsa()
        if publish:
            self.published.append(kid)

    def add_ec(self, kid: str) -> None:
        self.keys[kid] = ec.generate_private_key(ec.SECP256R1())
        self.published.append(kid)

    def jwks(self) -> dict[str, Any]:
        out = []
        for kid in self.published:
            key = self.keys[kid]
            if isinstance(key, rsa.RSAPrivateKey):
                jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
                jwk.update(alg="RS256", use="sig")
            else:
                jwk = ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
                jwk.update(alg="ES256", use="sig")
            jwk["kid"] = kid
            out.append(jwk)
        return {"keys": out}

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        self.fetches += 1
        if self.status != 200:
            return httpx.Response(self.status, text="identity provider down")
        return httpx.Response(200, json=self.jwks())

    def token(
        self,
        kid: str = "k1",
        *,
        alg: str | None = None,
        headers: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "user-1",
            "iat": now,
            "exp": now + 300,
            "scope": "agent:run agent:read",
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        key = self.keys[kid]
        algorithm = alg or ("RS256" if isinstance(key, rsa.RSAPrivateKey) else "ES256")
        return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid, **(headers or {})})
