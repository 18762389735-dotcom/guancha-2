"""Deterministic token verifiers for offline tests and local app wiring."""

from collections.abc import Mapping

from guancha_api.auth.errors import (
    AuthenticationNotConfigured,
    AuthenticationServiceUnavailable,
    InvalidAccessToken,
)
from guancha_api.auth.models import VerifiedIdentity


class FakeTokenVerifier:
    """A closed allow-list verifier; arbitrary tokens never authenticate."""

    def __init__(
        self,
        identities: Mapping[str, str] | None = None,
        *,
        unavailable_tokens: frozenset[str] = frozenset({"unavailable-token"}),
    ) -> None:
        self._identities = dict(
            identities
            or {
                "valid-token-a": "cloudbase-user-a",
                "valid-token-b": "cloudbase-user-b",
            }
        )
        self._unavailable_tokens = unavailable_tokens

    async def verify(self, access_token: str) -> VerifiedIdentity:
        if access_token in self._unavailable_tokens:
            raise AuthenticationServiceUnavailable
        external_subject = self._identities.get(access_token)
        if external_subject is None:
            raise InvalidAccessToken
        return VerifiedIdentity(external_subject=external_subject)


class UnconfiguredTokenVerifier:
    """Keeps anonymous app startup working when CloudBase is not configured."""

    async def verify(self, access_token: str) -> VerifiedIdentity:
        del access_token
        raise AuthenticationNotConfigured


class ConfigurationErrorTokenVerifier:
    """Represents an invalid CloudBase configuration without breaking startup."""

    async def verify(self, access_token: str) -> VerifiedIdentity:
        del access_token
        raise AuthenticationServiceUnavailable
