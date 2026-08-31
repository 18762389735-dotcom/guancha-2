"""P9-4A snapshot repository lifetime and ownership regressions."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from guancha_api.auth.fake import FakeTokenVerifier
from guancha_api.auth.models import AppUser, OwnerContext
from guancha_api.main import create_app
from guancha_api.repositories.postgres import OwnershipDenied, RepositoryError


USER_IDS = {
    "cloudbase-user-a": UUID("00000000-0000-0000-0000-0000000000a1"),
    "cloudbase-user-b": UUID("00000000-0000-0000-0000-0000000000b1"),
}
SNAPSHOT = {
    "session": {"id": "session-1", "need": {"taste_text": "fresh"}},
    "candidates": [],
    "current_decision_id": None,
    "question_decision_version_id": None,
    "question_generation_status": None,
    "questions": [],
    "merchant_replies": [],
    "rejudge_job": None,
    "session_decision_job": None,
    "decision_delta": None,
}


class FakeSnapshotRepository:
    def __init__(
        self,
        *,
        allowed_subject: str | None = None,
        allowed_anonymous_client_id: UUID | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.allowed_subject = allowed_subject
        self.allowed_anonymous_client_id = allowed_anonymous_client_id
        self.failure = failure
        self.resolve_calls = 0
        self.snapshot_calls = 0
        self.close_calls = 0

    async def resolve_or_create_app_user(self, cloudbase_user_id: str) -> AppUser:
        self.resolve_calls += 1
        now = datetime.now(timezone.utc)
        user_id = USER_IDS[cloudbase_user_id]
        return AppUser(
            id=user_id,
            cloudbase_user_id=cloudbase_user_id,
            created_at=now,
            updated_at=now,
        )

    async def selection_snapshot_for_client(
        self, *, session_id: UUID, client_id: OwnerContext
    ) -> dict[str, object]:
        del session_id
        self.snapshot_calls += 1
        if self.failure is not None:
            raise self.failure
        if client_id.is_authenticated:
            if self.allowed_subject is None or client_id.user_id != USER_IDS[self.allowed_subject]:
                raise OwnershipDenied
        elif client_id.anonymous_client_id != self.allowed_anonymous_client_id:
            raise OwnershipDenied
        return SNAPSHOT

    async def close(self) -> None:
        self.close_calls += 1


def _app(*, fallback: FakeSnapshotRepository, factory=None):
    return create_app(
        repository=fallback,
        worker_repository_factory=factory,
        token_verifier=FakeTokenVerifier(),
    )


async def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str = "valid-token-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_authenticated_snapshot_prefers_factory_and_closes_request_repository() -> None:
    fallback = FakeSnapshotRepository(allowed_subject="cloudbase-user-a")
    repositories: list[FakeSnapshotRepository] = []

    async def factory() -> FakeSnapshotRepository:
        repository = FakeSnapshotRepository(allowed_subject="cloudbase-user-a")
        repositories.append(repository)
        return repository

    app = _app(fallback=fallback, factory=factory)
    async with await _client(app) as client:
        response = await client.get(
            f"/api/v1/selection-sessions/{uuid4()}/snapshot", headers=_auth()
        )

    assert response.status_code == 200
    assert response.json() == SNAPSHOT
    assert len(repositories) == 2
    assert repositories[0].resolve_calls == 1
    assert repositories[0].close_calls == 1
    assert repositories[1].snapshot_calls == 1
    assert repositories[1].close_calls == 1
    assert fallback.resolve_calls == 0
    assert fallback.snapshot_calls == 0
    assert fallback.close_calls == 0


@pytest.mark.asyncio
async def test_authenticated_snapshot_closes_request_repository_on_failure() -> None:
    fallback = FakeSnapshotRepository(allowed_subject="cloudbase-user-a")
    repositories: list[FakeSnapshotRepository] = []

    async def factory() -> FakeSnapshotRepository:
        repository = FakeSnapshotRepository(
            allowed_subject="cloudbase-user-a",
            failure=RepositoryError("synthetic snapshot failure")
            if repositories
            else None,
        )
        repositories.append(repository)
        return repository

    app = _app(fallback=fallback, factory=factory)
    async with await _client(app) as client:
        response = await client.get(
            f"/api/v1/selection-sessions/{uuid4()}/snapshot", headers=_auth()
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert len(repositories) == 2
    assert repositories[1].snapshot_calls == 1
    assert repositories[1].close_calls == 1
    assert all(repository.close_calls == 1 for repository in repositories)


@pytest.mark.asyncio
async def test_injected_repository_fallback_remains_usable_and_is_not_closed() -> None:
    fallback = FakeSnapshotRepository(allowed_subject="cloudbase-user-a")
    app = _app(fallback=fallback)
    async with await _client(app) as client:
        response = await client.get(
            f"/api/v1/selection-sessions/{uuid4()}/snapshot", headers=_auth()
        )

    assert response.status_code == 200
    assert response.json() == SNAPSHOT
    assert fallback.resolve_calls == 1
    assert fallback.snapshot_calls == 1
    assert fallback.close_calls == 0


@pytest.mark.asyncio
async def test_authenticated_snapshot_enforces_owner_and_preserves_payload() -> None:
    fallback = FakeSnapshotRepository(allowed_subject="cloudbase-user-a")
    repositories: list[FakeSnapshotRepository] = []

    async def factory() -> FakeSnapshotRepository:
        repository = FakeSnapshotRepository(allowed_subject="cloudbase-user-a")
        repositories.append(repository)
        return repository

    app = _app(fallback=fallback, factory=factory)
    session_id = uuid4()
    async with await _client(app) as client:
        own = await client.get(
            f"/api/v1/selection-sessions/{session_id}/snapshot", headers=_auth()
        )
        other = await client.get(
            f"/api/v1/selection-sessions/{session_id}/snapshot",
            headers=_auth("valid-token-b"),
        )

    assert own.status_code == 200
    assert own.json() == SNAPSHOT
    assert other.status_code == 403
    assert other.json()["error"]["code"] == "resource_not_owned"


@pytest.mark.asyncio
async def test_anonymous_snapshot_still_uses_x_client_id_compatibility() -> None:
    anonymous_client_id = uuid4()
    fallback = FakeSnapshotRepository(allowed_anonymous_client_id=anonymous_client_id)
    app = _app(fallback=fallback)
    async with await _client(app) as client:
        response = await client.get(
            f"/api/v1/selection-sessions/{uuid4()}/snapshot",
            headers={"X-Client-Id": str(anonymous_client_id)},
        )

    assert response.status_code == 200
    assert response.json() == SNAPSHOT
    assert fallback.snapshot_calls == 1
    assert fallback.close_calls == 0
