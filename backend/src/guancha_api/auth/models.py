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


@dataclass(frozen=True, slots=True)
class OwnerContext:
    """Server-derived owner for a Selection request.

    Exactly one branch is populated.  The context deliberately contains no
    token, CloudBase subject, claims, or client-supplied internal user id.
    """

    user_id: UUID | None = None
    anonymous_client_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.user_id is None) == (self.anonymous_client_id is None):
            raise ValueError("OwnerContext must contain exactly one owner")

    @classmethod
    def authenticated(cls, user_id: UUID) -> "OwnerContext":
        return cls(user_id=user_id)

    @classmethod
    def anonymous(cls, anonymous_client_id: UUID) -> "OwnerContext":
        return cls(anonymous_client_id=anonymous_client_id)

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


def resolve_owner(*, owner: OwnerContext | None = None, client_id: UUID | None = None) -> OwnerContext:
    """Normalize a request owner while retaining compatibility for old callers."""
    if owner is not None:
        return owner
    if client_id is not None:
        return OwnerContext.anonymous(client_id)
    raise ValueError("owner is required")


def repository_owner(owner: OwnerContext) -> OwnerContext | UUID:
    """Return the server-derived value accepted by the repository boundary."""
    if owner.user_id is not None:
        return owner
    assert owner.anonymous_client_id is not None
    return owner.anonymous_client_id
