"""Small protocols used to keep authentication injectable in tests."""

from typing import Protocol

from guancha_api.auth.models import AppUser, VerifiedIdentity


class TokenVerifier(Protocol):
    async def verify(self, access_token: str) -> VerifiedIdentity:
        """Verify an access token and return only its trusted external subject."""


class AppUserRepository(Protocol):
    async def resolve_or_create_app_user(self, cloudbase_user_id: str) -> AppUser:
        """Resolve a stable internal app user from a verified external subject."""
