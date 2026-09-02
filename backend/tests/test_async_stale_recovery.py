"""Focused durable recovery tests for asynchronous extraction Jobs."""

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

from guancha_api.application.extraction_recovery import (
    DEFAULT_EXTRACTION_STALE_AFTER_SECONDS,
    extraction_stale_after_seconds_from_environment,
    extraction_stale_before_from_environment,
)
from guancha_api.application.task_runners import ManualTaskRunner
from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.main import create_app
from guancha_api.repositories.postgres import PostgresPhase2Repository
from guancha_api.schemas.contracts import ErrorCode, JobState, ProcessingMode


DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def repository() -> PostgresPhase2Repository:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for stale recovery integration tests")
    connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
    migrations = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    async with connection.cursor() as cursor:
        await cursor.execute("drop schema public cascade")
        await cursor.execute("create schema public")
        await cursor.execute(
            "\n".join(path.read_text(encoding="utf-8") for path in sorted(migrations.glob("*.sql")))
        )
    await connection.commit()
    await connection.set_autocommit(True)
    try:
        yield PostgresPhase2Repository(connection)
    finally:
        await connection.close()


async def _job(repository: PostgresPhase2Repository) -> tuple[UUID, UUID]:
    client_id, session_id, candidate_id = uuid4(), uuid4(), uuid4()
    await repository.create_selection_session(
        session_id=session_id,
        client_id=client_id,
        idempotency_key=uuid4(),
        request_hash="a" * 64,
        need={"taste_text": "controlled stale recovery"},
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    await repository.create_candidate(
        candidate_id=candidate_id,
        session_id=session_id,
        client_id=client_id,
        label="A",
        display_name="Controlled recovery tea",
        idempotency_key=uuid4(),
        request_hash="b" * 64,
    )
    result = await repository.create_image_and_initial_job(
        image_id=uuid4(),
        job_id=uuid4(),
        candidate_id=candidate_id,
        client_id=client_id,
        idempotency_key=uuid4(),
        content_type="image/png",
        size_bytes=8,
        sha256="c" * 64,
        request_hash="c" * 64,
        width=2,
        height=2,
        processing_mode=ProcessingMode.TEST_FIXTURE,
    )
    return client_id, result.job.id


async def _age_job(
    repository: PostgresPhase2Repository,
    *,
    job_id: UUID,
    seconds: float,
    processing: bool = False,
) -> None:
    timestamp = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    if processing:
        assert await repository.claim_job(job_id=job_id)
    async with repository._connection.cursor() as cursor:
        await cursor.execute(
            """update analysis_jobs
               set created_at=%s, claimed_at=%s, started_at=%s, updated_at=%s
               where id=%s""",
            (timestamp, timestamp if processing else None, timestamp if processing else None, timestamp, job_id),
        )
    await repository._connection.commit()


async def _poll_status(
    repository: PostgresPhase2Repository, *, client_id: UUID, job_id: UUID
) -> dict[str, object]:
    app = create_app(
        repository=repository,
        temporary_storage=InMemoryTemporaryPrivateStorage(),
        task_runner=ManualTaskRunner(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/jobs/{job_id}", headers={"X-Client-Id": str(client_id)}
        )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_poll_keeps_fresh_queued_job_queued(
    repository: PostgresPhase2Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "300")
    client_id, job_id = await _job(repository)

    job = await _poll_status(repository, client_id=client_id, job_id=job_id)

    assert job["status"] == "queued"


@pytest.mark.asyncio
async def test_poll_reconciles_stale_queued_job_to_failed(
    repository: PostgresPhase2Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "300")
    client_id, job_id = await _job(repository)
    await _age_job(repository, job_id=job_id, seconds=301)

    job = await _poll_status(repository, client_id=client_id, job_id=job_id)

    assert job["status"] == "failed"
    assert job["error_code"] == ErrorCode.WORKER_INTERRUPTED.value
    assert not await repository.claim_job(job_id=job_id)


@pytest.mark.asyncio
async def test_poll_keeps_processing_job_between_90_and_300_seconds(
    repository: PostgresPhase2Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "300")
    client_id, job_id = await _job(repository)
    await _age_job(repository, job_id=job_id, seconds=120, processing=True)

    job = await _poll_status(repository, client_id=client_id, job_id=job_id)

    assert job["status"] == "processing"


@pytest.mark.asyncio
async def test_poll_reconciles_stale_processing_job_to_failed(
    repository: PostgresPhase2Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "300")
    client_id, job_id = await _job(repository)
    await _age_job(repository, job_id=job_id, seconds=301, processing=True)

    job = await _poll_status(repository, client_id=client_id, job_id=job_id)

    assert job["status"] == "failed"
    assert job["error_code"] == ErrorCode.WORKER_INTERRUPTED.value


@pytest.mark.asyncio
async def test_conditional_recovery_preserves_completed_job(
    repository: PostgresPhase2Repository
) -> None:
    _, job_id = await _job(repository)
    await _age_job(repository, job_id=job_id, seconds=301)
    async with repository._connection.cursor() as cursor:
        await cursor.execute(
            "update analysis_jobs set status='completed', stage='completed' where id=%s",
            (job_id,),
        )
    await repository._connection.commit()

    changed = await repository.recover_stale_analysis_job(
        job_id=job_id,
        stale_before=extraction_stale_before_from_environment(),
    )

    assert changed is False
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select status from analysis_jobs where id=%s", (job_id,))
        assert (await cursor.fetchone())["status"] == "completed"


@pytest.mark.asyncio
async def test_startup_recovery_uses_the_same_300_second_semantics(
    repository: PostgresPhase2Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "300")
    _, fresh_job_id = await _job(repository)
    _, stale_job_id = await _job(repository)
    await _age_job(repository, job_id=fresh_job_id, seconds=120, processing=True)
    await _age_job(repository, job_id=stale_job_id, seconds=301, processing=True)

    changed = await repository.recover_interrupted_jobs(
        stale_before=extraction_stale_before_from_environment()
    )

    assert changed == 1
    assert (await repository.get_claimed_job(job_id=fresh_job_id)).status is JobState.PROCESSING
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select status from analysis_jobs where id=%s", (stale_job_id,))
        assert (await cursor.fetchone())["status"] == "failed"


def test_stale_horizon_default_custom_and_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", raising=False)
    assert extraction_stale_after_seconds_from_environment() == DEFAULT_EXTRACTION_STALE_AFTER_SECONDS
    monkeypatch.setenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "301")
    assert extraction_stale_after_seconds_from_environment() == 301.0
    monkeypatch.setenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "180")
    with pytest.raises(RuntimeError, match="must exceed"):
        extraction_stale_after_seconds_from_environment()
    monkeypatch.setenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "invalid")
    with pytest.raises(RuntimeError, match="must be numeric"):
        extraction_stale_after_seconds_from_environment()
