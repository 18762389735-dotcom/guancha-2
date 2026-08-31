"""P9-4A authenticated preference persistence and Selection discovery tests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

from guancha_api.auth.fake import FakeTokenVerifier
from guancha_api.auth.models import AppUser
from guancha_api.main import create_app
from guancha_api.repositories.postgres import (
    PreferenceRevisionConflict,
    StoredPreferenceEvidence,
    StoredSelectionSessionSummary,
    StoredUserPreferences,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
USER_A = "cloudbase-user-a"
USER_B = "cloudbase-user-b"


class FakePreferenceRepository:
    """Connection-scoped fake with shared state for API contract tests."""

    def __init__(self, shared: dict[str, object], *, fail_preferences: bool = False) -> None:
        self.shared = shared
        self.fail_preferences = fail_preferences
        self.close_calls = 0

    @property
    def users(self) -> dict[str, AppUser]:
        return self.shared.setdefault("users", {})  # type: ignore[return-value]

    @property
    def preferences(self) -> dict[UUID, StoredUserPreferences]:
        return self.shared.setdefault("preferences", {})  # type: ignore[return-value]

    @property
    def evidence(self) -> dict[tuple[UUID, str], StoredPreferenceEvidence]:
        return self.shared.setdefault("evidence", {})  # type: ignore[return-value]

    @property
    def sessions(self) -> list[StoredSelectionSessionSummary]:
        return self.shared.setdefault("sessions", [])  # type: ignore[return-value]

    async def close(self) -> None:
        self.close_calls += 1

    async def resolve_or_create_app_user(self, cloudbase_user_id: str) -> AppUser:
        existing = self.users.get(cloudbase_user_id)
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc)
        created = AppUser(id=uuid4(), cloudbase_user_id=cloudbase_user_id, created_at=now, updated_at=now)
        self.users[cloudbase_user_id] = created
        return created

    async def get_user_preferences(self, *, user_id: UUID) -> StoredUserPreferences | None:
        if self.fail_preferences:
            raise RuntimeError("synthetic preference read failure")
        return self.preferences.get(user_id)

    async def put_user_preferences(
        self, *, user_id: UUID, profile: dict[str, object], expected_revision: int
    ) -> StoredUserPreferences:
        if self.fail_preferences:
            raise RuntimeError("synthetic preference write failure")
        existing = self.preferences.get(user_id)
        actual = existing.revision if existing is not None else 0
        if actual != expected_revision:
            raise PreferenceRevisionConflict("Preference revision does not match")
        now = datetime.now(timezone.utc)
        row = StoredUserPreferences(
            user_id=user_id,
            profile=profile,
            schema_version=1,
            revision=actual + 1,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.preferences[user_id] = row
        return row

    async def list_user_preference_evidence(
        self, *, user_id: UUID, limit: int = 12
    ) -> tuple[StoredPreferenceEvidence, ...]:
        threshold = datetime.now(timezone.utc) - timedelta(days=90)
        rows = [
            value for (owner, _), value in self.evidence.items()
            if owner == user_id and value.created_at >= threshold
        ]
        rows.sort(key=lambda value: (value.created_at, value.id), reverse=True)
        return tuple(rows[:limit])

    async def put_user_preference_evidence(
        self, *, user_id: UUID, evidence: tuple[dict[str, object], ...]
    ) -> tuple[StoredPreferenceEvidence, ...]:
        for item in evidence:
            source = str(item["source_brew_session_id"])
            existing = self.evidence.get((user_id, source))
            self.evidence[(user_id, source)] = StoredPreferenceEvidence(
                id=existing.id if existing is not None else item["id"],
                user_id=user_id,
                target_type=str(item["target_type"]),
                target_value=str(item["target_value"]),
                polarity=str(item["polarity"]),
                confidence=str(item["confidence"]),
                issue_source=str(item["issue_source"]),
                source_brew_session_id=source,
                created_at=item["created_at"],  # type: ignore[arg-type]
            )
        return await self.list_user_preference_evidence(user_id=user_id)

    async def list_authenticated_selection_sessions(
        self, *, user_id: UUID, limit: int = 20
    ) -> tuple[StoredSelectionSessionSummary, ...]:
        rows = [value for value in self.sessions if value.user_id == user_id]  # type: ignore[attr-defined]
        rows.sort(key=lambda value: (value.created_at, value.id), reverse=True)
        return tuple(rows[:limit])


def make_app(*, repository=None, factory=None):
    return create_app(
        repository=repository,
        worker_repository_factory=factory,
        token_verifier=FakeTokenVerifier(),
    )


def auth(token: str = "valid-token-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def request_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def profile(*, tea: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "o1": {"tea": list(tea), "coffee": [], "milk": [], "juice": []},
        "o2": {"sweetness": 50, "flavors": []},
    }


def evidence_item(*, source: str = "record-123", evidence_id: UUID | None = None) -> dict[str, object]:
    return {
        "id": str(evidence_id or uuid4()),
        "target_type": "aroma",
        "target_value": "floral",
        "polarity": "positive",
        "confidence": "low",
        "issue_source": "brewing",
        "source_brew_session_id": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.asyncio
async def test_preferences_require_bearer_and_return_canonical_absent_state() -> None:
    shared: dict[str, object] = {}
    repository = FakePreferenceRepository(shared)
    app = make_app(repository=repository)
    async with await request_client(app) as client:
        missing = await client.get("/api/v1/me/preferences")
        invalid = await client.get("/api/v1/me/preferences", headers={"Authorization": "Bearer invalid-token"})
        fresh = await client.get("/api/v1/me/preferences", headers=auth())

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert fresh.status_code == 200
    assert fresh.json()["profile"] == profile()
    assert fresh.json()["revision"] == 0
    assert fresh.json()["updated_at"] is None
    assert repository.preferences == {}


@pytest.mark.asyncio
async def test_preferences_compare_and_swap_and_server_validation() -> None:
    shared: dict[str, object] = {}
    repository = FakePreferenceRepository(shared)
    app = make_app(repository=repository)
    async with await request_client(app) as client:
        created = await client.put(
            "/api/v1/me/preferences", headers=auth(), json={"profile": profile(tea=("绿茶",)), "expected_revision": 0}
        )
        updated = await client.put(
            "/api/v1/me/preferences", headers=auth(), json={"profile": profile(tea=("乌龙茶",)), "expected_revision": 1}
        )
        stale = await client.put(
            "/api/v1/me/preferences", headers=auth(), json={"profile": profile(tea=("红茶",)), "expected_revision": 1}
        )
        invalid_value = await client.put(
            "/api/v1/me/preferences", headers=auth(), json={"profile": profile(tea=("not-supported",)), "expected_revision": 2}
        )
        extra_state = await client.put(
            "/api/v1/me/preferences", headers=auth(), json={"profile": {**profile(), "warehouse": []}, "expected_revision": 2}
        )

    assert created.status_code == 200 and created.json()["revision"] == 1
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "preferences_revision_conflict"
    assert invalid_value.status_code == 422
    assert extra_state.status_code == 422


@pytest.mark.asyncio
async def test_preferences_and_evidence_are_isolated_between_users() -> None:
    shared: dict[str, object] = {}
    repository = FakePreferenceRepository(shared)
    app = make_app(repository=repository)
    async with await request_client(app) as client:
        saved = await client.put(
            "/api/v1/me/preferences", headers=auth(), json={"profile": profile(tea=("绿茶",)), "expected_revision": 0}
        )
        saved_evidence = await client.put(
            "/api/v1/me/preference-evidence", headers=auth(), json={"items": [evidence_item()]}
        )
        other_preferences = await client.get("/api/v1/me/preferences", headers=auth("valid-token-b"))
        other_evidence = await client.get("/api/v1/me/preference-evidence", headers=auth("valid-token-b"))
        other_saved = await client.put(
            "/api/v1/me/preferences", headers=auth("valid-token-b"), json={"profile": profile(tea=("红茶",)), "expected_revision": 0}
        )

    assert saved.status_code == saved_evidence.status_code == other_saved.status_code == 200
    assert other_preferences.json()["revision"] == 0
    assert other_evidence.json() == []
    assert saved_evidence.json()[0]["source_brew_session_id"] == "record-123"
    assert saved.json()["profile"]["o1"]["tea"] == ["绿茶"]


@pytest.mark.asyncio
async def test_preference_evidence_put_is_source_deduplicated_and_validated() -> None:
    shared: dict[str, object] = {}
    repository = FakePreferenceRepository(shared)
    app = make_app(repository=repository)
    async with await request_client(app) as client:
        first = evidence_item(evidence_id=uuid4())
        second = evidence_item(evidence_id=uuid4())
        initial = await client.put("/api/v1/me/preference-evidence", headers=auth(), json={"items": [first]})
        replay = await client.put("/api/v1/me/preference-evidence", headers=auth(), json={"items": [second]})
        invalid = await client.put(
            "/api/v1/me/preference-evidence", headers=auth(), json={"items": [{**evidence_item(), "target_value": "not valid"}]}
        )
        duplicate_batch = await client.put(
            "/api/v1/me/preference-evidence", headers=auth(), json={"items": [evidence_item(source="brew-a"), evidence_item(source="brew-a")]}
        )

    assert initial.status_code == replay.status_code == 200
    assert len(replay.json()) == 1
    assert replay.json()[0]["id"] == str(first["id"])
    assert invalid.status_code == duplicate_batch.status_code == 422


@pytest.mark.asyncio
async def test_selection_discovery_is_authenticated_bounded_and_excludes_anonymous() -> None:
    shared: dict[str, object] = {}
    repository = FakePreferenceRepository(shared)
    user_a = await repository.resolve_or_create_app_user(USER_A)
    user_b = await repository.resolve_or_create_app_user(USER_B)
    now = datetime.now(timezone.utc)
    repository.sessions.extend([
        StoredSelectionSessionSummary(id=uuid4(), user_id=user_a.id, need={"taste_text": "new"}, created_at=now, updated_at=now),
        StoredSelectionSessionSummary(id=uuid4(), user_id=user_a.id, need={"taste_text": "old"}, created_at=now - timedelta(minutes=1), updated_at=now),
        StoredSelectionSessionSummary(id=uuid4(), user_id=user_b.id, need={"taste_text": "other"}, created_at=now, updated_at=now),
    ])
    app = make_app(repository=repository)
    async with await request_client(app) as client:
        missing = await client.get("/api/v1/me/selection-sessions")
        listed = await client.get("/api/v1/me/selection-sessions?limit=1", headers=auth())
        too_many = await client.get("/api/v1/me/selection-sessions?limit=51", headers=auth())

    assert missing.status_code == 401
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["need"]["taste_text"] == "new"
    assert too_many.status_code == 422
    assert all(item["need"]["taste_text"] != "other" for item in listed.json())


@pytest.mark.asyncio
async def test_new_me_persistence_uses_factory_and_closes_each_repository() -> None:
    shared: dict[str, object] = {}
    repositories: list[FakePreferenceRepository] = []

    async def factory() -> FakePreferenceRepository:
        repository = FakePreferenceRepository(shared)
        repositories.append(repository)
        return repository

    fallback = FakePreferenceRepository(shared)
    app = make_app(repository=fallback, factory=factory)
    async with await request_client(app) as client:
        response = await client.get("/api/v1/me/preferences", headers=auth())

    assert response.status_code == 200
    assert len(repositories) == 2
    assert [repository.close_calls for repository in repositories] == [1, 1]
    assert fallback.close_calls == 0


@pytest.mark.asyncio
async def test_new_me_persistence_closes_factory_repository_on_failure() -> None:
    shared: dict[str, object] = {}
    repositories: list[FakePreferenceRepository] = []

    async def factory() -> FakePreferenceRepository:
        repository = FakePreferenceRepository(shared, fail_preferences=len(repositories) > 0)
        repositories.append(repository)
        return repository

    app = make_app(factory=factory)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/me/preferences", headers=auth())

    assert response.status_code == 500
    assert len(repositories) == 2
    assert [repository.close_calls for repository in repositories] == [1, 1]


@pytest.mark.asyncio
async def test_injected_repository_fallback_remains_usable_for_preferences() -> None:
    repository = FakePreferenceRepository({})
    app = make_app(repository=repository, factory=None)
    async with await request_client(app) as client:
        response = await client.get("/api/v1/me/preferences", headers=auth())

    assert response.status_code == 200
    assert repository.close_calls == 0


@pytest_asyncio.fixture
async def postgres_repository():
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for P9-4A PostgreSQL integration tests")
    connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
    migration_directory = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    migration = "\n".join(path.read_text(encoding="utf-8") for path in sorted(migration_directory.glob("*.sql")))
    async with connection.cursor() as cursor:
        await cursor.execute("drop schema public cascade")
        await cursor.execute("create schema public")
        await cursor.execute(migration)
    await connection.commit()
    await connection.set_autocommit(True)
    from guancha_api.repositories.postgres import PostgresPhase2Repository

    repository = PostgresPhase2Repository(connection)
    try:
        yield repository
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_postgres_p9_4a_preferences_evidence_and_selection_discovery(postgres_repository) -> None:
    app = make_app(repository=postgres_repository)
    async with await request_client(app) as client:
        fresh = await client.get("/api/v1/me/preferences", headers=auth())
        created = await client.put(
            "/api/v1/me/preferences", headers=auth(), json={"profile": profile(tea=("绿茶",)), "expected_revision": 0}
        )
        stale = await client.put(
            "/api/v1/me/preferences", headers=auth(), json={"profile": profile(tea=("乌龙茶",)), "expected_revision": 0}
        )
        evidence = await client.put(
            "/api/v1/me/preference-evidence", headers=auth(), json={"items": [evidence_item()]}
        )
        session = await client.post(
            "/api/v1/selection-sessions",
            headers={**auth(), "Idempotency-Key": str(uuid4())},
            json={"need": {"taste_text": "floral"}},
        )
        discovered = await client.get("/api/v1/me/selection-sessions", headers=auth())

    assert fresh.status_code == 200 and fresh.json()["revision"] == 0
    assert created.status_code == 200 and created.json()["revision"] == 1
    assert stale.status_code == 409
    assert evidence.status_code == 200 and len(evidence.json()) == 1
    assert session.status_code == 201
    assert [item["id"] for item in discovered.json()] == [session.json()["id"]]


@pytest.mark.asyncio
async def test_postgres_app_user_cascade_removes_p9_4a_rows(postgres_repository) -> None:
    user = await postgres_repository.resolve_or_create_app_user(USER_A)
    row = await postgres_repository.put_user_preferences(user_id=user.id, profile=profile(), expected_revision=0)
    await postgres_repository.put_user_preference_evidence(user_id=user.id, evidence=(evidence_item(),))
    async with postgres_repository._connection.cursor() as cursor:  # noqa: SLF001 - integration assertion
        await cursor.execute("delete from app_users where id=%s", (user.id,))
        await cursor.execute("select count(*) as count from user_preferences where user_id=%s", (user.id,))
        preference_count = (await cursor.fetchone())["count"]
        await cursor.execute("select count(*) as count from user_preference_evidence where user_id=%s", (user.id,))
        evidence_count = (await cursor.fetchone())["count"]
    assert row.revision == 1
    assert preference_count == 0
    assert evidence_count == 0
