"""AuthPortContract (PoC-7): a good token gives its principal, every bad token and a missing one
are `unauthenticated`, and an outage is `auth_unavailable` and retryable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from chassis.ports.auth import AuthError, AuthPort


@dataclass(frozen=True)
class GoodToken:
    token: str
    subject: str
    scopes: frozenset[str]


@pytest.mark.contract
class AuthPortContract:
    """Subclass as `Test*`, provide `auth_port`, `good_token`, and `bad_tokens` (each must be
    refused: forged, expired, another issuer or audience, garbage). Optional: `make_unavailable`
    (makes the next call fail as an outage).
    """

    @pytest.fixture
    def auth_port(self) -> AuthPort:
        raise NotImplementedError("provide an auth_port fixture")

    @pytest.fixture
    def good_token(self) -> GoodToken:
        raise NotImplementedError("provide a good_token fixture")

    @pytest.fixture
    def bad_tokens(self) -> list[str]:
        raise NotImplementedError("provide a bad_tokens fixture")

    @pytest.fixture
    def make_unavailable(self) -> Callable[[], None]:
        pytest.skip("binding provides no make_unavailable")

    async def test_good_token_gives_its_principal(
        self, auth_port: AuthPort, good_token: GoodToken
    ) -> None:
        principal = await auth_port.authenticate(good_token.token)
        assert principal.subject == good_token.subject
        assert good_token.scopes <= principal.scopes
        for scope in good_token.scopes:
            assert principal.allows(scope)
        assert not principal.allows("no-such-scope-ever")

    async def test_same_token_same_principal(
        self, auth_port: AuthPort, good_token: GoodToken
    ) -> None:
        first = await auth_port.authenticate(good_token.token)
        assert await auth_port.authenticate(good_token.token) == first

    @pytest.mark.parametrize("token", [None, ""])
    async def test_missing_token_is_unauthenticated(
        self, auth_port: AuthPort, token: str | None
    ) -> None:
        with pytest.raises(AuthError) as info:
            await auth_port.authenticate(token)
        assert info.value.code == "unauthenticated"
        assert info.value.retryable is False

    async def test_every_bad_token_is_unauthenticated(
        self, auth_port: AuthPort, bad_tokens: list[str]
    ) -> None:
        assert bad_tokens, "a binding with no bad tokens checks nothing"
        for token in bad_tokens:
            with pytest.raises(AuthError) as info:
                await auth_port.authenticate(token)
            assert info.value.code == "unauthenticated", token
            assert token not in str(info.value), "the error must not echo the token"

    async def test_outage_is_retryable(
        self,
        auth_port: AuthPort,
        good_token: GoodToken,
        make_unavailable: Callable[[], None],
    ) -> None:
        make_unavailable()
        with pytest.raises(AuthError) as info:
            await auth_port.authenticate(good_token.token)
        assert info.value.code == "auth_unavailable"
        assert info.value.retryable is True
