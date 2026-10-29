"""PoC-7 exit criterion 2: `OidcAuth` (`chassis.adapters.oidc`) passes `AuthPortContract`, the
suite `StaticTokens` passes, offline. The IdP is a generated RSA key and a JWKS served by an httpx
`MockTransport`: no socket, no key from outside.

Beyond the suite: the JWKS is cached for its TTL and fetched again once on an unknown `kid` (key
rotation); `alg: none`, HS256 signed with the public key (the algorithm confusion attack), and a
token whose key is not in the JWKS are all `unauthenticated`; scopes come from `scope` or `scp`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from chassis.adapters.oidc import OidcAuth
from chassis.ports.auth import AuthError
from chassis_contracts import AuthPortContract, GoodToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

ISSUER = "https://idp.invalid/realms/agents"
AUDIENCE = "agent-simplifier"
JWKS_URL = "https://idp.invalid/realms/agents/protocol/openid-connect/certs"


def _rsa() -> rsa.RSAPrivateKey:
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
        self.keys[kid] = _rsa()
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


def _unsigned(claims: dict[str, Any]) -> str:
    """An `alg: none` token, built by hand (pyjwt refuses to make one with a key)."""
    import base64

    def b64(data: dict[str, Any]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{b64({'alg': 'none', 'typ': 'JWT', 'kid': 'k1'})}.{b64(claims)}."


def _auth(idp: Idp, **kwargs: Any) -> OidcAuth:
    return OidcAuth(
        ISSUER, AUDIENCE, JWKS_URL, transport=httpx.MockTransport(idp.handler), **kwargs
    )


@pytest.fixture
def idp() -> Idp:
    idp = Idp()
    idp.add_rsa("k1")
    return idp


class TestOidcAuth(AuthPortContract):
    @pytest.fixture
    def auth_port(self, idp: Idp) -> OidcAuth:
        return _auth(idp)

    @pytest.fixture
    def good_token(self, idp: Idp) -> GoodToken:
        return GoodToken(idp.token(), "user-1", frozenset({"agent:run", "agent:read"}))

    @pytest.fixture
    def bad_tokens(self, idp: Idp) -> list[str]:
        now = int(time.time())
        forger = _rsa()
        good = idp.token()
        forged = jwt.encode(
            jwt.decode(good, options={"verify_signature": False}),
            forger,
            algorithm="RS256",
            headers={"kid": "k1"},
        )
        public_pem = (
            idp.keys["k1"]
            .public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        hs256_confusion = _hs256(
            jwt.decode(good, options={"verify_signature": False}), public_pem, kid="k1"
        )
        return [
            forged,
            idp.token(exp=now - 3600, iat=now - 7200),
            idp.token(iss="https://other-idp.invalid"),
            idp.token(aud="another-agent"),
            idp.token(sub=None),
            _unsigned(jwt.decode(good, options={"verify_signature": False})),
            hs256_confusion,
            "not-a-jwt",
            "a.b.c",
            good[:-8] + "AAAAAAAA",
        ]

    @pytest.fixture
    def make_unavailable(self, idp: Idp) -> Callable[[], None]:
        def down() -> None:
            idp.status = 503

        return down


def _hs256(claims: dict[str, Any], secret: bytes, *, kid: str) -> str:
    """HS256 with the RSA public key as the HMAC secret: pyjwt refuses a PEM as an HMAC key, so
    this signs by hand, as an attacker would."""
    import base64
    import hashlib
    import hmac

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    payload = b64(json.dumps(claims).encode())
    signature = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(signature)}"


async def test_jwks_is_cached_for_its_ttl(idp: Idp) -> None:
    auth = _auth(idp)
    for _ in range(3):
        await auth.authenticate(idp.token())
    assert idp.fetches == 1


async def test_jwks_is_fetched_again_after_the_ttl(idp: Idp) -> None:
    now = [1000.0]
    auth = _auth(idp, jwks_ttl_s=60, clock=lambda: now[0])
    await auth.authenticate(idp.token())
    now[0] += 61
    await auth.authenticate(idp.token())
    assert idp.fetches == 2


async def test_unknown_kid_refetches_once_then_accepts_a_rotated_key(idp: Idp) -> None:
    auth = _auth(idp, refetch_cooldown_s=0)
    await auth.authenticate(idp.token())
    idp.add_rsa("k2")  # the IdP rotates
    principal = await auth.authenticate(idp.token("k2"))
    assert principal.subject == "user-1"
    assert idp.fetches == 2


async def test_unknown_kid_is_refused_after_one_refetch(idp: Idp) -> None:
    auth = _auth(idp, refetch_cooldown_s=0)
    idp.add_rsa("k9", publish=False)
    with pytest.raises(AuthError) as info:
        await auth.authenticate(idp.token("k9"))
    assert info.value.code == "unauthenticated"
    assert idp.fetches == 2  # the first fetch, and one refetch for the unknown kid


async def test_unknown_kids_do_not_hammer_the_idp(idp: Idp) -> None:
    auth = _auth(idp)  # default cooldown
    idp.add_rsa("k9", publish=False)
    for _ in range(5):
        with pytest.raises(AuthError):
            await auth.authenticate(idp.token("k9"))
    assert idp.fetches == 1


async def test_es256_is_accepted(idp: Idp) -> None:
    idp.add_ec("e1")
    principal = await _auth(idp).authenticate(idp.token("e1"))
    assert principal.allows("agent:run")


async def test_scp_list_is_read_as_scopes(idp: Idp) -> None:
    token = idp.token(scope=None, scp=["agent:feedback"])
    principal = await _auth(idp).authenticate(token)
    assert principal.scopes == frozenset({"agent:feedback"})
    assert principal.issuer == ISSUER


async def test_hs256_is_refused_even_with_a_jwks_secret(idp: Idp) -> None:
    claims = jwt.decode(idp.token(), options={"verify_signature": False})
    token = _hs256(claims, b"anything", kid="k1")
    with pytest.raises(AuthError) as info:
        await _auth(idp).authenticate(token)
    assert info.value.code == "unauthenticated"


async def test_clock_skew_is_allowed_up_to_the_leeway(idp: Idp) -> None:
    now = int(time.time())
    auth = _auth(idp)
    assert (await auth.authenticate(idp.token(exp=now - 10))).subject == "user-1"
    with pytest.raises(AuthError):
        await auth.authenticate(idp.token(exp=now - 120))


async def test_jwks_transport_error_is_unavailable(idp: Idp) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    auth = OidcAuth(ISSUER, AUDIENCE, JWKS_URL, transport=httpx.MockTransport(broken))
    with pytest.raises(AuthError) as info:
        await auth.authenticate(idp.token())
    assert info.value.code == "auth_unavailable"
    assert info.value.retryable


async def test_jwks_that_is_not_json_is_unavailable(idp: Idp) -> None:
    auth = OidcAuth(
        ISSUER,
        AUDIENCE,
        JWKS_URL,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")),
    )
    with pytest.raises(AuthError) as info:
        await auth.authenticate(idp.token())
    assert info.value.code == "auth_unavailable"


def test_from_env_needs_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(LookupError):
        OidcAuth.from_env()
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    with pytest.raises(LookupError):
        OidcAuth.from_env()
    monkeypatch.setenv("OIDC_JWKS_URL", JWKS_URL)
    auth = OidcAuth.from_env()
    assert auth.issuer == ISSUER
    assert "OidcAuth(" in repr(auth)


def test_registry_builds_oidc_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from chassis.profiles import resolve

    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OIDC_JWKS_URL", JWKS_URL)
    assert isinstance(resolve("auth", "oidc"), OidcAuth)
