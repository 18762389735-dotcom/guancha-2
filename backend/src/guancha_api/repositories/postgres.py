"""PostgreSQL persistence boundary for the Phase 2 single-image slice.

This module deliberately owns SQL and ownership checks, but not HTTP, provider, or
job orchestration.  It is safe to exercise against a disposable PostgreSQL
database in CI; production Supabase wiring remains a separate, manual smoke test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from guancha_api.auth.models import AppUser, OwnerContext
from guancha_api.schemas.contracts import (
    CandidateImageStatus,
    EvidenceItem,
    ErrorCode,
    JobStage,
    JobState,
    ProcessingMode,
)


class RepositoryError(Exception):
    """Base class for persistence errors that application code can translate."""


class ResourceNotFound(RepositoryError):
    """A missing persistence resource, optionally with its public API contract code."""

    def __init__(self, message: str, *, error_code: ErrorCode | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class OwnershipDenied(RepositoryError):
    pass


class IdempotencyConflict(RepositoryError):
    pass


class PreferenceRevisionConflict(RepositoryError):
    """A preference write was based on an out-of-date server revision."""


class ImmutableVersionError(RepositoryError):
    pass


class CandidateLimitExceeded(RepositoryError):
    pass


class CandidateImageLimitExceeded(RepositoryError):
    pass


class CandidateExtractionInProgress(RepositoryError):
    pass


class DecisionInputInvalid(RepositoryError):
    pass


class CurrentDecisionNotAvailable(RepositoryError):
    pass


class DecisionStale(RepositoryError):
    pass


class QuestionsNotAvailable(RepositoryError):
    pass


class QuestionGenerationFailed(RepositoryError):
    pass


class MerchantReplyNotAvailable(RepositoryError):
    pass


OwnerLike = OwnerContext | UUID


def _as_owner(value: OwnerLike) -> OwnerContext:
    """Accept legacy UUID callers while making all SQL checks owner-aware."""

    return value if isinstance(value, OwnerContext) else OwnerContext.anonymous(value)


def _owner_predicate(owner: OwnerLike, alias: str = "s") -> tuple[str, tuple[UUID, ...]]:
    context = _as_owner(owner)
    if context.user_id is not None:
        return f"{alias}.user_id = %s", (context.user_id,)
    return f"{alias}.user_id is null and {alias}.anonymous_client_id = %s", (
        context.anonymous_client_id,
    )


def _owner_matches(owner: OwnerLike, row: dict[str, object]) -> bool:
    context = _as_owner(owner)
    if context.user_id is not None:
        return row.get("user_id") == context.user_id
    return row.get("user_id") is None and row.get("anonymous_client_id") == context.anonymous_client_id


def _explicit_product_conflict(product: dict[str, object] | None, merchant_value: object) -> bool:
    """Only a known, explicit product-page value can oppose a merchant claim."""
    if not product or product.get("source_type") != "product-claim" or product.get("information_status") != "explicit":
        return False
    product_value = str(product.get("normalized_value") or "").strip().casefold()
    if not product_value or product_value == "unknown":
        return False
    return product_value != str(merchant_value or "").strip().casefold()


@dataclass(frozen=True)
class StoredImage:
    id: UUID
    candidate_id: UUID
    content_type: str
    size_bytes: int
    source_sha256: str
    sanitized_sha256: str
    width: int
    height: int
    display_order: int
    status: CandidateImageStatus
    created_at: datetime


@dataclass(frozen=True)
class StoredJob:
    id: UUID
    candidate_id: UUID
    candidate_image_id: UUID
    status: JobState
    attempt: int
    processing_mode: ProcessingMode | None
    created_at: datetime
    updated_at: datetime
    stage: JobStage | None = None
    error_code: ErrorCode | None = None
    extraction_version_id: UUID | None = None
    input_image_ids: tuple[UUID, ...] = ()
    input_set_version: int = 0
    decision_version_id: UUID | None = None
    decision_delta_id: UUID | None = None


@dataclass(frozen=True)
class CreateImageJobResult:
    image: StoredImage
    job: StoredJob
    created: bool


@dataclass(frozen=True)
class AiCallLog:
    id: UUID
    analysis_job_id: UUID
    provider: str
    model_identifier: str
    processing_mode: ProcessingMode
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: ErrorCode | None = None
    provider_version: str | None = None
    request_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class StoredUserPreferences:
    user_id: UUID
    profile: dict[str, object]
    schema_version: int
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StoredPreferenceEvidence:
    id: UUID
    user_id: UUID
    target_type: str
    target_value: str
    polarity: str
    confidence: str
    issue_source: str
    source_brew_session_id: str
    created_at: datetime


@dataclass(frozen=True)
class StoredSelectionSessionSummary:
    id: UUID
    user_id: UUID
    need: dict[str, object]
    created_at: datetime
    updated_at: datetime


class PostgresPhase2Repository:
    """A connection-scoped repository; callers control connection lifetime."""

    def __init__(self, connection: AsyncConnection[dict[str, object]]) -> None:
        self._connection = connection

    @classmethod
    async def connect(cls, dsn: str) -> "PostgresPhase2Repository":
        connection = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row, autocommit=True)
        return cls(connection)

    async def close(self) -> None:
        await self._connection.close()

    async def create_client(self, client_id: UUID) -> None:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "insert into anonymous_clients (id) values (%s) on conflict (id) do nothing",
                (client_id,),
            )
        await self._connection.commit()

    async def resolve_or_create_app_user(self, cloudbase_user_id: str) -> AppUser:
        """Map a verified CloudBase subject to one stable internal user id."""
        normalized_subject = cloudbase_user_id.strip()
        if not normalized_subject:
            raise ValueError("cloudbase_user_id must not be empty")
        app_user_id = uuid4()
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """
                    insert into app_users (id, cloudbase_user_id)
                    values (%s, %s)
                    on conflict (cloudbase_user_id) do nothing
                    returning id, cloudbase_user_id, created_at, updated_at
                    """,
                    (app_user_id, normalized_subject),
                )
                row = await cursor.fetchone()
                if row is None:
                    await cursor.execute(
                        """
                        select id, cloudbase_user_id, created_at, updated_at
                        from app_users
                        where cloudbase_user_id = %s
                        """,
                        (normalized_subject,),
                    )
                    row = await cursor.fetchone()
        if row is None:
            raise RepositoryError("App user conflict was not visible after insert")
        return AppUser(
            id=row["id"],
            cloudbase_user_id=row["cloudbase_user_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_user_preferences(self, *, user_id: UUID) -> StoredUserPreferences | None:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select user_id, profile, schema_version, revision, created_at, updated_at
                   from user_preferences where user_id=%s""",
                (user_id,),
            )
            row = await cursor.fetchone()
        return None if row is None else self._stored_user_preferences(row)

    async def put_user_preferences(
        self, *, user_id: UUID, profile: dict[str, object], expected_revision: int
    ) -> StoredUserPreferences:
        """Create or replace one user's profile with compare-and-swap semantics."""

        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                if expected_revision == 0:
                    await cursor.execute(
                        """insert into user_preferences (user_id, profile, revision)
                           values (%s, %s, 1)
                           on conflict (user_id) do nothing
                           returning user_id, profile, schema_version, revision, created_at, updated_at""",
                        (user_id, psycopg.types.json.Jsonb(profile)),
                    )
                else:
                    await cursor.execute(
                        """update user_preferences
                           set profile=%s, revision=revision+1, updated_at=now()
                           where user_id=%s and revision=%s
                           returning user_id, profile, schema_version, revision, created_at, updated_at""",
                        (psycopg.types.json.Jsonb(profile), user_id, expected_revision),
                    )
                row = await cursor.fetchone()
        if row is None:
            raise PreferenceRevisionConflict("Preference revision does not match")
        return self._stored_user_preferences(row)

    async def list_user_preference_evidence(
        self, *, user_id: UUID, limit: int = 12
    ) -> tuple[StoredPreferenceEvidence, ...]:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id, user_id, target_type, target_value, polarity, confidence,
                          issue_source, source_brew_session_id, created_at
                   from user_preference_evidence
                   where user_id=%s and created_at >= now() - interval '90 days'
                   order by created_at desc, id desc
                   limit %s""",
                (user_id, limit),
            )
            rows = await cursor.fetchall()
        return tuple(self._stored_preference_evidence(row) for row in rows)

    async def put_user_preference_evidence(
        self, *, user_id: UUID, evidence: tuple[dict[str, object], ...]
    ) -> tuple[StoredPreferenceEvidence, ...]:
        """Upsert source-deduplicated evidence, retaining only the current product window."""

        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                for item in evidence:
                    await cursor.execute(
                        """insert into user_preference_evidence
                           (id, user_id, target_type, target_value, polarity, confidence,
                            issue_source, source_brew_session_id, created_at)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           on conflict (user_id, source_brew_session_id) do update set
                             target_type=excluded.target_type,
                             target_value=excluded.target_value,
                             polarity=excluded.polarity,
                             confidence=excluded.confidence,
                             issue_source=excluded.issue_source,
                             created_at=excluded.created_at""",
                        (
                            item["id"], user_id, item["target_type"], item["target_value"],
                            item["polarity"], item["confidence"], item["issue_source"],
                            item["source_brew_session_id"], item["created_at"],
                        ),
                    )
                await cursor.execute(
                    """delete from user_preference_evidence
                       where user_id=%s and created_at < now() - interval '90 days'""",
                    (user_id,),
                )
                await cursor.execute(
                    """delete from user_preference_evidence
                       where user_id=%s and id in (
                         select id from user_preference_evidence
                         where user_id=%s
                         order by created_at desc, id desc
                         offset 12
                       )""",
                    (user_id, user_id),
                )
        return await self.list_user_preference_evidence(user_id=user_id)

    async def list_authenticated_selection_sessions(
        self, *, user_id: UUID, limit: int = 20
    ) -> tuple[StoredSelectionSessionSummary, ...]:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id, user_id, need, created_at, updated_at
                   from selection_sessions
                   where user_id=%s
                   order by created_at desc, id desc
                   limit %s""",
                (user_id, limit),
            )
            rows = await cursor.fetchall()
        return tuple(
            StoredSelectionSessionSummary(
                id=row["id"],
                user_id=row["user_id"],
                need=dict(row["need"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    async def get_brew_feedback_replay(self, *, client_id: UUID, client_feedback_id: UUID, idempotency_key: UUID) -> dict[str, object] | None:
        async with self._connection.cursor() as cursor:
            await cursor.execute("select client_feedback_id, idempotency_key, request_hash, response from brew_feedback_replays where anonymous_client_id=%s and (client_feedback_id=%s or idempotency_key=%s)", (client_id, client_feedback_id, idempotency_key))
            return await cursor.fetchone()

    async def save_brew_feedback_replay(self, *, client_id: UUID, client_feedback_id: UUID, idempotency_key: UUID, request_hash: str, response: dict[str, object]) -> dict[str, object]:
        await self.create_client(client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("insert into brew_feedback_replays (anonymous_client_id,client_feedback_id,idempotency_key,request_hash,response) values (%s,%s,%s,%s,%s) on conflict do nothing returning client_feedback_id,idempotency_key,request_hash,response", (client_id,client_feedback_id,idempotency_key,request_hash,psycopg.types.json.Jsonb(response)))
                row = await cursor.fetchone()
                if row:
                    return row
                await cursor.execute("select client_feedback_id,idempotency_key,request_hash,response from brew_feedback_replays where anonymous_client_id=%s and (client_feedback_id=%s or idempotency_key=%s)", (client_id,client_feedback_id,idempotency_key))
                return await cursor.fetchone()

    async def create_selection_session(self, *, session_id: UUID, client_id: OwnerLike, idempotency_key: UUID, request_hash: str, need: dict[str, object], recent_preference_evidence: tuple[dict[str, object], ...] = (), expires_at: datetime) -> tuple[dict[str, object], bool]:
        owner = _as_owner(client_id)
        if owner.anonymous_client_id is not None:
            await self.create_client(owner.anonymous_client_id)
        owner_value = owner.user_id or owner.anonymous_client_id
        owner_column = "user_id" if owner.user_id is not None else "anonymous_client_id"
        owner_predicate, owner_params = _owner_predicate(owner)
        try:
            async with self._connection.transaction():
                async with self._connection.cursor() as cursor:
                    await cursor.execute(f"select id, anonymous_client_id, need, expires_at, created_at, updated_at, request_hash from selection_sessions s where {owner_predicate} and s.idempotency_key=%s", (*owner_params, idempotency_key))
                    row=await cursor.fetchone()
                    if row:
                        if row['request_hash'] != request_hash: raise IdempotencyConflict('Session key belongs to a different request')
                        return row, False
                    await cursor.execute(f"insert into selection_sessions (id, {owner_column}, need, recent_preference_evidence, idempotency_key, request_hash, expires_at) values (%s,%s,%s,%s,%s,%s,%s) returning id, anonymous_client_id, need, expires_at, created_at, updated_at", (session_id,owner_value,psycopg.types.json.Jsonb(need),psycopg.types.json.Jsonb(list(recent_preference_evidence)),idempotency_key,request_hash,expires_at))
                    return await cursor.fetchone(), True
        except psycopg.errors.UniqueViolation:
            async with self._connection.cursor() as cursor:
                await cursor.execute(f"select id, anonymous_client_id, need, expires_at, created_at, updated_at, request_hash from selection_sessions s where {owner_predicate} and s.idempotency_key=%s", (*owner_params, idempotency_key))
                row=await cursor.fetchone()
            if row and row['request_hash'] == request_hash: return row, False
            raise IdempotencyConflict('Session key belongs to a different request')

    async def create_candidate(self, *, candidate_id: UUID, session_id: UUID, client_id: UUID, label: str, display_name: str | None, idempotency_key: UUID, request_hash: str) -> tuple[dict[str, object], bool]:
        await self._require_owned_session(session_id, client_id)
        try:
            async with self._connection.transaction():
              async with self._connection.cursor() as cursor:
                # Locking the parent session serialises only its short order allocation.
                await cursor.execute("select id from selection_sessions where id=%s for update", (session_id,))
                await cursor.execute("select id, selection_session_id, display_label, display_name, display_order, created_at, request_hash from candidates where selection_session_id=%s and idempotency_key=%s", (session_id,idempotency_key))
                row=await cursor.fetchone()
                if row:
                    if row['request_hash'] != request_hash: raise IdempotencyConflict('Candidate key belongs to a different request')
                    return row, False
                await cursor.execute("select display_order from candidates where selection_session_id=%s and status='active' order by display_order", (session_id,))
                occupied_orders = {row["display_order"] for row in await cursor.fetchall()}
                if len(occupied_orders) >= 5:
                    raise CandidateLimitExceeded("A selection session permits at most five candidates")
                display_order = next(order for order in range(1, 6) if order not in occupied_orders)
                await cursor.execute(
                    "insert into candidates (id, selection_session_id, display_label, display_name, display_order, idempotency_key, request_hash) values (%s,%s,%s,%s,%s,%s,%s) returning id, selection_session_id, display_label, display_name, display_order, created_at",
                    (candidate_id, session_id, label, display_name, display_order, idempotency_key, request_hash),
                )
                row=await cursor.fetchone()
            return row, True
        except psycopg.errors.UniqueViolation:
            await self._connection.rollback()
            async with self._connection.cursor() as cursor:
                await cursor.execute("select id, selection_session_id, display_label, display_name, display_order, created_at, request_hash from candidates where selection_session_id=%s and idempotency_key=%s", (session_id,idempotency_key))
                row=await cursor.fetchone()
            if row:
                if row['request_hash'] == request_hash: return row, False
                raise IdempotencyConflict('Candidate key belongs to a different request')
            raise CandidateLimitExceeded("A selection session permits at most five candidates")

    async def create_image_and_initial_job(
        self,
        *,
        image_id: UUID,
        job_id: UUID,
        candidate_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        content_type: str,
        size_bytes: int,
        request_hash: str,
        width: int,
        height: int,
        processing_mode: ProcessingMode,
        stage_until_selection_start: bool = False,
        source_sha256: str | None = None,
        sanitized_sha256: str | None = None,
        sha256: str | None = None,
    ) -> CreateImageJobResult:
        """Atomically create the only Phase-2 image and its first queued Job.

        Replaying precisely the same idempotency key returns the original pair.
        Reusing it for another candidate or image fingerprint is an explicit
        conflict, rather than silently binding a request to the wrong resource.
        """
        await self._require_owned_candidate(candidate_id, client_id)
        # Application connections use autocommit and this becomes one short
        # transaction below.  A caller that deliberately supplied a surrounding
        # transaction retains ownership of it: repository code must not commit
        # that caller's prior work merely to prepare an idempotent insert.
        source_sha256 = source_sha256 or sha256
        sanitized_sha256 = sanitized_sha256 or sha256
        if source_sha256 is None or sanitized_sha256 is None:
            raise ValueError("Both image hashes are required")
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """select j.id, j.candidate_id, j.candidate_image_id, j.status, j.stage, j.attempt,
                              j.error_code, j.extraction_version_id, j.processing_mode, j.input_image_ids, j.input_set_version, j.request_hash,
                              j.created_at, j.updated_at, i.id as image_id, i.content_type, i.size_bytes,
                              i.source_sha256, i.sanitized_sha256, i.width, i.height, i.display_order as image_display_order,
                              i.status as image_status, i.created_at as image_created_at
                       from analysis_jobs j join candidate_images i on i.id = j.candidate_image_id
                       where j.candidate_id = %s and j.idempotency_key = %s""",
                    (candidate_id, idempotency_key),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflict("Idempotency key belongs to a different image request")
                    return CreateImageJobResult(self._to_image(existing), self._to_job(existing), False)

                # Candidate-local serialisation makes display-order allocation and
                # image-set versioning deterministic without a process-global lock.
                await cursor.execute(
                    "select image_set_version from candidates where id=%s and status='active' for update",
                    (candidate_id,),
                )
                candidate = await cursor.fetchone()
                if candidate is None:
                    raise ResourceNotFound("Candidate not found")
                await cursor.execute(
                    "select display_order from candidate_images where candidate_id=%s and status <> 'deleted' order by display_order",
                    (candidate_id,),
                )
                occupied_image_orders = {row["display_order"] for row in await cursor.fetchall()}
                if len(occupied_image_orders) >= 2:
                    raise CandidateImageLimitExceeded("A candidate permits at most two images")
                display_order = next(order for order in range(1, 3) if order not in occupied_image_orders)
                next_set_version = int(candidate["image_set_version"]) + 1

                # PostgreSQL, not a Python process lock, arbitrates the one
                # image per candidate. DO NOTHING leaves the connection usable
                # and lets the loser read the winner after its short commit.
                await cursor.execute(
                    """insert into candidate_images
                       (id, candidate_id, content_type, size_bytes, source_sha256, sanitized_sha256,
                        width, height, display_order, status)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'received')
                       on conflict do nothing
                       returning id""",
                    (image_id, candidate_id, content_type, size_bytes, source_sha256,
                     sanitized_sha256, width, height, display_order),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    await cursor.execute(
                        """select j.id, j.candidate_id, j.candidate_image_id, j.status, j.stage, j.attempt,
                                  j.error_code, j.extraction_version_id, j.processing_mode, j.input_image_ids, j.input_set_version, j.request_hash,
                                  j.created_at, j.updated_at, i.id as image_id, i.content_type, i.size_bytes,
                                  i.source_sha256, i.sanitized_sha256, i.width, i.height, i.display_order as image_display_order,
                                  i.status as image_status, i.created_at as image_created_at
                           from analysis_jobs j join candidate_images i on i.id = j.candidate_image_id
                           where j.candidate_id = %s and j.idempotency_key = %s""",
                        (candidate_id, idempotency_key),
                    )
                    existing = await cursor.fetchone()
                    if existing is not None:
                        if existing["request_hash"] != request_hash:
                            raise IdempotencyConflict("Idempotency key belongs to a different image request")
                        return CreateImageJobResult(self._to_image(existing), self._to_job(existing), False)
                    raise CandidateImageLimitExceeded("A candidate permits at most two images")

                await cursor.execute(
                    "update candidates set image_set_version=%s where id=%s",
                    (next_set_version, candidate_id),
                )
                # Images are staged until the selection is explicitly started.
                # When the candidate gains another image, its earlier queued
                # extraction must never be dispatched with an obsolete input
                # set.  It is intentionally stale rather than failed: no
                # provider call was made and the replacement job below owns
                # the complete, newest input set.
                if stage_until_selection_start:
                    await cursor.execute(
                        """update analysis_jobs set status='stale', stage='failed',
                           finished_at=now(), updated_at=now()
                           where candidate_id=%s and job_kind='extraction'
                           and status='queued'""",
                        (candidate_id,),
                    )
                await cursor.execute(
                    "select id from candidate_images where candidate_id=%s and status <> 'deleted' order by display_order",
                    (candidate_id,),
                )
                input_image_ids = [row["id"] for row in await cursor.fetchall()]

                # The visible input set changes as soon as this image is
                # committed.  An Extraction based on the previous set must
                # never remain current while the joint replacement Job is
                # queued or processing.
                await cursor.execute(
                    "update extraction_versions set is_current=false,status='stale' where candidate_id=%s and is_current",
                    (candidate_id,),
                )
                await cursor.execute(
                    """update decision_versions set is_current=false,status='stale'
                       where selection_session_id=(select selection_session_id from candidates where id=%s) and is_current""",
                    (candidate_id,),
                )

                await cursor.execute(
                    """insert into analysis_jobs
                       (id, candidate_id, candidate_image_id, idempotency_key, request_hash, status,
                        attempt, processing_mode, input_image_ids, input_set_version)
                       values (%s, %s, %s, %s, %s, 'queued', 1, %s, %s, %s)""",
                    (job_id, candidate_id, image_id, idempotency_key, request_hash,
                     processing_mode.value, input_image_ids, next_set_version),
                )
                await cursor.execute(
                    """select j.id, j.candidate_id, j.candidate_image_id, j.status, j.stage, j.attempt,
                              j.error_code, j.extraction_version_id, j.processing_mode, j.input_image_ids, j.input_set_version, j.created_at,
                              j.updated_at, i.id as image_id, i.content_type, i.size_bytes,
                              i.source_sha256, i.sanitized_sha256, i.width, i.height, i.display_order as image_display_order,
                              i.status as image_status, i.created_at as image_created_at
                       from analysis_jobs j join candidate_images i on i.id = j.candidate_image_id
                       where j.id = %s""",
                    (job_id,),
                )
                row = await cursor.fetchone()
        return CreateImageJobResult(self._to_image(row), self._to_job(row), True)

    async def find_image_job_replay(
        self, *, candidate_id: UUID, client_id: UUID, idempotency_key: UUID, request_hash: str
    ) -> CreateImageJobResult | None:
        """Read-only preflight prevents a normal idempotent replay from writing storage."""
        await self._require_owned_candidate(candidate_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select j.id, j.candidate_id, j.candidate_image_id, j.status, j.stage, j.attempt,
                          j.error_code, j.extraction_version_id, j.processing_mode, j.input_image_ids, j.input_set_version, j.request_hash, j.created_at, j.updated_at,
                          i.id as image_id, i.content_type, i.size_bytes, i.source_sha256, i.sanitized_sha256,
                          i.width, i.height, i.display_order as image_display_order, i.status as image_status, i.created_at as image_created_at
                   from analysis_jobs j join candidate_images i on i.id=j.candidate_image_id
                   where j.candidate_id=%s and j.idempotency_key=%s""",
                (candidate_id, idempotency_key),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise IdempotencyConflict("Image key belongs to a different image request")
        return CreateImageJobResult(self._to_image(row), self._to_job(row), False)

    async def require_candidate_for_client(self, *, candidate_id: UUID, client_id: UUID) -> None:
        await self._require_owned_candidate(candidate_id, client_id)

    async def get_job_for_client(self, *, job_id: UUID, client_id: UUID) -> StoredJob:
        await self._require_owned_resource("analysis_jobs", job_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id, candidate_id, candidate_image_id, status, stage, attempt,
                          error_code, extraction_version_id, decision_version_id, decision_delta_id, processing_mode,
                          input_image_ids, input_set_version, created_at, updated_at
                   from analysis_jobs where id = %s""",
                (job_id,),
            )
            row = await cursor.fetchone()
        return self._to_job(row)

    async def get_latest_job_for_candidate(
        self, *, candidate_id: UUID, client_id: UUID
    ) -> StoredJob | None:
        await self._require_owned_candidate(candidate_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id, candidate_id, candidate_image_id, status, stage, attempt,
                          error_code, extraction_version_id, processing_mode, input_image_ids, input_set_version, created_at, updated_at
                   from analysis_jobs where candidate_id=%s order by created_at desc limit 1""",
                (candidate_id,),
            )
            row = await cursor.fetchone()
        return self._to_job(row) if row is not None else None

    async def list_queued_extraction_jobs_for_session(
        self, *, session_id: UUID, client_id: UUID
    ) -> tuple[StoredJob, ...]:
        """Return the one newest queued input set for every owned candidate."""
        await self._require_owned_session(session_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select distinct on (j.candidate_id)
                          j.id, j.candidate_id, j.candidate_image_id, j.status,
                          j.stage, j.attempt, j.error_code,
                          j.extraction_version_id, j.decision_version_id,
                          j.decision_delta_id, j.processing_mode,
                          j.input_image_ids, j.input_set_version,
                          j.created_at, j.updated_at
                   from analysis_jobs j
                   join candidates c on c.id=j.candidate_id
                   join selection_sessions s on s.id=c.selection_session_id
                   where s.id=%s
                     and j.job_kind='extraction' and j.status='queued'
                   order by j.candidate_id, j.created_at desc""",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return tuple(self._to_job(row) for row in rows)

    async def requeue_interrupted_job(
        self, *, failed_job_id: UUID, new_job_id: UUID, idempotency_key: UUID, request_hash: str
    ) -> tuple[StoredJob, bool] | None:
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """select id, candidate_id, candidate_image_id, processing_mode, status, stage,
                              attempt, error_code, extraction_version_id, input_image_ids, input_set_version, idempotency_key, request_hash,
                              created_at, updated_at
                       from analysis_jobs where id=%s for update""",
                    (failed_job_id,),
                )
                failed = await cursor.fetchone()
                if failed is None:
                    return None
                if failed["status"] == "queued":
                    if failed["idempotency_key"] != idempotency_key:
                        return None
                    if failed["request_hash"] != request_hash:
                        raise IdempotencyConflict("Retry key belongs to a different request")
                    return self._to_job(failed), False
                if failed["status"] != "failed" or failed["error_code"] != "worker_interrupted":
                    return None
                await cursor.execute(
                    """select id, candidate_id, candidate_image_id, status, stage, attempt,
                              error_code, extraction_version_id, processing_mode, input_image_ids, input_set_version, request_hash,
                              created_at, updated_at
                       from analysis_jobs where candidate_id=%s and idempotency_key=%s
                       for update""",
                    (failed["candidate_id"], idempotency_key),
                )
                row = await cursor.fetchone()
                if row is not None:
                    if row["request_hash"] != request_hash:
                        raise IdempotencyConflict("Retry key belongs to a different request")
                    if row["status"] == "queued":
                        return self._to_job(row), False
                    if row["status"] == "failed" and row["error_code"] == "worker_interrupted":
                        # A retry enqueue can itself fail while the object is
                        # still private and recoverable. Reuse this same Job
                        # for the same idempotency key instead of attempting a
                        # second insert against its unique constraint.
                        await cursor.execute(
                            """update analysis_jobs set status='queued', stage='queued', error_code=null,
                               finished_at=null, updated_at=now() where id=%s
                               returning id, candidate_id, candidate_image_id, status, stage, attempt,
                               error_code, extraction_version_id, processing_mode, input_image_ids, input_set_version, created_at, updated_at""",
                            (row["id"],),
                        )
                        requeued = await cursor.fetchone()
                        await cursor.execute(
                            "update candidate_images set status='received', error_code=null where id=%s and status != 'deleted'",
                            (row["candidate_image_id"],),
                        )
                        return self._to_job(requeued), True
                    return self._to_job(row), False
                await cursor.execute(
                    """select id from analysis_jobs where candidate_id=%s
                       and status in ('queued', 'processing') limit 1""",
                    (failed["candidate_id"],),
                )
                if await cursor.fetchone() is not None:
                    raise CandidateExtractionInProgress(
                        "Candidate already has an active extraction job"
                    )
                await cursor.execute(
                    """insert into analysis_jobs
                       (id, candidate_id, candidate_image_id, idempotency_key, request_hash, status, attempt, processing_mode,
                        input_image_ids, input_set_version)
                       values (%s,%s,%s,%s,%s,'queued',2,%s,%s,%s)
                       returning id, candidate_id, candidate_image_id, status, stage, attempt,
                       error_code, extraction_version_id, processing_mode, input_image_ids, input_set_version, created_at, updated_at""",
                    (new_job_id, failed["candidate_id"], failed["candidate_image_id"], idempotency_key,
                     request_hash, failed["processing_mode"], failed["input_image_ids"], failed["input_set_version"]),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise IdempotencyConflict("Retry key belongs to a different request")
                await cursor.execute(
                    "update candidate_images set status='received', error_code=null where id=%s and status != 'deleted'",
                    (failed["candidate_image_id"],),
                )
                return self._to_job(row), True

    async def claim_job(self, *, job_id: UUID) -> bool:
        """Atomically claim only a queued Job; competing workers observe False."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """update analysis_jobs set status='processing', stage='claimed', claimed_at=now(),
                   started_at=now(), updated_at=now() where id=%s and status='queued' returning id""",
                (job_id,),
            )
            claimed = await cursor.fetchone() is not None
        await self._connection.commit()
        return claimed

    async def get_selection_session_for_client(self, *, session_id: UUID, client_id: UUID) -> dict[str, object]:
        await self._require_owned_session(session_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id, anonymous_client_id, need, expires_at, created_at, updated_at
                   from selection_sessions where id=%s""",
                (session_id,),
            )
            row = await cursor.fetchone()
        return row

    async def update_selection_need_for_client(
        self, *, session_id: UUID, client_id: UUID, need: dict[str, object], recent_preference_evidence: tuple[dict[str, object], ...] = ()
    ) -> dict[str, object]:
        """Replace a session's raw need and invalidate its current decision atomically."""
        await self._require_owned_session(session_id, client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    "select id from selection_sessions where id=%s for update",
                    (session_id,),
                )
                if await cursor.fetchone() is None:
                    await self._raise_ownership_or_not_found("selection_sessions", session_id, client_id)
                await cursor.execute(
                    """update selection_sessions set need=%s, recent_preference_evidence=%s, updated_at=now() where id=%s
                       returning id, anonymous_client_id, need, expires_at, created_at, updated_at""",
                    (psycopg.types.json.Jsonb(need), psycopg.types.json.Jsonb(list(recent_preference_evidence)), session_id),
                )
                row = await cursor.fetchone()
                await cursor.execute(
                    """update decision_versions set is_current=false, status='stale'
                       where selection_session_id=%s and is_current""",
                    (session_id,),
                )
        return row

    async def list_candidates_for_session(self, *, session_id: UUID, client_id: UUID) -> list[dict[str, object]]:
        await self._require_owned_session(session_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute("select id, selection_session_id, display_label, display_name, display_order, created_at from candidates where selection_session_id=%s and status='active' order by display_order", (session_id,))
            return list(await cursor.fetchall())

    async def get_image_for_client(self, *, image_id: UUID, client_id: UUID) -> dict[str, object]:
        await self._require_owned_resource("candidate_images", image_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute("""select i.id, i.candidate_id, i.content_type, i.size_bytes, i.sanitized_sha256, i.width, i.height, i.display_order, i.status, i.error_code, i.created_at,
              (select id from analysis_jobs where candidate_image_id=i.id order by created_at desc limit 1) current_job_id
              from candidate_images i where i.id=%s""", (image_id,))
            row = await cursor.fetchone()
        return row

    async def get_extraction_version_for_client(self, *, version_id: UUID, client_id: UUID) -> tuple[dict[str, object], list[dict[str, object]]]:
        await self._require_owned_extraction_version(version_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute("select id, candidate_id, source_image_id, source_image_ids, status, schema_version, created_at from extraction_versions where id=%s", (version_id,))
            version = await cursor.fetchone()
            await cursor.execute("select * from evidence_items where extraction_version_id=%s order by created_at", (version_id,))
            evidence = list(await cursor.fetchall())
        return version, evidence

    async def get_current_extraction_for_candidate(self, *, candidate_id: UUID, client_id: UUID) -> tuple[dict[str, object], list[dict[str, object]]] | None:
        await self._require_owned_candidate(candidate_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute("select id from extraction_versions where candidate_id=%s and is_current and status='completed' order by created_at desc limit 1", (candidate_id,))
            row = await cursor.fetchone()
        return None if row is None else await self.get_extraction_version_for_client(version_id=row["id"], client_id=client_id)

    async def decision_inputs_for_session(self, *, session_id: UUID, client_id: UUID) -> tuple[dict[str, object], list[dict[str, object]]]:
        await self._require_owned_session(session_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute("select need, recent_preference_evidence from selection_sessions where id=%s", (session_id,))
            session = await cursor.fetchone()
            await cursor.execute("""select c.id as candidate_id, v.id as extraction_version_id, v.source_image_id
                from candidates c left join extraction_versions v on v.candidate_id=c.id and v.is_current and v.status='completed'
                where c.selection_session_id=%s and c.status='active' order by c.display_order""", (session_id,))
            inputs = list(await cursor.fetchall())
            if not inputs or any(item["extraction_version_id"] is None for item in inputs):
                raise DecisionInputInvalid("Every active candidate needs a current extraction")
            for item in inputs:
                await cursor.execute("select count(*) as count from analysis_jobs where candidate_id=%s and job_kind='extraction' and status in ('queued','processing')", (item["candidate_id"],))
                if (await cursor.fetchone())["count"]:
                    raise DecisionInputInvalid("Candidate extraction is still processing")
                await cursor.execute("select count(*) as count from candidate_images where candidate_id=%s and status='failed'", (item["candidate_id"],))
                if (await cursor.fetchone())["count"]:
                    raise DecisionInputInvalid("Candidate has an unresolved failed extraction")
                await cursor.execute("select field_name, normalized_value, information_status, source_type, verification_status, evidence_strength from evidence_items where extraction_version_id=%s", (item["extraction_version_id"],))
                item["evidence"] = list(await cursor.fetchall())
        return session, inputs

    async def create_session_decision_job(self, *, job_id: UUID, session_id: UUID, client_id: UUID, idempotency_key: UUID, request_hash: str, need_snapshot: dict[str, object], expected_extraction_version_ids: tuple[UUID, ...]) -> tuple[StoredJob, bool]:
        session, inputs = await self.decision_inputs_for_session(session_id=session_id, client_id=client_id)
        del session
        actual = tuple(item["extraction_version_id"] for item in inputs)
        if actual != expected_extraction_version_ids:
            raise DecisionInputInvalid("Decision inputs changed; refresh before analysis")
        anchor = inputs[0]
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("select id from selection_sessions where id=%s for update", (session_id,))
                await cursor.execute("""select id,candidate_id,candidate_image_id,status,stage,attempt,error_code,extraction_version_id,
                    processing_mode,input_image_ids,input_set_version,created_at,updated_at,request_hash
                    from analysis_jobs where candidate_id=%s and idempotency_key=%s""", (anchor["candidate_id"], idempotency_key))
                existing = await cursor.fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash: raise IdempotencyConflict("Idempotency key belongs to a different decision request")
                    return self._to_job(existing), False
                await cursor.execute("""insert into analysis_jobs
                    (id,candidate_id,candidate_image_id,idempotency_key,request_hash,status,attempt,processing_mode,input_image_ids,input_set_version,job_kind,decision_need_snapshot,expected_extraction_version_ids)
                    values (%s,%s,%s,%s,%s,'queued',1,'test-fixture',%s,0,'session_decision',%s::jsonb,%s)""",
                    (job_id,anchor["candidate_id"],anchor["source_image_id"],idempotency_key,request_hash,[anchor["source_image_id"],],psycopg.types.json.Jsonb(need_snapshot),list(actual)))
                await cursor.execute("""select id,candidate_id,candidate_image_id,status,stage,attempt,error_code,extraction_version_id,
                    processing_mode,input_image_ids,input_set_version,created_at,updated_at from analysis_jobs where id=%s""", (job_id,))
                return self._to_job(await cursor.fetchone()), True

    async def complete_session_decision_job(self, *, job_id: UUID, session_id: UUID, client_id: UUID, version_id: UUID, rule_version: str, input_fingerprint: str, decisions: list[dict[str, object]]) -> None:
        await self._require_owned_session(session_id, client_id)
        owner = _as_owner(client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("select expected_extraction_version_ids, decision_need_snapshot from analysis_jobs where id=%s and job_kind='session_decision' and status='processing' for update", (job_id,))
                job = await cursor.fetchone()
                if job is None: raise RepositoryError("Decision job is not claimable for completion")
                await cursor.execute("select need from selection_sessions where id=%s for update", (session_id,))
                session = await cursor.fetchone()
                if session is None: raise RepositoryError("Decision session changed before completion")
                await cursor.execute("""select v.id from candidates c join extraction_versions v on v.candidate_id=c.id
                    where c.selection_session_id=%s and c.status='active' and v.is_current and v.status='completed' order by c.display_order""", (session_id,))
                is_current = (
                    tuple(row["id"] for row in await cursor.fetchall()) == tuple(job["expected_extraction_version_ids"])
                    and session["need"] == job["decision_need_snapshot"]
                )
                await cursor.execute("select coalesce(max(version),0)+1 as next_version from decision_versions where selection_session_id=%s", (session_id,))
                next_version = (await cursor.fetchone())["next_version"]
                if is_current:
                    await cursor.execute("update decision_versions set is_current=false,status='stale' where selection_session_id=%s and is_current", (session_id,))
                await cursor.execute("""insert into decision_versions
                    (id,selection_session_id,anonymous_client_id,version,status,rule_version,need_snapshot,input_fingerprint,top_candidate_id,is_current)
                    values (%s,%s,%s,%s,'completed',%s,%s::jsonb,%s,%s,%s)""", (version_id,session_id,owner.anonymous_client_id,next_version,rule_version,psycopg.types.json.Jsonb(job["decision_need_snapshot"]),input_fingerprint,decisions[0]["candidate_id"],is_current))
                for decision in decisions:
                    await cursor.execute("""insert into candidate_decisions
                       (id,decision_version_id,candidate_id,extraction_version_id,action_bucket,rank_within_bucket,overall_order,reasons,risk_flags,missing_critical_fields,score_components,internal_score)
                       values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)""", (
                         decision["id"],version_id,decision["candidate_id"],decision["extraction_version_id"],decision["action_bucket"],decision["rank_within_bucket"],decision["overall_order"],
                         psycopg.types.json.Jsonb(decision["reasons"]),psycopg.types.json.Jsonb(decision["risk_flags"]),psycopg.types.json.Jsonb(decision["missing_critical_fields"]),psycopg.types.json.Jsonb(decision["score_components"]),decision["internal_score"],
                    ))
                await cursor.execute("update analysis_jobs set status='completed',stage='completed',decision_version_id=%s,finished_at=now(),updated_at=now() where id=%s", (version_id,job_id))

    async def get_decision_version_for_client(self, *, version_id: UUID, client_id: UUID) -> tuple[dict[str, object], list[dict[str, object]]]:
        await self._require_owned_resource("decision_versions", version_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute("select * from decision_versions where id=%s", (version_id,))
            version = await cursor.fetchone()
        async with self._connection.cursor() as cursor:
            await cursor.execute("select * from candidate_decisions where decision_version_id=%s order by overall_order", (version_id,))
            return version, list(await cursor.fetchall())

    async def get_current_decision_for_session(self, *, session_id: UUID, client_id: UUID) -> tuple[dict[str, object], list[dict[str, object]]] | None:
        await self._require_owned_session(session_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute("select id from decision_versions where selection_session_id=%s and is_current and status='completed' order by created_at desc limit 1", (session_id,))
            row = await cursor.fetchone()
        return None if row is None else await self.get_decision_version_for_client(version_id=row["id"], client_id=client_id)

    async def answer_contract_inputs_for_session(self, *, session_id: UUID, client_id: UUID) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]] | None:
        current = await self.get_current_decision_for_session(session_id=session_id, client_id=client_id)
        if current is None:
            return None
        version, decisions = current
        candidates: list[dict[str, object]] = []
        async with self._connection.cursor() as cursor:
            for decision in decisions:
                await cursor.execute("select id as candidate_id,display_label,display_name from candidates where id=%s", (decision["candidate_id"],))
                candidate = await cursor.fetchone()
                await cursor.execute(
                    """select field_name,normalized_value,information_status,source_type,verification_status,evidence_strength
                       from evidence_items where extraction_version_id=%s order by created_at""",
                    (decision["extraction_version_id"],),
                )
                candidate["evidence"] = list(await cursor.fetchall())
                # A rejudge creates a new immutable DecisionVersion rather than
                # rewriting the screenshot ExtractionVersion.  The answer read
                # model must therefore append only the parsed merchant claims
                # that were the parent version's auditable inputs; otherwise a
                # completed rejudge looks identical to the first judgment.
                parent_version_id = version["parent_decision_version_id"] or version["id"]
                await cursor.execute(
                    """select m.field_key as field_name,m.normalized_value,m.information_status,
                              m.source_type,m.verification_status,m.evidence_strength
                       from merchant_claims m
                       join merchant_replies r on r.id=m.merchant_reply_id
                      where r.decision_version_id=%s and r.candidate_id=%s
                        and r.status='parsed' and r.processing_status='completed'
                        and m.normalized_value is not null
                      order by m.created_at""",
                    (parent_version_id, decision["candidate_id"]),
                )
                candidate["evidence"].extend(await cursor.fetchall())
                candidates.append(candidate)
            await cursor.execute(
                """select id,candidate_id,field_key,question_text from followup_questions
                   where decision_version_id=%s and status='completed' order by priority desc,created_at""",
                (version["id"],),
            )
            questions = list(await cursor.fetchall())
        return version, decisions, candidates, questions

    async def question_context_for_current_decision(self, *, version_id: UUID, client_id: UUID) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
        """Return an immutable decision snapshot only while it is still current."""
        version, decisions = await self.get_decision_version_for_client(version_id=version_id, client_id=client_id)
        if version["status"] == "stale" or not version["is_current"]:
            raise DecisionStale("Decision version is no longer current")
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select count(*) as count from analysis_jobs j join candidates c on c.id=j.candidate_id
                   where c.selection_session_id=%s and j.job_kind='session_decision' and j.status in ('queued','processing')""",
                (version["selection_session_id"],),
            )
            if (await cursor.fetchone())["count"]:
                raise QuestionsNotAvailable("A newer decision is still processing")
            await cursor.execute(
                """select c.id as candidate_id, d.extraction_version_id
                   from candidate_decisions d join candidates c on c.id=d.candidate_id
                   where d.decision_version_id=%s order by d.overall_order""",
                (version_id,),
            )
            inputs = list(await cursor.fetchall())
            if len(inputs) != len(decisions):
                raise QuestionsNotAvailable("Decision candidate snapshot is incomplete")
            for item in inputs:
                await cursor.execute("select is_current, status from extraction_versions where id=%s", (item["extraction_version_id"],))
                extraction = await cursor.fetchone()
                if extraction is None or not extraction["is_current"] or extraction["status"] != "completed":
                    raise DecisionStale("Decision extraction input is no longer current")
                await cursor.execute(
                    """select field_name, normalized_value, information_status, source_type,
                       verification_status, evidence_strength from evidence_items
                       where extraction_version_id=%s order by created_at""",
                    (item["extraction_version_id"],),
                )
                item["evidence"] = list(await cursor.fetchall())
        return version, decisions, inputs

    async def get_followup_questions_for_current_decision(self, *, version_id: UUID, client_id: UUID) -> list[dict[str, object]]:
        await self.question_context_for_current_decision(version_id=version_id, client_id=client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id, decision_version_id, selection_session_id, candidate_id, field_key,
                   question_text, reason, affected_decision, answer_branches, priority, status, created_at
                   from followup_questions where decision_version_id=%s
                   order by priority desc, candidate_id, field_key limit 3""",
                (version_id,),
            )
            return list(await cursor.fetchall())

    async def selection_snapshot_for_client(self, *, session_id: UUID, client_id: UUID) -> dict[str, object]:
        """Server-authoritative recovery read; browser-local files are excluded."""
        session = await self.get_selection_session_for_client(session_id=session_id, client_id=client_id)
        candidates = await self.list_candidates_for_session(session_id=session_id, client_id=client_id)
        async with self._connection.cursor() as cursor:
            for candidate in candidates:
                await cursor.execute(
                    """select i.id,i.content_type,i.size_bytes,i.sanitized_sha256 as sha256,i.width,i.height,i.display_order,i.status,i.created_at,
                              j.id as current_job_id,j.status as current_job_status,j.error_code
                       from candidate_images i left join lateral (
                         select id,status,error_code from analysis_jobs where candidate_image_id=i.id order by created_at desc limit 1
                       ) j on true where i.candidate_id=%s and i.status <> 'deleted' order by i.display_order""",
                    (candidate["id"],),
                )
                candidate["images"] = list(await cursor.fetchall())
                await cursor.execute("select id,status from extraction_versions where candidate_id=%s and is_current order by created_at desc limit 1", (candidate["id"],))
                candidate["current_extraction"] = await cursor.fetchone()
        current = await self.get_current_decision_for_session(session_id=session_id, client_id=client_id)
        current_decision_id = None if current is None else current[0]["id"]
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select j.id,j.status,j.stage,j.error_code,j.decision_version_id,j.created_at,j.updated_at
                   from analysis_jobs j join candidates anchor on anchor.id=j.candidate_id
                   where anchor.selection_session_id=%s and j.job_kind='session_decision' and (
                     (j.status='completed' and j.decision_version_id=%s)
                     or (j.status in ('queued','processing','failed','stale')
                       and j.decision_need_snapshot=%s
                       and j.expected_extraction_version_ids=(
                         select coalesce(array_agg(v.id order by c.display_order),array[]::uuid[])
                         from candidates c join extraction_versions v on v.candidate_id=c.id
                           and v.is_current and v.status='completed'
                         where c.selection_session_id=%s and c.status='active'
                       ))
                   ) order by j.created_at desc limit 1""",
                (session_id, current_decision_id, psycopg.types.json.Jsonb(session["need"]), session_id),
            )
            session_decision_job = await cursor.fetchone()
        if current is None:
            return {
                "session": session,
                "candidates": candidates,
                "current_decision_id": None,
                "question_decision_version_id": None,
                "question_generation_status": None,
                "questions": [],
                "merchant_replies": [],
                "rejudge_job": None,
                "session_decision_job": session_decision_job,
                "decision_delta": None,
            }

        version = current[0]
        # A completed aggregate rejudge creates V2 and makes V1 stale.  The
        # questions and their saved replies still belong to V1, so recovery
        # must follow the immutable parent rather than asking localStorage to
        # remember them.
        question_version_id = version["parent_decision_version_id"] or version["id"]
        question_generation_status = await self.get_question_generation_state(version_id=question_version_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id,decision_version_id,selection_session_id,candidate_id,field_key,
                          question_text,reason,affected_decision,answer_branches,priority,status,created_at
                   from followup_questions where decision_version_id=%s and status='completed'
                   order by priority desc,candidate_id,field_key""",
                (question_version_id,),
            )
            questions = list(await cursor.fetchall())
            await cursor.execute(
                """select id,selection_session_id,decision_version_id,followup_question_id,candidate_id,
                          raw_text,status,processing_status,parse_status,created_at
                   from merchant_replies where decision_version_id=%s
                   order by created_at""",
                (question_version_id,),
            )
            replies = list(await cursor.fetchall())
            await cursor.execute(
                """select id,status,stage,error_code,decision_version_id,decision_delta_id,created_at,updated_at
                   from analysis_jobs where job_kind='merchant_rejudgement'
                     and merchant_reply_id in (select id from merchant_replies where decision_version_id=%s)
                   order by created_at desc limit 1""",
                (question_version_id,),
            )
            rejudge_job = await cursor.fetchone()
            await cursor.execute(
                """select id,selection_session_id,old_decision_version_id,new_decision_version_id,merchant_reply_id,
                          merchant_reply_ids,added_facts,updated_fields,unresolved_fields,resolved_risks,added_risks,
                          ranking_changed,action_tier_changed,old_top_candidate_id,new_top_candidate_id,explanation,created_at
                   from decision_deltas where new_decision_version_id=%s""",
                (version["id"],),
            )
            delta = await cursor.fetchone()
        return {
            "session": session,
            "candidates": candidates,
            "current_decision_id": version["id"],
            "question_decision_version_id": question_version_id,
            "question_generation_status": question_generation_status,
            "questions": questions,
            "merchant_replies": replies,
            "rejudge_job": rejudge_job,
            "session_decision_job": session_decision_job,
            "decision_delta": delta,
        }

    async def get_question_generation_state(self, *, version_id: UUID) -> str | None:
        async with self._connection.cursor() as cursor:
            await cursor.execute("select status from question_generation_runs where decision_version_id=%s", (version_id,))
            row = await cursor.fetchone()
        return None if row is None else str(row["status"])

    async def claim_question_generation(self, *, version_id: UUID, client_id: UUID, idempotency_key: UUID) -> bool:
        """Atomically elect one provider caller; completed runs replay their immutable rows."""
        await self._require_owned_resource("decision_versions", version_id, client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    "select id from decision_versions where id=%s and is_current and status='completed' for update",
                    (version_id,),
                )
                if await cursor.fetchone() is None:
                    raise DecisionStale("Decision version is no longer current")
                await cursor.execute(
                    """insert into question_generation_runs (decision_version_id,idempotency_key,status)
                       values (%s,%s,'processing') on conflict (decision_version_id) do nothing returning decision_version_id""",
                    (version_id, idempotency_key),
                )
                if await cursor.fetchone() is not None:
                    return True
                await cursor.execute("select status from question_generation_runs where decision_version_id=%s for update", (version_id,))
                status = (await cursor.fetchone())["status"]
                if status == "failed":
                    await cursor.execute("update question_generation_runs set status='processing',error_code=null,updated_at=now() where decision_version_id=%s", (version_id,))
                    return True
                if status == "completed":
                    return False
                raise QuestionsNotAvailable("Question generation is already in progress")

    async def persist_followup_questions(
        self, *, version_id: UUID, client_id: UUID, status: str, error_code: str | None,
        questions: list[dict[str, object]],
    ) -> None:
        """Persist the run terminal state and all questions atomically; records are append-only."""
        await self._require_owned_resource("decision_versions", version_id, client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    "select id from decision_versions where id=%s and is_current and status='completed' for update",
                    (version_id,),
                )
                if await cursor.fetchone() is None:
                    raise DecisionStale("Decision version is no longer current")
                await cursor.execute(
                    """update question_generation_runs set status=%s,error_code=%s,updated_at=now()
                       where decision_version_id=%s and status='processing'""",
                    (status, error_code, version_id),
                )
                if status != "completed":
                    return
                for question in questions:
                    await cursor.execute(
                        """insert into followup_questions
                           (id,decision_version_id,selection_session_id,candidate_id,field_key,question_text,reason,
                            affected_decision,answer_branches,priority,value_score,value_components,status)
                           values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,'completed')""",
                        (
                            question["id"], version_id, question["selection_session_id"], question["candidate_id"],
                            question["field_key"], question["question_text"], question["reason"],
                            psycopg.types.json.Jsonb(question["affected_decision"]), psycopg.types.json.Jsonb(question["answer_branches"]),
                            question["priority"], question["value_score"], psycopg.types.json.Jsonb(question["value_components"]),
                        ),
                    )

    async def create_or_replay_merchant_reply(
        self, *, reply_id: UUID, session_id: UUID, client_id: UUID, decision_version_id: UUID,
        followup_question_id: UUID, idempotency_key: UUID, request_hash: str, raw_text: str,
    ) -> tuple[dict[str, object], bool]:
        """Bind reply targets from the current question record, never from client supplied fields."""
        await self.question_context_for_current_decision(version_id=decision_version_id, client_id=client_id)
        owner = _as_owner(client_id)
        idempotency_column = "selection_session_id" if owner.user_id is not None else "anonymous_client_id"
        idempotency_owner = session_id if owner.user_id is not None else owner.anonymous_client_id
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """select id,candidate_id,field_key from followup_questions
                       where id=%s and decision_version_id=%s and selection_session_id=%s for update""",
                    (followup_question_id, decision_version_id, session_id),
                )
                question = await cursor.fetchone()
                if question is None:
                    raise MerchantReplyNotAvailable("Question is not available for this decision")
                await cursor.execute(
                    """select id,selection_session_id,decision_version_id,followup_question_id,candidate_id,
                       raw_text,status,processing_status,parse_status,request_hash,created_at from merchant_replies
                       where """ + idempotency_column + """=%s and followup_question_id=%s and idempotency_key=%s""",
                    (idempotency_owner, followup_question_id, idempotency_key),
                )
                existing = await cursor.fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflict("Merchant reply key belongs to a different request")
                    return existing, False
                await cursor.execute(
                    """insert into merchant_replies
                       (id,selection_session_id,decision_version_id,followup_question_id,candidate_id,anonymous_client_id,
                       idempotency_key,request_hash,raw_text,status,processing_status)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'submitted','queued')
                       on conflict do nothing
                       returning id,selection_session_id,decision_version_id,followup_question_id,candidate_id,raw_text,status,processing_status,parse_status,created_at""",
                    (reply_id, session_id, decision_version_id, followup_question_id, question["candidate_id"], owner.anonymous_client_id, idempotency_key, request_hash, raw_text),
                )
                inserted = await cursor.fetchone()
                if inserted is not None:
                    return inserted, True
                await cursor.execute(
                    """select id,selection_session_id,decision_version_id,followup_question_id,candidate_id,raw_text,status,processing_status,parse_status,request_hash,created_at
                       from merchant_replies where """ + idempotency_column + """=%s and followup_question_id=%s and idempotency_key=%s""",
                    (idempotency_owner, followup_question_id, idempotency_key),
                )
                replay = await cursor.fetchone()
                if replay is None:
                    raise RepositoryError("Merchant reply idempotency replay was not visible")
                if replay["request_hash"] != request_hash:
                    raise IdempotencyConflict("Merchant reply key belongs to a different request")
                return replay, False

    async def get_merchant_reply_for_client(self, *, reply_id: UUID, client_id: UUID) -> dict[str, object]:
        await self._require_owned_resource("merchant_replies", reply_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id,selection_session_id,decision_version_id,followup_question_id,candidate_id,raw_text,status,processing_status,parse_status,created_at
                   from merchant_replies where id=%s""", (reply_id,)
            )
            row = await cursor.fetchone()
        return row

    async def persist_merchant_reply_parse(
        self, *, reply_id: UUID, client_id: UUID, parsed_status: str, claims: tuple[dict[str, str], ...],
    ) -> None:
        """Append merchant claims without mutating an immutable extraction version."""
        await self._require_owned_resource("merchant_replies", reply_id, client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """select r.*,q.field_key,d.extraction_version_id from merchant_replies r
                       join followup_questions q on q.id=r.followup_question_id
                       join candidate_decisions d on d.decision_version_id=r.decision_version_id and d.candidate_id=r.candidate_id
                       where r.id=%s and r.processing_status='processing' for update""",
                    (reply_id,),
                )
                reply = await cursor.fetchone()
                if reply is None:
                    raise MerchantReplyNotAvailable("Merchant reply is not claimable")
                for claim in claims:
                    if claim.get("field_key") != reply["field_key"]:
                        raise ValueError("Reply parser returned a field outside the follow-up question")
                    await cursor.execute(
                        """select id,normalized_value,information_status,source_type from evidence_items
                           where extraction_version_id=%s and field_name=%s and source_type='product-claim'
                           order by (information_status='explicit') desc,created_at limit 1""",
                        (reply["extraction_version_id"], claim["field_key"]),
                    )
                    product = await cursor.fetchone()
                    conflict = _explicit_product_conflict(product, claim["normalized_value"])
                    claim_id = uuid4()
                    await cursor.execute(
                        """insert into merchant_claims (id,merchant_reply_id,candidate_id,field_key,raw_text,normalized_value,
                           information_status,source_type,verification_status,evidence_strength,conflicts_with_evidence_id)
                           values (%s,%s,%s,%s,%s,%s,%s,'merchant-claim','unverified','medium',%s)""",
                        (claim_id, reply_id, reply["candidate_id"], claim["field_key"], claim["raw_text"], claim["normalized_value"], 'conflict' if conflict else 'explicit', product["id"] if conflict else None),
                    )
                await cursor.execute("update merchant_replies set status='parsed',processing_status='completed',parse_status=%s where id=%s", (parsed_status, reply_id))

    async def fail_merchant_reply_parse(self, *, reply_id: UUID, client_id: UUID) -> None:
        """Leave no partial claim rows when the parser cannot produce a result."""
        await self._require_owned_resource("merchant_replies", reply_id, client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """update merchant_replies set status='failed',processing_status='failed'
                       where id=%s and processing_status='processing'""",
                    (reply_id,),
                )

    async def claim_merchant_reply_for_parse(self, *, reply_id: UUID, client_id: UUID) -> tuple[dict[str, object], tuple[dict[str, object], ...]] | None:
        await self._require_owned_resource("merchant_replies", reply_id, client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("""update merchant_replies set processing_status='processing' where id=%s and processing_status='queued'
                    returning id,decision_version_id,followup_question_id,candidate_id,raw_text""", (reply_id,))
                reply = await cursor.fetchone()
                if reply is None: return None
                await cursor.execute("select field_key from followup_questions where id=%s", (reply["followup_question_id"],))
                reply["field_key"] = (await cursor.fetchone())["field_key"]
                await cursor.execute("select * from evidence_items where extraction_version_id in (select extraction_version_id from candidate_decisions where decision_version_id=%s and candidate_id=%s)", (reply["decision_version_id"], reply["candidate_id"]))
                return reply, tuple(await cursor.fetchall())

    async def create_merchant_rejudgement_job(
        self, *, job_id: UUID, session_id: UUID, client_id: UUID, reply_id: UUID,
        idempotency_key: UUID, request_hash: str,
    ) -> tuple[StoredJob, bool]:
        """Create exactly one asynchronous rejudgement job per merchant reply."""
        await self._require_owned_resource("merchant_replies", reply_id, client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """select r.*,d.is_current,d.status as decision_status,cd.extraction_version_id,v.source_image_id
                       from merchant_replies r join decision_versions d on d.id=r.decision_version_id
                       join candidate_decisions cd on cd.decision_version_id=d.id and cd.candidate_id=r.candidate_id
                       join extraction_versions v on v.id=cd.extraction_version_id
                       where r.id=%s and r.selection_session_id=%s for update""",
                    (reply_id, session_id),
                )
                reply = await cursor.fetchone()
                if reply is None:
                    await self._raise_ownership_or_not_found("merchant_replies", reply_id, client_id)
                if not reply["is_current"] or reply["decision_status"] != "completed":
                    raise DecisionStale("Merchant reply belongs to a stale decision")
                # A candidate with no generated question is deliberately absent
                # from this gate.  Each generated question needs a saved reply;
                # a single candidate can have more than one decision-relevant
                # question and must not be treated as complete after only one.
                await cursor.execute(
                    """select id from followup_questions
                       where decision_version_id=%s and status='completed'""",
                    (reply["decision_version_id"],),
                )
                questioned_ids = {row["id"] for row in await cursor.fetchall()}
                await cursor.execute(
                    """select distinct followup_question_id from merchant_replies
                       where decision_version_id=%s and status <> 'failed'""",
                    (reply["decision_version_id"],),
                )
                replied_question_ids = {row["followup_question_id"] for row in await cursor.fetchall()}
                if not questioned_ids.issubset(replied_question_ids):
                    raise MerchantReplyNotAvailable("Merchant replies are incomplete for this decision")
                await cursor.execute(
                    """select id,candidate_id,candidate_image_id,status,stage,attempt,error_code,extraction_version_id,
                       decision_version_id,processing_mode,input_image_ids,input_set_version,created_at,updated_at,request_hash
                       from analysis_jobs where candidate_id=%s and idempotency_key=%s and job_kind='merchant_rejudgement'""",
                    (reply["candidate_id"], idempotency_key),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflict("Idempotency key belongs to a different rejudgement request")
                    return self._to_job(existing), False
                await cursor.execute(
                    """select id,candidate_id,candidate_image_id,status,stage,attempt,error_code,extraction_version_id,
                       decision_version_id,processing_mode,input_image_ids,input_set_version,created_at,updated_at,request_hash
                       from analysis_jobs where merchant_reply_id=%s and job_kind='merchant_rejudgement'""",
                    (reply_id,),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    return self._to_job(existing), False
                await cursor.execute(
                    """insert into analysis_jobs
                       (id,candidate_id,candidate_image_id,idempotency_key,request_hash,status,attempt,processing_mode,
                        input_image_ids,input_set_version,job_kind,merchant_reply_id)
                       values (%s,%s,%s,%s,%s,'queued',1,'test-fixture',%s,0,'merchant_rejudgement',%s)
                       on conflict (merchant_reply_id) where job_kind = 'merchant_rejudgement' do nothing
                       returning id,candidate_id,candidate_image_id,status,stage,attempt,error_code,extraction_version_id,
                       decision_version_id,processing_mode,input_image_ids,input_set_version,created_at,updated_at""",
                    (job_id, reply["candidate_id"], reply["source_image_id"], idempotency_key, request_hash,
                     [reply["source_image_id"]], reply_id),
                )
                inserted = await cursor.fetchone()
                if inserted is not None:
                    return self._to_job(inserted), True
                await cursor.execute(
                    """select id,candidate_id,candidate_image_id,status,stage,attempt,error_code,extraction_version_id,
                       decision_version_id,processing_mode,input_image_ids,input_set_version,created_at,updated_at
                       from analysis_jobs where merchant_reply_id=%s and job_kind='merchant_rejudgement'""",
                    (reply_id,),
                )
                replay = await cursor.fetchone()
                if replay is None:
                    raise RepositoryError("Merchant rejudgement replay was not visible")
                return self._to_job(replay), False

    async def aggregate_rejudge_anchor(self, *, session_id: UUID, client_id: UUID) -> UUID:
        """Return an internal audit anchor for a session-scoped aggregate action."""
        await self._require_owned_session(session_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select r.id from merchant_replies r
                   join decision_versions d on d.id=r.decision_version_id
                   where r.selection_session_id=%s
                     and d.is_current and d.status='completed'
                   order by r.created_at desc limit 1""",
                (session_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise MerchantReplyNotAvailable("No saved merchant replies are available for rejudgement")
        return row["id"]

    async def merchant_rejudgement_inputs(
        self, *, reply_id: UUID, client_id: UUID,
    ) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
        reply = await self.get_merchant_reply_for_client(reply_id=reply_id, client_id=client_id)
        version, _decisions, inputs = await self.question_context_for_current_decision(
            version_id=reply["decision_version_id"], client_id=client_id
        )
        async with self._connection.cursor() as cursor:
            await cursor.execute("select field_key from followup_questions where id=%s", (reply["followup_question_id"],))
            question = await cursor.fetchone()
        if question is None:
            raise MerchantReplyNotAvailable("Follow-up question no longer exists")
        reply["field_key"] = question["field_key"]
        return reply, version, inputs

    async def merchant_rejudgement_batch(
        self, *, anchor_reply_id: UUID, client_id: UUID,
    ) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
        """Return the whole current merchant-answer batch for one decision.

        Merchant answers are append-only facts.  They are intentionally gathered
        before one rejudgement rather than allowing each answer to stale the
        decision that subsequent answers still reference.
        """
        anchor = await self.get_merchant_reply_for_client(reply_id=anchor_reply_id, client_id=client_id)
        version, _decisions, inputs = await self.question_context_for_current_decision(
            version_id=anchor["decision_version_id"], client_id=client_id
        )
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select recent_preference_evidence from selection_sessions
                   where id=%s""",
                (version["selection_session_id"],),
            )
            session = await cursor.fetchone()
            if session is None:
                await self._raise_ownership_or_not_found(
                    "selection_sessions", version["selection_session_id"], client_id
                )
            # Updating a session need/preferences stales its current Decision,
            # so a current V1 can safely reuse this bounded session snapshot.
            version["recent_preference_evidence"] = list(session["recent_preference_evidence"] or [])
            await cursor.execute(
                """select r.id,r.candidate_id,r.raw_text,r.processing_status,r.parse_status,q.field_key
                   from merchant_replies r join followup_questions q on q.id=r.followup_question_id
                   where r.decision_version_id=%s
                   order by r.created_at""",
                (anchor["decision_version_id"],),
            )
            replies = list(await cursor.fetchall())
            await cursor.execute(
                """select c.merchant_reply_id,c.candidate_id,c.field_key,c.raw_text,c.normalized_value,
                          c.information_status,c.source_type,c.verification_status,c.evidence_strength
                   from merchant_claims c join merchant_replies r on r.id=c.merchant_reply_id
                   where r.decision_version_id=%s order by c.created_at""",
                (anchor["decision_version_id"],),
            )
            claims = list(await cursor.fetchall())
        return anchor, version, inputs, replies, claims

    async def complete_aggregate_merchant_rejudgement(
        self, *, job_id: UUID, client_id: UUID, anchor_reply_id: UUID, reply_ids: tuple[UUID, ...], version_id: UUID,
        decisions: list[dict[str, object]], delta: dict[str, object], input_fingerprint: str,
    ) -> None:
        """Commit one V2 decision from all saved merchant claims in one transaction."""
        await self._require_owned_resource("merchant_replies", anchor_reply_id, client_id)
        owner = _as_owner(client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """select j.id,r.selection_session_id,r.decision_version_id,d.is_current,d.status as decision_status,
                              d.need_snapshot
                       from analysis_jobs j join merchant_replies r on r.id=j.merchant_reply_id
                       join decision_versions d on d.id=r.decision_version_id
                       where j.id=%s and j.merchant_reply_id=%s and j.status='processing'
                         for update""",
                    (job_id, anchor_reply_id),
                )
                state = await cursor.fetchone()
                if state is None:
                    raise MerchantReplyNotAvailable("Rejudgement job is not claimable for completion")
                if not state["is_current"] or state["decision_status"] != "completed":
                    raise DecisionStale("Original decision is no longer current")
                await cursor.execute("select coalesce(max(version),0)+1 as next_version from decision_versions where selection_session_id=%s", (state["selection_session_id"],))
                next_version = (await cursor.fetchone())["next_version"]
                await cursor.execute("update decision_versions set is_current=false,status='stale' where id=%s", (state["decision_version_id"],))
                await cursor.execute(
                    """insert into decision_versions (id,selection_session_id,anonymous_client_id,version,status,rule_version,
                       need_snapshot,input_fingerprint,top_candidate_id,is_current,parent_decision_version_id,trigger_type,trigger_resource_id)
                       values (%s,%s,%s,%s,'completed','v1',%s::jsonb,%s,%s,true,%s,'merchant_reply_batch',%s)""",
                    (version_id, state["selection_session_id"], owner.anonymous_client_id, next_version,
                     psycopg.types.json.Jsonb(state["need_snapshot"]), input_fingerprint, decisions[0]["candidate_id"],
                     state["decision_version_id"], anchor_reply_id),
                )
                for decision in decisions:
                    await cursor.execute(
                        """insert into candidate_decisions (id,decision_version_id,candidate_id,extraction_version_id,action_bucket,
                           rank_within_bucket,overall_order,reasons,risk_flags,missing_critical_fields,score_components,internal_score)
                           values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)""",
                        (decision["id"], version_id, decision["candidate_id"], decision["extraction_version_id"],
                         decision["action_bucket"], decision["rank_within_bucket"], decision["overall_order"],
                         psycopg.types.json.Jsonb(decision["reasons"]), psycopg.types.json.Jsonb(decision["risk_flags"]),
                         psycopg.types.json.Jsonb(decision["missing_critical_fields"]), psycopg.types.json.Jsonb(decision["score_components"]), decision["internal_score"]),
                    )
                await cursor.execute(
                    """insert into decision_deltas (id,selection_session_id,old_decision_version_id,new_decision_version_id,merchant_reply_id,
                       merchant_reply_ids,added_facts,updated_fields,unresolved_fields,resolved_risks,added_risks,ranking_changed,action_tier_changed,
                       old_top_candidate_id,new_top_candidate_id,explanation)
                       values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)""",
                    (delta["id"], state["selection_session_id"], state["decision_version_id"], version_id, anchor_reply_id, list(reply_ids),
                     psycopg.types.json.Jsonb(delta["added_facts"]), psycopg.types.json.Jsonb(delta["updated_fields"]),
                     psycopg.types.json.Jsonb(delta["unresolved_fields"]), psycopg.types.json.Jsonb(delta["resolved_risks"]),
                     psycopg.types.json.Jsonb(delta["added_risks"]), delta["ranking_changed"], delta["action_tier_changed"],
                     delta["old_top_candidate_id"], delta["new_top_candidate_id"], delta["explanation"]),
                )
                await cursor.execute("update analysis_jobs set status='completed',stage='completed',decision_version_id=%s,decision_delta_id=%s,finished_at=now(),updated_at=now() where id=%s", (version_id, delta["id"], job_id))

    async def fail_aggregate_merchant_rejudgement(self, *, job_id: UUID, error_code: ErrorCode) -> None:
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("update analysis_jobs set status='failed',stage='failed',error_code=%s,finished_at=now(),updated_at=now() where id=%s and status in ('queued','processing')", (error_code.value, job_id))

    async def complete_merchant_rejudgement(
        self, *, job_id: UUID, client_id: UUID, reply_id: UUID, version_id: UUID,
        decisions: list[dict[str, object]], parsed_claims: tuple[dict[str, str], ...], parsed_status: str,
        delta: dict[str, object], input_fingerprint: str,
    ) -> None:
        """One transaction: claims/evidence, immutable V2, delta, and terminal job state."""
        await self._require_owned_resource("merchant_replies", reply_id, client_id)
        owner = _as_owner(client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """select j.id,r.*,q.field_key,d.is_current,d.status as decision_status,d.selection_session_id,
                              d.need_snapshot,cd.extraction_version_id
                       from analysis_jobs j join merchant_replies r on r.id=j.merchant_reply_id
                       join followup_questions q on q.id=r.followup_question_id
                       join decision_versions d on d.id=r.decision_version_id
                       join candidate_decisions cd on cd.decision_version_id=d.id and cd.candidate_id=r.candidate_id
                       where j.id=%s and j.merchant_reply_id=%s and j.status='processing'
                         and r.processing_status='processing' for update""",
                    (job_id, reply_id),
                )
                state = await cursor.fetchone()
                if state is None:
                    raise MerchantReplyNotAvailable("Rejudgement job is not claimable for completion")
                if not state["is_current"] or state["decision_status"] != "completed":
                    raise DecisionStale("Original decision is no longer current")
                for claim in parsed_claims:
                    if claim.get("field_key") != state["field_key"]:
                        raise ValueError("Reply parser returned a field outside the follow-up question")
                    await cursor.execute(
                        """select id,normalized_value,information_status,source_type from evidence_items
                           where extraction_version_id=%s and field_name=%s and source_type='product-claim'
                           order by (information_status='explicit') desc,created_at limit 1""",
                        (state["extraction_version_id"], claim["field_key"]),
                    )
                    product = await cursor.fetchone()
                    conflict = _explicit_product_conflict(product, claim["normalized_value"])
                    information_status = 'conflict' if conflict else 'explicit'
                    await cursor.execute(
                        """insert into merchant_claims (id,merchant_reply_id,candidate_id,field_key,raw_text,normalized_value,
                           information_status,source_type,verification_status,evidence_strength,conflicts_with_evidence_id)
                           values (%s,%s,%s,%s,%s,%s,%s,'merchant-claim','unverified','medium',%s)""",
                        (uuid4(), reply_id, state["candidate_id"], claim["field_key"], claim["raw_text"],
                         claim["normalized_value"], information_status, product["id"] if conflict else None),
                    )
                    await cursor.execute(
                        """insert into evidence_items (id,extraction_version_id,field_name,raw_text,normalized_value,model_confidence,
                           information_status,source_type,verification_status,source_image_id,source_location,evidence_strength)
                           select %s,%s,%s,%s,%s,null,%s,'merchant-claim','unverified',source_image_id,'merchant-reply','medium'
                           from extraction_versions where id=%s""",
                        (uuid4(), state["extraction_version_id"], claim["field_key"], claim["raw_text"],
                         claim["normalized_value"], information_status, state["extraction_version_id"]),
                    )
                await cursor.execute("select coalesce(max(version),0)+1 as next_version from decision_versions where selection_session_id=%s", (state["selection_session_id"],))
                next_version = (await cursor.fetchone())["next_version"]
                await cursor.execute("update decision_versions set is_current=false,status='stale' where id=%s", (state["decision_version_id"],))
                await cursor.execute(
                    """insert into decision_versions (id,selection_session_id,anonymous_client_id,version,status,rule_version,
                       need_snapshot,input_fingerprint,top_candidate_id,is_current,parent_decision_version_id,trigger_type,trigger_resource_id)
                       values (%s,%s,%s,%s,'completed','v1',%s::jsonb,%s,%s,true,%s,'merchant_reply',%s)""",
                    (version_id, state["selection_session_id"], owner.anonymous_client_id, next_version,
                     psycopg.types.json.Jsonb(state["need_snapshot"]), input_fingerprint, decisions[0]["candidate_id"],
                     state["decision_version_id"], reply_id),
                )
                for decision in decisions:
                    await cursor.execute(
                        """insert into candidate_decisions (id,decision_version_id,candidate_id,extraction_version_id,action_bucket,
                           rank_within_bucket,overall_order,reasons,risk_flags,missing_critical_fields,score_components,internal_score)
                           values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)""",
                        (decision["id"], version_id, decision["candidate_id"], decision["extraction_version_id"],
                         decision["action_bucket"], decision["rank_within_bucket"], decision["overall_order"],
                         psycopg.types.json.Jsonb(decision["reasons"]), psycopg.types.json.Jsonb(decision["risk_flags"]),
                         psycopg.types.json.Jsonb(decision["missing_critical_fields"]), psycopg.types.json.Jsonb(decision["score_components"]), decision["internal_score"]),
                    )
                await cursor.execute(
                    """insert into decision_deltas (id,selection_session_id,old_decision_version_id,new_decision_version_id,merchant_reply_id,
                       added_facts,updated_fields,unresolved_fields,resolved_risks,added_risks,ranking_changed,action_tier_changed,
                       old_top_candidate_id,new_top_candidate_id,explanation)
                       values (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)""",
                    (delta["id"], state["selection_session_id"], state["decision_version_id"], version_id, reply_id,
                     psycopg.types.json.Jsonb(delta["added_facts"]), psycopg.types.json.Jsonb(delta["updated_fields"]),
                     psycopg.types.json.Jsonb(delta["unresolved_fields"]), psycopg.types.json.Jsonb(delta["resolved_risks"]),
                     psycopg.types.json.Jsonb(delta["added_risks"]), delta["ranking_changed"], delta["action_tier_changed"],
                     delta["old_top_candidate_id"], delta["new_top_candidate_id"], delta["explanation"]),
                )
                await cursor.execute("update merchant_replies set status='parsed',processing_status='completed',parse_status=%s where id=%s", (parsed_status, reply_id))
                await cursor.execute("update analysis_jobs set status='completed',stage='completed',decision_version_id=%s,decision_delta_id=%s,finished_at=now(),updated_at=now() where id=%s", (version_id, delta["id"], job_id))

    async def fail_merchant_rejudgement(self, *, job_id: UUID, reply_id: UUID, error_code: ErrorCode) -> None:
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("update merchant_replies set status='failed',processing_status='failed' where id=%s and processing_status in ('queued','processing')", (reply_id,))
                await cursor.execute("update analysis_jobs set status='failed',stage='failed',error_code=%s,finished_at=now(),updated_at=now() where id=%s and status in ('queued','processing')", (error_code.value, job_id))

    async def complete_nonrejudgable_merchant_reply(self, *, job_id: UUID, reply_id: UUID, parse_status: str) -> None:
        """A parsed evasive/non-answer remains history; it must not stale a valid decision."""
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("update merchant_replies set status='parsed',processing_status='completed',parse_status=%s where id=%s and processing_status='processing'", (parse_status, reply_id))
                await cursor.execute("update analysis_jobs set status='failed',stage='failed',error_code='candidate_extraction_not_retryable',finished_at=now(),updated_at=now() where id=%s and status='processing'", (job_id,))

    async def get_decision_delta_for_client(self, *, delta_id: UUID, client_id: UUID) -> dict[str, object]:
        await self._require_owned_resource("decision_deltas", delta_id, client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute("select d.* from decision_deltas d where d.id=%s", (delta_id,))
            row = await cursor.fetchone()
        if row is None:
            await self._raise_ownership_or_not_found("decision_deltas", delta_id, client_id)
        return row

    async def stale_current_decision_for_session(self, *, session_id: UUID) -> None:
        async with self._connection.cursor() as cursor:
            await cursor.execute("update decision_versions set is_current=false,status='stale' where selection_session_id=%s and is_current", (session_id,))
        await self._connection.commit()

    async def stale_current_decision_for_candidate(self, *, candidate_id: UUID) -> None:
        async with self._connection.cursor() as cursor:
            await cursor.execute("""update decision_versions set is_current=false,status='stale'
                where selection_session_id=(select selection_session_id from candidates where id=%s) and is_current""", (candidate_id,))
        await self._connection.commit()

    async def delete_candidate(self, *, candidate_id: UUID, client_id: UUID) -> tuple[UUID, ...]:
        """Soft-delete only one owned candidate and stop its pending Jobs."""
        await self._require_owned_candidate(candidate_id, client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("select id from candidate_images where candidate_id=%s and status <> 'deleted' for update", (candidate_id,))
                image_ids = tuple(row["id"] for row in await cursor.fetchall())
                await cursor.execute("update analysis_jobs set status='failed', stage='failed', error_code='worker_interrupted', finished_at=now(), updated_at=now() where candidate_id=%s and status in ('queued','processing')", (candidate_id,))
                await cursor.execute("update candidate_images set status='deleted', deleted_at=now() where candidate_id=%s and status <> 'deleted'", (candidate_id,))
                await cursor.execute("update extraction_versions set status='stale', is_current=false where candidate_id=%s and is_current", (candidate_id,))
                await cursor.execute("update candidates set status='deleted', deleted_at=now(), image_set_version=image_set_version+1 where id=%s", (candidate_id,))
                await cursor.execute("""update decision_versions set is_current=false,status='stale'
                    where selection_session_id=(select selection_session_id from candidates where id=%s) and is_current""", (candidate_id,))
        return image_ids

    async def mark_image_deleted(self, *, image_id: UUID, client_id: UUID) -> None:
        await self.get_image_for_client(image_id=image_id, client_id=client_id)
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute("select candidate_id from candidate_images where id=%s for update", (image_id,))
                row = await cursor.fetchone()
                if row is None:
                    raise ResourceNotFound("Candidate image not found")
                await cursor.execute("update candidate_images set status='deleted', deleted_at=now() where id=%s", (image_id,))
                await cursor.execute("update candidates set image_set_version=image_set_version+1 where id=%s", (row["candidate_id"],))
                await cursor.execute("update extraction_versions set status='stale', is_current=false where candidate_id=%s and is_current", (row["candidate_id"],))
                await cursor.execute("update analysis_jobs set status='failed', stage='failed', error_code='worker_interrupted', finished_at=now(), updated_at=now() where candidate_id=%s and status in ('queued','processing')", (row["candidate_id"],))

    async def recover_interrupted_jobs(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=90)
        # A queued job is only recovered after the same bounded timeout as a
        # processing job. This leaves normal newly queued work alone while
        # making an enqueue failure whose error persistence was interrupted
        # recoverable after application restart.
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """update analysis_jobs set status='failed', stage='failed', error_code='worker_interrupted',
                       finished_at=now(), updated_at=now()
                       where (status='processing' and started_at < %s)
                          or (status='queued' and created_at < %s)
                       returning candidate_image_id""",
                    (cutoff, cutoff),
                )
                recovered = await cursor.fetchall()
                changed = len(recovered)
                for row in recovered:
                    await cursor.execute(
                        """update candidate_images set status='failed', error_code='worker_interrupted'
                           where id=%s and status != 'deleted'""",
                        (row["candidate_image_id"],),
                    )
        return changed

    async def get_claimed_job(self, *, job_id: UUID) -> StoredJob:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id, candidate_id, candidate_image_id, status, stage, attempt, error_code, extraction_version_id, processing_mode, input_image_ids, input_set_version, created_at, updated_at
                   from analysis_jobs where id=%s and status='processing'""", (job_id,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise ResourceNotFound("Claimed job not found")
        return self._to_job(row)

    async def get_job_input_images(self, *, job_id: UUID) -> tuple[dict[str, object], ...]:
        """Read the normalized hashes used by one claimed job, in UI order."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select i.id, i.display_order, i.sanitized_sha256
                   from analysis_jobs j join candidate_images i on i.id = any(j.input_image_ids)
                   where j.id=%s order by i.display_order""",
                (job_id,),
            )
            return tuple(await cursor.fetchall())

    async def append_ai_call_log_for_job(self, *, log: AiCallLog) -> None:
        """Append non-secret audit metadata from a worker-owned job.

        The worker is already constrained by an atomic claim; this avoids
        inventing a client context solely for a failure audit row.
        """
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """insert into ai_call_logs
                   (id,analysis_job_id,provider,model_identifier,provider_version,request_metadata,processing_mode,latency_ms,input_tokens,output_tokens,error_code)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (log.id, log.analysis_job_id, log.provider, log.model_identifier,
                 log.provider_version, psycopg.types.json.Jsonb(log.request_metadata or {}), log.processing_mode.value, log.latency_ms, log.input_tokens,
                 log.output_tokens, log.error_code.value if log.error_code else None),
            )
        await self._connection.commit()

    async def list_jobs_for_admin(self, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select id as job_id, job_kind as job_type, status, processing_mode, error_code,
                          created_at, finished_at as completed_at
                   from analysis_jobs order by created_at desc limit %s""", (limit,)
            )
            return tuple(await cursor.fetchall())

    async def list_ai_calls_for_admin(self, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select j.job_kind as chain_type, l.provider, l.model_identifier as model,
                          l.request_metadata->>'prompt_version' as prompt_version,
                          l.request_metadata->>'schema_version' as schema_version,
                          l.processing_mode, l.latency_ms, l.input_tokens as input_size,
                          l.output_tokens as output_size, (l.error_code is null) as success,
                          l.error_code, l.created_at
                   from ai_call_logs l join analysis_jobs j on j.id=l.analysis_job_id
                   order by l.created_at desc limit %s""", (limit,)
            )
            return tuple(await cursor.fetchall())

    async def fail_extraction_job(self, *, job_id: UUID, error_code: ErrorCode) -> None:
        """Failure never creates an ExtractionVersion or evidence rows."""
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """update analysis_jobs set status='failed', stage='failed', error_code=%s,
                       finished_at=now(), updated_at=now() where id=%s and status in ('queued', 'processing')
                       returning candidate_image_id""",
                    (error_code.value, job_id),
                )
                row = await cursor.fetchone()
                if row is not None:
                    await cursor.execute(
                        """update candidate_images set status='failed', error_code=%s
                           where id=%s and status != 'deleted'""",
                        (error_code.value, row["candidate_image_id"]),
                    )

    async def fail_session_decision_job(self, *, job_id: UUID, error_code: ErrorCode) -> None:
        async with self._connection.cursor() as cursor:
            await cursor.execute("""update analysis_jobs set status='failed',stage='failed',error_code=%s,finished_at=now(),updated_at=now()
                where id=%s and job_kind='session_decision' and status in ('queued','processing')""", (error_code.value, job_id))
        await self._connection.commit()

    async def complete_extraction_job(
        self,
        *,
        job_id: UUID,
        version_id: UUID,
        schema_version: str,
        evidence_items: tuple[EvidenceItem, ...],
        ai_log: AiCallLog,
        temporary_image_deleted: bool,
    ) -> None:
        """Append one immutable version and make it current only for its image set."""
        # Phase 3 keeps sanitized private objects until the image is deleted so
        # Image 1 can participate in the single joint Image 1 + Image 2 call.
        del temporary_image_deleted
        async with self._connection.transaction():
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """select candidate_id, candidate_image_id, input_image_ids, input_set_version from analysis_jobs
                       where id=%s and status='processing' for update""",
                    (job_id,),
                )
                job = await cursor.fetchone()
                if job is None:
                    raise RepositoryError("Job is not claimable for completion")
                await cursor.execute(
                    "select id, status from candidate_images where candidate_id=%s and id = any(%s) for update",
                    (job["candidate_id"], list(job["input_image_ids"])),
                )
                images = list(await cursor.fetchall())
                input_ids = tuple(job["input_image_ids"])
                if len(images) != len(input_ids) or any(image["status"] == "deleted" for image in images):
                    raise RepositoryError("Candidate image set changed before extraction completion")
                input_id_set = set(input_ids)
                if any(item.source_image_id not in input_id_set for item in evidence_items):
                    raise ValueError("Evidence references an image outside the Job input set")
                await cursor.execute(
                    "select image_set_version from candidates where id=%s for update",
                    (job["candidate_id"],),
                )
                candidate = await cursor.fetchone()
                if candidate is None:
                    raise RepositoryError("Candidate was deleted before extraction completion")
                is_current = candidate["image_set_version"] == job["input_set_version"]
                if is_current:
                    await cursor.execute(
                        "update extraction_versions set is_current=false where candidate_id=%s and is_current",
                        (job["candidate_id"],),
                    )
                    await cursor.execute("""update decision_versions set is_current=false,status='stale'
                        where selection_session_id=(select selection_session_id from candidates where id=%s) and is_current""", (job["candidate_id"],))
                await cursor.execute(
                    """insert into extraction_versions
                       (id, candidate_id, source_image_id, source_image_ids, input_set_version, is_current, status, schema_version)
                       values (%s, %s, %s, %s, %s, %s, 'completed', %s)""",
                    (version_id, job["candidate_id"], job["candidate_image_id"], list(input_ids), job["input_set_version"], is_current, schema_version),
                )
                for item in evidence_items:
                    if item.extraction_version_id != version_id:
                        raise ValueError("Evidence item belongs to another extraction version")
                    await cursor.execute(
                        """insert into evidence_items
                           (id, extraction_version_id, field_name, raw_text, normalized_value, model_confidence,
                            information_status, source_type, verification_status, source_image_id, source_location,
                            evidence_strength, created_at)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (item.id, item.extraction_version_id, item.field_name, item.raw_text,
                         item.normalized_value, item.model_confidence, item.information_status.value,
                         item.source_type.value, item.verification_status.value, item.source_image_id,
                         item.source_location, item.evidence_strength.value, item.created_at),
                    )
                await cursor.execute(
                    """insert into ai_call_logs
                       (id, analysis_job_id, provider, model_identifier, provider_version, request_metadata, processing_mode, latency_ms,
                        input_tokens, output_tokens, error_code)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (ai_log.id, job_id, ai_log.provider, ai_log.model_identifier,
                     ai_log.provider_version, psycopg.types.json.Jsonb(ai_log.request_metadata or {}), ai_log.processing_mode.value,
                     ai_log.latency_ms, ai_log.input_tokens, ai_log.output_tokens,
                     ai_log.error_code.value if ai_log.error_code else None),
                )
                await cursor.execute(
                    """update analysis_jobs set status='completed', stage='completed', extraction_version_id=%s,
                       processing_mode=%s, finished_at=now(), updated_at=now() where id=%s""",
                    (version_id, ai_log.processing_mode.value, job_id),
                )
                await cursor.execute(
                    "update candidate_images set status='completed', error_code=null where id = any(%s) and status <> 'deleted'",
                    (list(input_ids),),
                )

    async def create_extraction_version(
        self, *, version_id: UUID, candidate_id: UUID, image_id: UUID, client_id: UUID, schema_version: str
    ) -> None:
        """Insert-only API: extraction snapshots never expose an update operation."""
        await self._require_owned_candidate(candidate_id, client_id)
        await self._require_image_for_candidate(image_id, candidate_id)
        try:
            async with self._connection.cursor() as cursor:
                await cursor.execute(
                    """insert into extraction_versions
                       (id, candidate_id, source_image_id, status, schema_version)
                       values (%s, %s, %s, 'completed', %s)""",
                    (version_id, candidate_id, image_id, schema_version),
                )
            await self._connection.commit()
        except psycopg.errors.UniqueViolation as exc:
            await self._connection.rollback()
            raise ImmutableVersionError("Extraction versions are immutable") from exc

    async def append_evidence_items(
        self, *, client_id: UUID, extraction_version_id: UUID, items: tuple[EvidenceItem, ...]
    ) -> None:
        """Append evidence only after confirming the parent version belongs to the client."""
        await self._require_owned_extraction_version(extraction_version_id, client_id)
        if any(item.extraction_version_id != extraction_version_id for item in items):
            raise ValueError("All evidence items must belong to the supplied extraction version")
        async with self._connection.cursor() as cursor:
            for item in items:
                await cursor.execute(
                    """insert into evidence_items
                       (id, extraction_version_id, field_name, raw_text, normalized_value, model_confidence, information_status, source_type,
                        verification_status, source_image_id, source_location, evidence_strength, created_at)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        item.id, item.extraction_version_id, item.field_name, item.raw_text, item.normalized_value, item.model_confidence,
                        item.information_status.value, item.source_type.value,
                        item.verification_status.value, item.source_image_id, item.source_location,
                        item.evidence_strength.value, item.created_at,
                    ),
                )
        await self._connection.commit()

    async def append_ai_call_log(self, *, client_id: UUID, log: AiCallLog) -> None:
        """Append non-secret provider metadata; API keys and image paths are never accepted."""
        await self.get_job_for_client(job_id=log.analysis_job_id, client_id=client_id)
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """insert into ai_call_logs
                   (id, analysis_job_id, provider, model_identifier, provider_version, request_metadata, processing_mode, latency_ms,
                    input_tokens, output_tokens, error_code)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    log.id, log.analysis_job_id, log.provider, log.model_identifier,
                    log.provider_version, psycopg.types.json.Jsonb(log.request_metadata or {}), log.processing_mode.value, log.latency_ms, log.input_tokens, log.output_tokens,
                    log.error_code.value if log.error_code is not None else None,
                ),
            )
        await self._connection.commit()

    async def require_session_for_owner(self, *, session_id: UUID, owner: OwnerLike) -> None:
        """Authorize a root Selection Session for either owner branch."""

        await self._require_owned_session(session_id, owner)

    async def require_candidate_for_owner(self, *, candidate_id: UUID, owner: OwnerLike) -> None:
        """Authorize a candidate by resolving its owning Selection Session."""

        await self._require_owned_candidate(candidate_id, owner)

    async def _require_owned_session(self, session_id: UUID, owner: OwnerLike) -> None:
        await self._require_owned_resource("selection_sessions", session_id, owner)

    async def _require_owned_candidate(self, candidate_id: UUID, owner: OwnerLike) -> None:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                """select s.user_id, s.anonymous_client_id from candidates c
                   join selection_sessions s on s.id = c.selection_session_id
                   where c.id = %s and c.status='active'""",
                (candidate_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ResourceNotFound("Candidate not found")
        if not _owner_matches(owner, row):
            raise OwnershipDenied("Candidate belongs to another owner")

    async def _require_image_for_candidate(self, image_id: UUID, candidate_id: UUID) -> None:
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                "select 1 from candidate_images where id = %s and candidate_id = %s",
                (image_id, candidate_id),
            )
            if await cursor.fetchone() is None:
                raise ResourceNotFound("Candidate image not found for candidate")

    async def _require_owned_extraction_version(self, version_id: UUID, owner: OwnerLike) -> None:
        await self._require_owned_resource("extraction_versions", version_id, owner)

    async def _require_owned_resource(self, table: str, resource_id: UUID, owner: OwnerLike) -> None:
        owner_queries = {
            "selection_sessions": "select user_id, anonymous_client_id from selection_sessions where id = %s",
            "candidates": """select s.user_id, s.anonymous_client_id from candidates r
                join selection_sessions s on s.id = r.selection_session_id where r.id = %s""",
            "candidate_images": """select s.user_id, s.anonymous_client_id from candidate_images r
                join candidates c on c.id = r.candidate_id
                join selection_sessions s on s.id = c.selection_session_id where r.id = %s""",
            "analysis_jobs": """select s.user_id, s.anonymous_client_id from analysis_jobs r
                join candidates c on c.id = r.candidate_id
                join selection_sessions s on s.id = c.selection_session_id where r.id = %s""",
            "extraction_versions": """select s.user_id, s.anonymous_client_id from extraction_versions r
                join candidates c on c.id = r.candidate_id
                join selection_sessions s on s.id = c.selection_session_id where r.id = %s""",
            "decision_versions": """select s.user_id, s.anonymous_client_id from decision_versions r
                join selection_sessions s on s.id = r.selection_session_id where r.id = %s""",
            "followup_questions": """select s.user_id, s.anonymous_client_id from followup_questions r
                join selection_sessions s on s.id = r.selection_session_id where r.id = %s""",
            "merchant_replies": """select s.user_id, s.anonymous_client_id from merchant_replies r
                join selection_sessions s on s.id = r.selection_session_id where r.id = %s""",
            "decision_deltas": """select s.user_id, s.anonymous_client_id from decision_deltas r
                join selection_sessions s on s.id = r.selection_session_id where r.id = %s""",
        }
        query = owner_queries.get(table)
        if query is None:
            raise ValueError(f"Unsupported owner resource: {table}")
        async with self._connection.cursor() as cursor:
            await cursor.execute(query, (resource_id,))
            row = await cursor.fetchone()
        if row is None:
            not_found_codes = {
                "selection_sessions": ErrorCode.SELECTION_SESSION_NOT_FOUND,
                "candidates": ErrorCode.CANDIDATE_NOT_FOUND,
                "candidate_images": ErrorCode.CANDIDATE_IMAGE_NOT_FOUND,
                "merchant_replies": ErrorCode.MERCHANT_REPLY_NOT_FOUND,
                "decision_deltas": ErrorCode.DECISION_DELTA_NOT_FOUND,
            }
            raise ResourceNotFound(f"{table} not found", error_code=not_found_codes.get(table))
        if not _owner_matches(owner, row):
            raise OwnershipDenied("Resource belongs to another owner")

    async def _raise_ownership_or_not_found(self, table: str, resource_id: UUID, owner: OwnerLike) -> None:
        owner_queries = {
            "selection_sessions": "select 1 from selection_sessions where id = %s",
            "analysis_jobs": "select 1 from analysis_jobs where id = %s",
            "decision_versions": "select 1 from decision_versions where id = %s",
            "merchant_replies": "select 1 from merchant_replies where id = %s",
            "decision_deltas": "select 1 from decision_deltas where id = %s",
        }
        query = owner_queries.get(table)
        if query is None:
            raise ValueError(f"Unsupported owner resource: {table}")
        async with self._connection.cursor() as cursor:
            await cursor.execute(query, (resource_id,))
            exists = await cursor.fetchone() is not None
        if exists:
            raise OwnershipDenied("Resource belongs to another owner")
        resource_names = {
            "selection_sessions": "Selection session",
            "candidate_images": "Candidate image",
            "analysis_jobs": "Analysis job",
            "decision_versions": "Decision version",
            "merchant_replies": "Merchant reply",
            "decision_deltas": "Decision delta",
        }
        raise ResourceNotFound(f"{resource_names.get(table, 'Resource')} not found")

    @staticmethod
    def _to_image(row: dict[str, object]) -> StoredImage:
        return StoredImage(
            id=row["image_id"], candidate_id=row["candidate_id"], content_type=row["content_type"],
            size_bytes=row["size_bytes"], source_sha256=row["source_sha256"], sanitized_sha256=row["sanitized_sha256"], width=row["width"], height=row["height"],
            display_order=row.get("image_display_order", 1), status=CandidateImageStatus(row["image_status"]), created_at=row["image_created_at"],
        )  # type: ignore[arg-type]

    @staticmethod
    def _to_job(row: dict[str, object]) -> StoredJob:
        mode = row["processing_mode"]
        return StoredJob(
            id=row["id"], candidate_id=row["candidate_id"], candidate_image_id=row["candidate_image_id"],
            status=JobState(row["status"]), stage=JobStage(row["stage"]) if row.get("stage") else None, attempt=row["attempt"],
            error_code=ErrorCode(row["error_code"]) if row.get("error_code") else None,
            extraction_version_id=row.get("extraction_version_id"),
            decision_version_id=row.get("decision_version_id"),
            decision_delta_id=row.get("decision_delta_id"),
            input_image_ids=tuple(row.get("input_image_ids") or (row["candidate_image_id"],)),
            input_set_version=int(row.get("input_set_version") or 0),
            processing_mode=ProcessingMode(mode) if mode is not None else None,
            created_at=row["created_at"], updated_at=row["updated_at"],
        )  # type: ignore[arg-type]

    @staticmethod
    def _stored_user_preferences(row: dict[str, object]) -> StoredUserPreferences:
        profile = row["profile"]
        if not isinstance(profile, dict):
            raise RepositoryError("Persisted preference profile is malformed")
        return StoredUserPreferences(
            user_id=row["user_id"],
            profile=dict(profile),
            schema_version=int(row["schema_version"]),
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )  # type: ignore[arg-type]

    @staticmethod
    def _stored_preference_evidence(row: dict[str, object]) -> StoredPreferenceEvidence:
        return StoredPreferenceEvidence(
            id=row["id"],
            user_id=row["user_id"],
            target_type=row["target_type"],
            target_value=row["target_value"],
            polarity=row["polarity"],
            confidence=row["confidence"],
            issue_source=row["issue_source"],
            source_brew_session_id=row["source_brew_session_id"],
            created_at=row["created_at"],
        )  # type: ignore[arg-type]
