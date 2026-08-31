"""Selection routes must use the request-scoped repository dependency."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from guancha_api.auth.fake import FakeTokenVerifier
from guancha_api.auth.models import AppUser, OwnerContext
from guancha_api.main import create_app


USER_ID = UUID("00000000-0000-0000-0000-0000000000a1")
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


class AppLifetimeRepositorySentinel:
    """Any business call here proves a route bypassed RequestRepository."""

    async def resolve_or_create_app_user(self, _subject: str) -> AppUser:
        raise AssertionError("the app-lifetime repository must not resolve users")

    def __getattr__(self, name: str):
        raise AssertionError(f"the app-lifetime repository must not call {name}")


class FreshSelectionRepository:
    def __init__(self, sessions: dict[UUID, dict[str, object]]) -> None:
        self.sessions = sessions
        self.business_calls: list[str] = []
        self.close_calls = 0

    async def resolve_or_create_app_user(self, cloudbase_user_id: str) -> AppUser:
        assert cloudbase_user_id == "cloudbase-user-a"
        now = datetime.now(timezone.utc)
        return AppUser(
            id=USER_ID,
            cloudbase_user_id=cloudbase_user_id,
            created_at=now,
            updated_at=now,
        )

    async def create_selection_session(
        self,
        *,
        session_id: UUID,
        client_id: OwnerContext,
        idempotency_key: UUID,
        request_hash: str,
        need: dict[str, object],
        recent_preference_evidence: tuple[dict[str, object], ...],
        expires_at: datetime,
    ) -> tuple[dict[str, object], bool]:
        del idempotency_key, request_hash, recent_preference_evidence
        self.business_calls.append("create_selection_session")
        assert client_id.user_id == USER_ID
        now = datetime.now(timezone.utc)
        row = {
            "id": session_id,
            "anonymous_client_id": None,
            "need": need,
            "expires_at": expires_at,
            "created_at": now,
            "updated_at": now,
        }
        self.sessions[session_id] = row
        return row, True

    async def get_selection_session_for_client(
        self, *, session_id: UUID, client_id: OwnerContext
    ) -> dict[str, object]:
        self.business_calls.append("get_selection_session")
        assert client_id.user_id == USER_ID
        return self.sessions[session_id]

    async def selection_snapshot_for_client(
        self, *, session_id: UUID, client_id: OwnerContext
    ) -> dict[str, object]:
        self.business_calls.append("selection_snapshot")
        assert client_id.user_id == USER_ID
        assert session_id in self.sessions
        return SNAPSHOT

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_authenticated_selection_routes_bypass_app_repository_and_close_fresh_repositories() -> None:
    fallback = AppLifetimeRepositorySentinel()
    sessions: dict[UUID, dict[str, object]] = {}
    fresh_repositories: list[FreshSelectionRepository] = []

    async def factory() -> FreshSelectionRepository:
        repository = FreshSelectionRepository(sessions)
        fresh_repositories.append(repository)
        return repository

    app = create_app(
        repository=fallback,
        worker_repository_factory=factory,
        token_verifier=FakeTokenVerifier(),
    )
    headers = {
        "Authorization": "Bearer valid-token-a",
        "Idempotency-Key": str(uuid4()),
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/selection-sessions",
            headers=headers,
            json={"need": {"taste_text": "fresh"}},
        )
        assert created.status_code == 201, created.text
        session_id = UUID(created.json()["id"])

        restored = await client.get(
            f"/api/v1/selection-sessions/{session_id}",
            headers={"Authorization": headers["Authorization"]},
        )
        snapshot = await client.get(
            f"/api/v1/selection-sessions/{session_id}/snapshot",
            headers={"Authorization": headers["Authorization"]},
        )

    assert restored.status_code == 200, restored.text
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json() == SNAPSHOT
    assert fallback.__dict__ == {}

    # Each authenticated request resolves the user and obtains its own
    # request repository.  Both instances are closed by their dependencies.
    assert len(fresh_repositories) == 6
    assert all(repository.close_calls == 1 for repository in fresh_repositories)
    assert fresh_repositories[1].business_calls == ["create_selection_session"]
    assert fresh_repositories[3].business_calls == ["get_selection_session"]
    assert fresh_repositories[5].business_calls == ["selection_snapshot"]
