"""P9-4B authenticated Warehouse and Brew Journal contract tests."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
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
    BrewJournalRevisionConflict,
    OwnershipDenied,
    ResourceNotFound,
    StoredBrewJournalEntry,
    StoredWarehouseTea,
    WarehouseRevisionConflict,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
USER_A = "p9-4b-user-a"
USER_B = "p9-4b-user-b"
TEA_ID = UUID("7c8f6f74-83a1-4c4e-9bb9-c3e5f37a0101")
JOURNAL_ID = UUID("7c8f6f74-83a1-4c4e-9bb9-c3e5f37a0102")


def now() -> datetime:
    return datetime.now(timezone.utc)


def warehouse_input(*, name: str = "春日乌龙") -> dict[str, object]:
    return {
        "name": name,
        "tea_category": "乌龙茶",
        "tea_subtype": "清香型",
        "origin": "安溪",
        "roast_or_style": "轻火",
        "aroma": "兰花",
        "status": "drinking",
        "source_type": "manual",
        "facts": ["待补充"],
        "risks": ["产地与年份未记录"],
        "risk_flags": [],
    }


def journal_input(tea_id: UUID = TEA_ID, *, brewed_on: str = "2026-08-31") -> dict[str, object]:
    return {
        "tea_id": str(tea_id),
        "brewed_on": brewed_on,
        "infusions": [{"number": 1, "suggested": 10, "actual": 11}],
        "plan": {"ware": "盖碗", "water": "110 ml", "grams": "5 g", "temp": "95℃"},
        "feedback": {"taste": "喜欢", "strength": "刚好", "tags": ["清爽"], "aroma": ["兰花"], "score": 4, "advanced": {"回甘": "明显"}},
        "suggestion": "暂时保持本次参数",
    }


class FakePostPurchaseRepository:
    def __init__(self, shared: dict[str, object]) -> None:
        self.shared = shared
        self.close_calls = 0

    @property
    def users(self) -> dict[str, AppUser]:
        return self.shared.setdefault("users", {})  # type: ignore[return-value]

    @property
    def teas(self) -> dict[UUID, StoredWarehouseTea]:
        return self.shared.setdefault("teas", {})  # type: ignore[return-value]

    @property
    def entries(self) -> dict[UUID, StoredBrewJournalEntry]:
        return self.shared.setdefault("entries", {})  # type: ignore[return-value]

    async def close(self) -> None:
        self.close_calls += 1

    async def resolve_or_create_app_user(self, cloudbase_user_id: str) -> AppUser:
        existing = self.users.get(cloudbase_user_id)
        if existing is not None:
            return existing
        created = AppUser(id=uuid4(), cloudbase_user_id=cloudbase_user_id, created_at=now(), updated_at=now())
        self.users[cloudbase_user_id] = created
        return created

    async def list_user_warehouse_teas(self, *, user_id: UUID, limit: int = 100) -> tuple[StoredWarehouseTea, ...]:
        rows = [row for row in self.teas.values() if row.user_id == user_id]
        rows.sort(key=lambda row: (row.updated_at, row.id), reverse=True)
        return tuple(rows[:limit])

    async def put_user_warehouse_tea(self, *, user_id: UUID, tea_id: UUID, tea: dict[str, object], expected_revision: int) -> StoredWarehouseTea:
        existing = self.teas.get(tea_id)
        if expected_revision == 0 and existing is not None:
            if existing.user_id != user_id:
                raise OwnershipDenied("Warehouse tea belongs to another owner")
            raise WarehouseRevisionConflict("Warehouse tea revision does not match")
        if expected_revision > 0 and (existing is None or existing.user_id != user_id):
            if existing is not None:
                raise OwnershipDenied("Warehouse tea belongs to another owner")
            raise ResourceNotFound("Warehouse tea not found")
        if expected_revision > 0 and existing.revision != expected_revision:
            raise WarehouseRevisionConflict("Warehouse tea revision does not match")
        timestamp = now()
        row = StoredWarehouseTea(
            id=tea_id, user_id=user_id, name=str(tea["name"]), tea_category=tea.get("tea_category"),
            tea_subtype=tea.get("tea_subtype"), origin=tea.get("origin"), roast_or_style=tea.get("roast_or_style"),
            aroma=tea.get("aroma"), status=str(tea["status"]), source_type=str(tea["source_type"]),
            selection_session_id=tea.get("selection_session_id"), candidate_id=tea.get("candidate_id"),
            extraction_version_id=tea.get("extraction_version_id"), decision_version_id=tea.get("decision_version_id"),
            facts=list(tea.get("facts") or []), risks=list(tea.get("risks") or []), risk_flags=list(tea.get("risk_flags") or []),
            joined_at=existing.joined_at if existing else timestamp, revision=(existing.revision + 1 if existing else 1),
            created_at=existing.created_at if existing else timestamp, updated_at=timestamp,
        )
        self.teas[tea_id] = row
        return row

    async def list_user_brew_journal_entries(self, *, user_id: UUID, limit: int = 365) -> tuple[StoredBrewJournalEntry, ...]:
        rows = [row for row in self.entries.values() if row.user_id == user_id]
        rows.sort(key=lambda row: (row.brewed_on, row.created_at, row.id), reverse=True)
        return tuple(rows[:limit])

    async def put_user_brew_journal_entry(self, *, user_id: UUID, entry_id: UUID, entry: dict[str, object], expected_revision: int) -> StoredBrewJournalEntry:
        tea = self.teas.get(entry["tea_id"])
        if tea is None or tea.user_id != user_id:
            raise OwnershipDenied("Journal tea belongs to another owner")
        existing = self.entries.get(entry_id)
        if expected_revision == 0 and existing is not None:
            if existing.user_id != user_id:
                raise OwnershipDenied("Brew Journal entry belongs to another owner")
            raise BrewJournalRevisionConflict("Brew Journal revision does not match")
        if expected_revision > 0 and (existing is None or existing.user_id != user_id):
            if existing is not None:
                raise OwnershipDenied("Brew Journal entry belongs to another owner")
            raise ResourceNotFound("Brew Journal entry not found")
        if expected_revision > 0 and existing.revision != expected_revision:
            raise BrewJournalRevisionConflict("Brew Journal revision does not match")
        timestamp = now()
        row = StoredBrewJournalEntry(
            id=entry_id, user_id=user_id, tea_id=entry["tea_id"], brewed_on=date.fromisoformat(str(entry["brewed_on"])),
            infusions=list(entry.get("infusions") or []), plan=dict(entry.get("plan") or {}), feedback=dict(entry.get("feedback") or {}),
            suggestion=entry.get("suggestion"), revision=(existing.revision + 1 if existing else 1),
            created_at=existing.created_at if existing else timestamp, updated_at=timestamp,
        )
        self.entries[entry_id] = row
        return row


def make_app(*, repository=None, factory=None):
    return create_app(repository=repository, worker_repository_factory=factory, token_verifier=FakeTokenVerifier())


def auth(token: str = "valid-token-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def request_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def app_with_factory(shared: dict[str, object]):
    repositories: list[FakePostPurchaseRepository] = []

    async def factory() -> FakePostPurchaseRepository:
        repository = FakePostPurchaseRepository(shared)
        repositories.append(repository)
        return repository

    return make_app(factory=factory), repositories


@pytest.mark.asyncio
async def test_warehouse_requires_auth_and_supports_atomic_cas_and_isolation() -> None:
    shared: dict[str, object] = {}
    app, _repositories = await app_with_factory(shared)
    async with await request_client(app) as client:
        missing = await client.get("/api/v1/me/warehouse")
        created = await client.put(f"/api/v1/me/warehouse/{TEA_ID}", headers=auth(), json={"tea": warehouse_input(), "expected_revision": 0})
        stale_create = await client.put(f"/api/v1/me/warehouse/{TEA_ID}", headers=auth(), json={"tea": warehouse_input(), "expected_revision": 0})
        updated = await client.put(f"/api/v1/me/warehouse/{TEA_ID}", headers=auth(), json={"tea": {**warehouse_input(name="更新后的茶"), "status": "paused"}, "expected_revision": 1})
        stale_update = await client.put(f"/api/v1/me/warehouse/{TEA_ID}", headers=auth(), json={"tea": warehouse_input(), "expected_revision": 1})
        other_read = await client.get("/api/v1/me/warehouse", headers=auth("valid-token-b"))
        other_write = await client.put(f"/api/v1/me/warehouse/{TEA_ID}", headers=auth("valid-token-b"), json={"tea": warehouse_input(), "expected_revision": 2})
        invalid_extra = await client.put(f"/api/v1/me/warehouse/{uuid4()}", headers=auth(), json={"tea": {**warehouse_input(), "records": 1}, "expected_revision": 0})

    assert missing.status_code == 401
    assert created.status_code == 200 and created.json()["revision"] == 1
    assert stale_create.status_code == 409 and stale_create.json()["error"]["code"] == "warehouse_revision_conflict"
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    assert stale_update.status_code == 409
    assert other_read.status_code == 200 and other_read.json() == []
    assert other_write.status_code == 403 and other_write.json()["error"]["code"] == "resource_not_owned"
    assert invalid_extra.status_code == 422


@pytest.mark.asyncio
async def test_journal_requires_owned_tea_and_supports_cas_and_isolation() -> None:
    shared: dict[str, object] = {}
    app, _repositories = await app_with_factory(shared)
    async with await request_client(app) as client:
        assert (await client.get("/api/v1/me/brew-journal", headers=auth())).json() == []
        await client.put(f"/api/v1/me/warehouse/{TEA_ID}", headers=auth(), json={"tea": warehouse_input(), "expected_revision": 0})
        created = await client.put(f"/api/v1/me/brew-journal/{JOURNAL_ID}", headers=auth(), json={"entry": journal_input(), "expected_revision": 0})
        updated = await client.put(f"/api/v1/me/brew-journal/{JOURNAL_ID}", headers=auth(), json={"entry": {**journal_input(), "suggestion": "更新"}, "expected_revision": 1})
        stale = await client.put(f"/api/v1/me/brew-journal/{JOURNAL_ID}", headers=auth(), json={"entry": journal_input(), "expected_revision": 1})
        other_read = await client.get("/api/v1/me/brew-journal", headers=auth("valid-token-b"))
        other_tea = await client.put(f"/api/v1/me/brew-journal/{uuid4()}", headers=auth("valid-token-b"), json={"entry": journal_input(), "expected_revision": 0})
        extra = await client.put(f"/api/v1/me/brew-journal/{uuid4()}", headers=auth(), json={"entry": {**journal_input(), "unexpected": True}, "expected_revision": 0})

    assert created.status_code == 200 and created.json()["revision"] == 1
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "brew_journal_revision_conflict"
    assert other_read.status_code == 200 and other_read.json() == []
    assert other_tea.status_code == 403
    assert extra.status_code == 422


@pytest.mark.asyncio
async def test_me_resource_factory_is_fresh_and_closed_for_every_request() -> None:
    shared: dict[str, object] = {}
    app, repositories = await app_with_factory(shared)
    fallback = FakePostPurchaseRepository(shared)
    app.state.repository = fallback
    async with await request_client(app) as client:
        first = await client.get("/api/v1/me/warehouse", headers=auth())
        second = await client.get("/api/v1/me/brew-journal", headers=auth())
    assert first.status_code == second.status_code == 200
    assert len(repositories) == 4
    assert all(repository.close_calls == 1 for repository in repositories)
    assert fallback.close_calls == 0


@pytest_asyncio.fixture
async def postgres_repository():
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for P9-4B PostgreSQL integration tests")
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
async def test_postgres_p9_4b_fk_cas_and_list_order(postgres_repository) -> None:
    user = await postgres_repository.resolve_or_create_app_user(USER_A)
    other = await postgres_repository.resolve_or_create_app_user(USER_B)
    first = await postgres_repository.put_user_warehouse_tea(user_id=user.id, tea_id=TEA_ID, tea=warehouse_input(), expected_revision=0)
    with pytest.raises(WarehouseRevisionConflict):
        await postgres_repository.put_user_warehouse_tea(user_id=user.id, tea_id=TEA_ID, tea=warehouse_input(), expected_revision=0)
    second = await postgres_repository.put_user_warehouse_tea(user_id=user.id, tea_id=TEA_ID, tea={**warehouse_input(name="更新后的茶"), "status": "paused"}, expected_revision=1)
    entry = await postgres_repository.put_user_brew_journal_entry(user_id=user.id, entry_id=JOURNAL_ID, entry=journal_input(), expected_revision=0)
    updated_entry = await postgres_repository.put_user_brew_journal_entry(user_id=user.id, entry_id=JOURNAL_ID, entry=journal_input(brewed_on="2026-08-31"), expected_revision=1)
    with pytest.raises(BrewJournalRevisionConflict):
        await postgres_repository.put_user_brew_journal_entry(user_id=user.id, entry_id=JOURNAL_ID, entry=journal_input(), expected_revision=1)
    with pytest.raises(OwnershipDenied):
        await postgres_repository.put_user_brew_journal_entry(user_id=other.id, entry_id=uuid4(), entry=journal_input(), expected_revision=0)
    teas = await postgres_repository.list_user_warehouse_teas(user_id=user.id)
    entries = await postgres_repository.list_user_brew_journal_entries(user_id=user.id)
    assert first.revision == 1 and second.revision == 2
    assert entry.revision == 1 and updated_entry.revision == 2
    assert [tea.id for tea in teas] == [TEA_ID]
    assert [item.id for item in entries] == [JOURNAL_ID]
