from __future__ import annotations

import os
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from guancha_api.repositories.postgres import (
    AiCallLog,
    CandidateImageLimitExceeded,
    CandidateLimitExceeded,
    IdempotencyConflict,
    OwnershipDenied,
    PostgresPhase2Repository,
)
from guancha_api.schemas.contracts import (
    ErrorCode,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceStrength,
    InformationStatus,
    JobState,
    ProcessingMode,
    VerificationStatus,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def repository() -> PostgresPhase2Repository:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
    migration_directory = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    migration = "\n".join(path.read_text(encoding="utf-8") for path in sorted(migration_directory.glob("*.sql")))
    async with connection.cursor() as cursor:
        await cursor.execute("drop schema public cascade")
        await cursor.execute("create schema public")
        await cursor.execute(migration)
    await connection.commit()
    await connection.set_autocommit(True)
    repository = PostgresPhase2Repository(connection)
    try:
        yield repository
    finally:
        await connection.close()


async def _candidate(repository: PostgresPhase2Repository) -> tuple[UUID, UUID]:
    client_id, session_id, candidate_id = uuid4(), uuid4(), uuid4()
    await repository.create_selection_session(session_id=session_id, client_id=client_id, idempotency_key=uuid4(), request_hash="a" * 64, need={"taste_text": "fresh"}, expires_at=datetime.now(timezone.utc))
    await repository.create_candidate(candidate_id=candidate_id, session_id=session_id, client_id=client_id, label="A", display_name="Tea", idempotency_key=uuid4(), request_hash="b" * 64)
    return client_id, candidate_id


async def test_async_pool_preserves_repository_connection_semantics(
    repository: PostgresPhase2Repository,
) -> None:
    pool = AsyncConnectionPool(
        conninfo=DATABASE_URL,
        kwargs={"autocommit": True, "row_factory": dict_row},
        min_size=1,
        max_size=1,
        timeout=5.0,
        open=False,
    )
    await pool.open()
    try:
        async with pool.connection() as connection:
            assert connection.autocommit is True
            pooled_repository = PostgresPhase2Repository(connection)
            app_user = await pooled_repository.resolve_or_create_app_user("pool-semantics-user")
            assert app_user.cloudbase_user_id == "pool-semantics-user"
            async with connection.cursor() as cursor:
                await cursor.execute("select %s as value", ("dict-row",))
                assert await cursor.fetchone() == {"value": "dict-row"}
    finally:
        await pool.close()


async def test_image_and_first_job_are_atomic_and_idempotent(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    request = dict(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/jpeg", size_bytes=32, sha256="a" * 64, request_hash="a" * 64,
        width=8, height=4, processing_mode=ProcessingMode.TEST_FIXTURE,
    )
    first = await repository.create_image_and_initial_job(**request)
    replay = await repository.create_image_and_initial_job(**{**request, "image_id": uuid4(), "job_id": uuid4()})

    assert first.created is True
    assert first.job.status is JobState.QUEUED
    assert replay.created is False
    assert replay.image.id == first.image.id
    assert replay.job.id == first.job.id


async def test_session_and_candidate_create_or_replay_persist_full_request(repository: PostgresPhase2Repository) -> None:
    client_id, session_key = uuid4(), uuid4()
    request = dict(session_id=uuid4(), client_id=client_id, idempotency_key=session_key, request_hash="d" * 64, need={"taste_text": "floral", "purpose_text": "gift"}, expires_at=datetime.now(timezone.utc))
    session, created = await repository.create_selection_session(**request)
    replay, replay_created = await repository.create_selection_session(**{**request, "session_id": uuid4()})
    assert created and not replay_created and replay["id"] == session["id"] and replay["need"] == request["need"]
    with pytest.raises(IdempotencyConflict):
        await repository.create_selection_session(**{**request, "session_id": uuid4(), "request_hash": "e" * 64})
    candidate_request = dict(candidate_id=uuid4(), session_id=session["id"], client_id=client_id, label="A", display_name="岩茶", idempotency_key=uuid4(), request_hash="f" * 64)
    candidate, candidate_created = await repository.create_candidate(**candidate_request)
    replay_candidate, replay_created = await repository.create_candidate(**{**candidate_request, "candidate_id": uuid4()})
    assert candidate_created and not replay_created and replay_candidate["id"] == candidate["id"] and candidate["display_name"] == "岩茶"


async def test_idempotency_key_cannot_be_reused_for_a_different_image(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    request = dict(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/png", size_bytes=32, sha256="b" * 64, request_hash="b" * 64,
        width=8, height=4, processing_mode=ProcessingMode.FAKE_PROVIDER,
    )
    await repository.create_image_and_initial_job(**request)
    with pytest.raises(IdempotencyConflict):
        await repository.create_image_and_initial_job(**{**request, "image_id": uuid4(), "job_id": uuid4(), "sha256": "c" * 64, "request_hash": "c" * 64})


async def test_job_is_visible_only_to_its_anonymous_client(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    result = await repository.create_image_and_initial_job(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/jpeg", size_bytes=32, sha256="d" * 64, request_hash="d" * 64,
        width=8, height=4, processing_mode=ProcessingMode.TEST_FIXTURE,
    )
    assert (await repository.get_job_for_client(job_id=result.job.id, client_id=client_id)).id == result.job.id
    with pytest.raises(OwnershipDenied):
        await repository.get_job_for_client(job_id=result.job.id, client_id=uuid4())


async def test_extraction_version_is_insert_only(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    result = await repository.create_image_and_initial_job(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/jpeg", size_bytes=32, sha256="e" * 64, request_hash="e" * 64,
        width=8, height=4, processing_mode=ProcessingMode.TEST_FIXTURE,
    )
    version_id = uuid4()
    await repository.create_extraction_version(
        version_id=version_id, candidate_id=candidate_id, image_id=result.image.id,
        client_id=client_id, schema_version="phase2-v1"
    )
    async with repository._connection.cursor() as cursor:  # verifies the persisted snapshot, not an in-memory fake
        await cursor.execute("select status, schema_version from extraction_versions where id = %s", (version_id,))
        assert await cursor.fetchone() == {"status": "completed", "schema_version": "phase2-v1"}


async def test_phase3_candidate_and_image_limits(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select selection_session_id from candidates where id = %s", (candidate_id,))
        session_id = (await cursor.fetchone())["selection_session_id"]
    for label, marker in (("B", "c"), ("C", "d"), ("D", "e"), ("E", "f")):
        await repository.create_candidate(candidate_id=uuid4(), session_id=session_id, client_id=client_id, label=label, display_name=None, idempotency_key=uuid4(), request_hash=marker * 64)
    with pytest.raises(CandidateLimitExceeded):
        await repository.create_candidate(candidate_id=uuid4(), session_id=session_id, client_id=client_id, label="F", display_name=None, idempotency_key=uuid4(), request_hash="0" * 64)

    await repository.create_image_and_initial_job(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/jpeg", size_bytes=32, sha256="f" * 64, request_hash="f" * 64,
        width=8, height=4, processing_mode=ProcessingMode.TEST_FIXTURE,
    )
    await repository.create_image_and_initial_job(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/jpeg", size_bytes=32, sha256="0" * 64, request_hash="0" * 64,
        width=8, height=4, processing_mode=ProcessingMode.TEST_FIXTURE,
    )
    with pytest.raises(CandidateImageLimitExceeded):
        await repository.create_image_and_initial_job(
            image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
            idempotency_key=uuid4(), content_type="image/jpeg", size_bytes=32, sha256="9" * 64, request_hash="9" * 64,
            width=8, height=4, processing_mode=ProcessingMode.TEST_FIXTURE,
        )


async def test_evidence_and_ai_log_are_append_only_owned_records(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    result = await repository.create_image_and_initial_job(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/png", size_bytes=32, sha256="1" * 64, request_hash="1" * 64,
        width=8, height=4, processing_mode=ProcessingMode.FAKE_PROVIDER,
    )
    version_id = uuid4()
    await repository.create_extraction_version(
        version_id=version_id, candidate_id=candidate_id, image_id=result.image.id,
        client_id=client_id, schema_version="phase2-v1"
    )
    evidence = EvidenceItem(
        id=uuid4(), extraction_version_id=version_id, field_name="origin", raw_text="安溪", normalized_value="安溪", model_confidence=0.9,
        information_status=InformationStatus.EXPLICIT, source_type=EvidenceSourceType.PRODUCT_CLAIM,
        verification_status=VerificationStatus.UNVERIFIED, source_image_id=result.image.id,
        source_location="label", evidence_strength=EvidenceStrength.HIGH,
        created_at=result.image.created_at,
    )
    await repository.append_evidence_items(client_id=client_id, extraction_version_id=version_id, items=(evidence,))
    await repository.append_ai_call_log(
        client_id=client_id,
        log=AiCallLog(
            id=uuid4(), analysis_job_id=result.job.id, provider="fake", model_identifier="fixture-v1",
            processing_mode=ProcessingMode.FAKE_PROVIDER, latency_ms=1,
            error_code=ErrorCode.AI_SCHEMA_INVALID,
        ),
    )
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select source_type, evidence_strength from evidence_items where id = %s", (evidence.id,))
        assert await cursor.fetchone() == {"source_type": "product-claim", "evidence_strength": "high"}
        await cursor.execute("select error_code from ai_call_logs where analysis_job_id = %s", (result.job.id,))
        assert await cursor.fetchone() == {"error_code": "ai_schema_invalid"}


async def test_complete_extraction_job_commits_version_evidence_log_and_job_together(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    result = await repository.create_image_and_initial_job(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/png", size_bytes=32, sha256="2" * 64, request_hash="2" * 64,
        width=8, height=4, processing_mode=ProcessingMode.FAKE_PROVIDER,
    )
    assert await repository.claim_job(job_id=result.job.id)
    version_id = uuid4()
    evidence = EvidenceItem(
        id=uuid4(), extraction_version_id=version_id, field_name="origin", raw_text="安溪", normalized_value="安溪",
        model_confidence=0.9, information_status=InformationStatus.EXPLICIT,
        source_type=EvidenceSourceType.PRODUCT_CLAIM, verification_status=VerificationStatus.UNVERIFIED,
        source_image_id=result.image.id, source_location="label.origin", evidence_strength=EvidenceStrength.HIGH,
        created_at=result.image.created_at,
    )
    await repository.complete_extraction_job(
        job_id=result.job.id, version_id=version_id, schema_version="phase2-fake-v1", evidence_items=(evidence,),
        ai_log=AiCallLog(id=uuid4(), analysis_job_id=result.job.id, provider="fake", model_identifier="fixture-v1", processing_mode=ProcessingMode.FAKE_PROVIDER),
        temporary_image_deleted=True,
    )
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select status, extraction_version_id from analysis_jobs where id=%s", (result.job.id,))
        assert await cursor.fetchone() == {"status": "completed", "extraction_version_id": version_id}
        await cursor.execute("select count(*) as count from extraction_versions where id=%s", (version_id,))
        assert (await cursor.fetchone())["count"] == 1
        await cursor.execute("select count(*) as count from evidence_items where extraction_version_id=%s", (version_id,))
        assert (await cursor.fetchone())["count"] == 1
        await cursor.execute("select count(*) as count from ai_call_logs where analysis_job_id=%s", (result.job.id,))
        assert (await cursor.fetchone())["count"] == 1
    assert not await repository.claim_job(job_id=result.job.id)


async def test_complete_extraction_job_rolls_back_everything_on_evidence_failure(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    result = await repository.create_image_and_initial_job(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/png", size_bytes=32, sha256="3" * 64, request_hash="3" * 64,
        width=8, height=4, processing_mode=ProcessingMode.FAKE_PROVIDER,
    )
    assert await repository.claim_job(job_id=result.job.id)
    version_id = uuid4()
    wrong_version_evidence = EvidenceItem(
        id=uuid4(), extraction_version_id=uuid4(), field_name="origin", raw_text="安溪", normalized_value="安溪",
        model_confidence=0.9, information_status=InformationStatus.EXPLICIT,
        source_type=EvidenceSourceType.PRODUCT_CLAIM, verification_status=VerificationStatus.UNVERIFIED,
        source_image_id=result.image.id, source_location="label.origin", evidence_strength=EvidenceStrength.HIGH,
        created_at=result.image.created_at,
    )
    with pytest.raises(ValueError, match="another extraction version"):
        await repository.complete_extraction_job(
            job_id=result.job.id, version_id=version_id, schema_version="phase2-fake-v1", evidence_items=(wrong_version_evidence,),
            ai_log=AiCallLog(id=uuid4(), analysis_job_id=result.job.id, provider="fake", model_identifier="fixture-v1", processing_mode=ProcessingMode.FAKE_PROVIDER),
            temporary_image_deleted=True,
        )
    await repository.fail_extraction_job(job_id=result.job.id, error_code=ErrorCode.WORKER_INTERRUPTED)
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select status from analysis_jobs where id=%s", (result.job.id,))
        assert await cursor.fetchone() == {"status": "failed"}
        await cursor.execute("select count(*) as count from extraction_versions where id=%s", (version_id,))
        assert (await cursor.fetchone())["count"] == 0
        await cursor.execute("select count(*) as count from evidence_items where extraction_version_id=%s", (version_id,))
        assert (await cursor.fetchone())["count"] == 0
        await cursor.execute("select count(*) as count from ai_call_logs where analysis_job_id=%s", (result.job.id,))
        assert (await cursor.fetchone())["count"] == 0
    assert not await repository.claim_job(job_id=result.job.id)


async def test_concurrent_connections_claim_a_job_only_once(repository: PostgresPhase2Repository) -> None:
    client_id, candidate_id = await _candidate(repository)
    result = await repository.create_image_and_initial_job(
        image_id=uuid4(), job_id=uuid4(), candidate_id=candidate_id, client_id=client_id,
        idempotency_key=uuid4(), content_type="image/png", size_bytes=32, sha256="4" * 64, request_hash="4" * 64,
        width=8, height=4, processing_mode=ProcessingMode.FAKE_PROVIDER,
    )
    second_connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
    second_repository = PostgresPhase2Repository(second_connection)
    try:
        claims = await asyncio.gather(
            repository.claim_job(job_id=result.job.id),
            second_repository.claim_job(job_id=result.job.id),
        )
    finally:
        await second_connection.close()
    assert claims.count(True) == 1
    assert claims.count(False) == 1
