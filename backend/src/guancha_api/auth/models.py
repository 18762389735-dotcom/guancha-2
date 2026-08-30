"""Authentication value objects; no access or refresh token is retained."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """The only identity data accepted from a successful token verification."""

    external_subject: str

    def __post_init__(self) -> None:
        if not self.external_subject.strip():
            raise ValueError("external_subject must not be empty")


@dataclass(frozen=True, slots=True)
class AppUser:
    """Database-backed identity mapping used inside the application."""

    id: UUID
    cloudbase_user_id: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CurrentUserInfo:
    """Minimal internal current-user value passed to protected endpoints."""

    id: UUID
    created_at: datetime
