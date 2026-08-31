from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from guancha_api.auth.models import OwnerContext, repository_owner, resolve_owner
from guancha_api.repositories.idempotency import request_hash
from guancha_api.repositories.postgres import CandidateExtractionInProgress, PostgresPhase2Repository
from guancha_api.schemas.contracts import Candidate, CreateCandidateRequest, SelectionNeedInput, SelectionSession
from guancha_api.schemas.contracts import ErrorCode, ProcessingMode, UploadCandidateImageResponse, CandidateImageMetadata, AnalysisJobResponse
from guancha_api.infrastructure.image_pipeline import sanitize_image_upload
from guancha_api.infrastructure.storage.interfaces import (
    TemporaryImageCleanupError,
    TemporaryPrivateStorage,
)
from guancha_api.infrastructure.temporary_images import temporary_image_object_key
from guancha_api.application.job_runner import FakeExtractionJobRunner
from guancha_api.application.task_runners import InProcessTaskRunner, ManualTaskRunner, TaskEnqueueError
from guancha_api.providers.execution import StructuredVisionProvider
from guancha_api.providers.mimo import MiMoVisionProvider
from guancha_api.providers.openai import OpenAIResponsesProvider


REQUEST_BOUND_EXTRACTION_TIMEOUT_SECONDS = 50


class Phase2ExtractionService:
    def __init__(
        self,
        repository: PostgresPhase2Repository,
        worker_repository_factory: Callable[[], Awaitable[PostgresPhase2Repository]] | None = None,
    ) -> None:
        self.repository = repository
        self.worker_repository_factory = worker_repository_factory

    async def _run_extraction_job(
        self, *, job_id: UUID, provider: StructuredVisionProvider, storage: TemporaryPrivateStorage
    ) -> None:
        """Run a job on its own PostgreSQL connection when available.

        Psycopg async connections are connection-scoped and cannot safely serve
        both an HTTP request and an independently running worker at once.
        Tests can deliberately omit the factory and retain their single,
        deterministic ManualTaskRunner repository.

        Repository acquisition is intentionally inside the lifetime boundary.
        Callers persist WORKER_INTERRUPTED through the still-live request
        repository if acquisition or execution raises.
        """
        worker_repository = self.repository
        owns_worker_repository = False
        try:
            if self.worker_repository_factory is not None:
                worker_repository = await self.worker_repository_factory()
                owns_worker_repository = True
            await FakeExtractionJobRunner(
                worker_repository,
                provider,
                storage,
                timeout_seconds=REQUEST_BOUND_EXTRACTION_TIMEOUT_SECONDS,
            ).run(job_id=job_id)
        finally:
            if owns_worker_repository:
                await worker_repository.close()

    async def create_session(
        self, *, idempotency_key: UUID, need: SelectionNeedInput,
        recent_preference_evidence: tuple[dict[str, object], ...] = (),
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> tuple[SelectionSession, bool]:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        now = datetime.now(timezone.utc)
        evidence = tuple(item for item in recent_preference_evidence if item.get("confidence") == "low")[-12:]
        row, created = await self.repository.create_selection_session(
            session_id=uuid4(), client_id=repository_owner(request_owner), idempotency_key=idempotency_key,
            request_hash=request_hash({"need": need.model_dump(mode="json"), "recent_preference_evidence": evidence}), need=need.model_dump(mode="json"), recent_preference_evidence=evidence,
            expires_at=now + timedelta(days=15),
        )
        return self._session(row), created

    async def get_session(
        self, *, session_id: UUID, owner: OwnerContext | None = None, client_id: UUID | None = None
    ) -> SelectionSession:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        return self._session(await self.repository.get_selection_session_for_client(session_id=session_id, client_id=repository_owner(request_owner)))

    async def update_session_need(
        self, *, session_id: UUID, need: SelectionNeedInput,
        recent_preference_evidence: tuple[dict[str, object], ...] = (),
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> SelectionSession:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        row = await self.repository.update_selection_need_for_client(
            session_id=session_id, client_id=repository_owner(request_owner), need=need.model_dump(mode="json"), recent_preference_evidence=tuple(item for item in recent_preference_evidence if item.get("confidence") == "low")[-12:]
        )
        return self._session(row)

    async def create_candidate(
        self, *, session_id: UUID, idempotency_key: UUID, request: CreateCandidateRequest,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> tuple[Candidate, bool]:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        row, created = await self.repository.create_candidate(candidate_id=uuid4(), session_id=session_id, client_id=repository_owner(request_owner), label=request.display_label, display_name=request.display_name, idempotency_key=idempotency_key, request_hash=request_hash(request.model_dump(mode="json")))
        if created:
            await self.repository.stale_current_decision_for_session(session_id=session_id)
        return self._candidate(row), created

    async def list_candidates(
        self, *, session_id: UUID, owner: OwnerContext | None = None, client_id: UUID | None = None
    ) -> tuple[Candidate, ...]:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        return tuple(self._candidate(row) for row in await self.repository.list_candidates_for_session(session_id=session_id, client_id=repository_owner(request_owner)))

    async def delete_candidate(
        self, *, candidate_id: UUID, storage: TemporaryPrivateStorage,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> None:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        image_ids = await self.repository.delete_candidate(candidate_id=candidate_id, client_id=repository_owner(request_owner))
        for image_id in image_ids:
            try:
                await storage.delete(object_key=temporary_image_object_key(image_id))
            except KeyError:
                continue

    async def upload_image(
        self, *, candidate_id: UUID, idempotency_key: UUID,
        data: bytes, declared_content_type: str, storage: TemporaryPrivateStorage,
        task_runner: InProcessTaskRunner | ManualTaskRunner, provider: StructuredVisionProvider,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> tuple[UploadCandidateImageResponse, bool]:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        image = sanitize_image_upload(data=data, declared_content_type=declared_content_type)
        # The stable request digest binds the logical target to sanitized pixels.
        digest = request_hash({"candidate_id": str(candidate_id), "sanitized_sha256": image.sanitized_sha256, "content_type": image.content_type})
        # Deterministic private identities make a storage-cleanup failure
        # recoverable on the exact same idempotent request.  A competing
        # identical request writes the same private object, so the loser must
        # never delete it; requests with a different digest get a different
        # key and can never overwrite the winner's image.
        identity = f"guancha:phase2:image:{candidate_id}:{idempotency_key}:{digest}"
        image_id = uuid5(NAMESPACE_URL, identity)
        job_id = uuid5(NAMESPACE_URL, f"{identity}:job")
        object_key = temporary_image_object_key(image_id)
        replay = await self.repository.find_image_job_replay(
            candidate_id=candidate_id, client_id=repository_owner(request_owner), idempotency_key=idempotency_key, request_hash=digest
        )
        if replay is not None:
            return self._upload_response(replay.image, replay.job), False
        try:
            await storage.put_private(object_key=object_key, content_type=image.content_type, data=image.data)
        except Exception as storage_error:
            raise TaskEnqueueError("Temporary private storage did not accept the image") from storage_error
        try:
            result = await self.repository.create_image_and_initial_job(
                image_id=image_id, job_id=job_id, candidate_id=candidate_id, client_id=repository_owner(request_owner),
                idempotency_key=idempotency_key, content_type=image.content_type, size_bytes=image.size_bytes,
                source_sha256=image.source_sha256,
                sanitized_sha256=image.sanitized_sha256,
                request_hash=digest,
                width=image.width,
                height=image.height,
                processing_mode=provider.processing_mode,
                stage_until_selection_start=isinstance(
                    provider, (MiMoVisionProvider, OpenAIResponsesProvider)
                ),
            )
        except Exception:
            try:
                await storage.delete(object_key=object_key)
            except Exception as cleanup_error:
                raise TemporaryImageCleanupError(
                    "Unable to remove the temporary image after a database failure"
                ) from cleanup_error
            raise
        if not result.created:
            # With a deterministic content-bound key this is the winner's
            # object too. Deleting it here would turn a correct concurrent
            # replay into a queued Job with no private image.
            return self._upload_response(result.image, result.job), False
        await self.repository.stale_current_decision_for_candidate(candidate_id=candidate_id)
        # External vision calls are deliberately staged.  Dispatch happens
        # only after the user starts selection analysis, allowing a two-image
        # candidate to reach the provider in one joint call.  The deterministic
        # FakeProvider keeps the established immediate-dispatch unit-test
        # semantics; it never reaches a network or real model.
        if isinstance(provider, (MiMoVisionProvider, OpenAIResponsesProvider)):
            return self._upload_response(result.image, result.job), True
        try:
            await task_runner.enqueue(
                job_id=result.job.id,
                task=lambda: self._run_extraction_job(
                    job_id=result.job.id, provider=provider, storage=storage
                ),
            )
        except Exception as enqueue_error:
            await self.repository.fail_extraction_job(
                job_id=result.job.id, error_code=ErrorCode.WORKER_INTERRUPTED
            )
            raise TaskEnqueueError("Task runner did not accept the queued job") from enqueue_error
        if isinstance(task_runner, InProcessTaskRunner):
            current_job = await self.repository.get_job_for_client(
                job_id=result.job.id, client_id=repository_owner(request_owner)
            )
            return self._upload_response(result.image, current_job), True
        return self._upload_response(result.image, result.job), True

    async def start_staged_extractions(
        self, *, session_id: UUID,
        storage: TemporaryPrivateStorage,
        task_runner: InProcessTaskRunner | ManualTaskRunner,
        provider: StructuredVisionProvider,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> tuple[AnalysisJobResponse, ...]:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        jobs = await self.repository.list_queued_extraction_jobs_for_session(
            session_id=session_id, client_id=repository_owner(request_owner)
        )

        async def enqueue_one(job: object) -> bool:
            return await task_runner.enqueue(
                job_id=job.id,
                task=lambda job_id=job.id: self._run_extraction_job(
                    job_id=job_id, provider=provider, storage=storage
                ),
            )

        if isinstance(task_runner, InProcessTaskRunner):
            # Each worker gets its own repository from the factory.  Gather is
            # fully awaited, so CloudBase Run cannot reclaim the request while
            # extraction is still running.  Request-repository failure writes
            # and reads happen only after all worker coroutines have finished.
            results = await asyncio.gather(
                *(enqueue_one(job) for job in jobs), return_exceptions=True
            )
            failures: list[BaseException] = []
            for job, result in zip(jobs, results, strict=True):
                if isinstance(result, BaseException):
                    failures.append(result)
                    await self.repository.fail_extraction_job(
                        job_id=job.id, error_code=ErrorCode.WORKER_INTERRUPTED
                    )
            if failures:
                raise TaskEnqueueError(
                    "A staged extraction did not complete"
                ) from failures[0]
            started = []
            for job, accepted in zip(jobs, results, strict=True):
                if accepted:
                    current = await self.repository.get_job_for_client(
                        job_id=job.id, client_id=repository_owner(request_owner)
                    )
                    started.append(self._job_response(current))
            return tuple(started)

        started: list[AnalysisJobResponse] = []
        for job in jobs:
            try:
                accepted = await enqueue_one(job)
            except Exception as enqueue_error:
                await self.repository.fail_extraction_job(
                    job_id=job.id, error_code=ErrorCode.WORKER_INTERRUPTED
                )
                raise TaskEnqueueError(
                    "Task runner did not accept the staged extraction job"
                ) from enqueue_error
            if accepted:
                started.append(self._job_response(job))
        return tuple(started)

    async def list_staged_extractions(
        self, *, session_id: UUID, owner: OwnerContext | None = None, client_id: UUID | None = None
    ) -> tuple[AnalysisJobResponse, ...]:
        """Return queued response anchors without dispatching or emitting."""
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        jobs = await self.repository.list_queued_extraction_jobs_for_session(
            session_id=session_id, client_id=repository_owner(request_owner)
        )
        return tuple(self._job_response(job) for job in jobs)

    async def delete_image(
        self, *, image_id: UUID, storage: TemporaryPrivateStorage,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> None:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        image = await self.repository.get_image_for_client(image_id=image_id, client_id=repository_owner(request_owner))
        job_id = image.get("current_job_id")
        # Persist the recoverable deletion intent before external I/O.  If the
        # private delete fails, a repeated DELETE can safely finish cleanup;
        # the inverse order could leave an apparently usable DB image whose
        # only private object has already disappeared.
        await self.repository.mark_image_deleted(image_id=image_id, client_id=repository_owner(request_owner))
        await self.repository.stale_current_decision_for_candidate(candidate_id=image["candidate_id"])
        if job_id is not None:
            try:
                await storage.delete(object_key=temporary_image_object_key(image_id))
            except Exception as cleanup_error:
                raise TemporaryImageCleanupError(
                    "Unable to remove the temporary image during deletion"
                ) from cleanup_error

    async def reject_retry(self, *, candidate_id: UUID, owner: OwnerContext | None = None, client_id: UUID | None = None) -> None:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        await self.repository.require_candidate_for_client(candidate_id=candidate_id, client_id=repository_owner(request_owner))

    async def get_job(self, *, job_id: UUID, owner: OwnerContext | None = None, client_id: UUID | None = None) -> AnalysisJobResponse:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        return self._job_response(await self.repository.get_job_for_client(job_id=job_id, client_id=repository_owner(request_owner)))

    async def get_image_metadata(
        self, *, image_id: UUID, owner: OwnerContext | None = None, client_id: UUID | None = None
    ) -> CandidateImageMetadata:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        row = await self.repository.get_image_for_client(
            image_id=image_id, client_id=repository_owner(request_owner)
        )
        return CandidateImageMetadata(
            id=row["id"], candidate_id=row["candidate_id"],
            content_type=row["content_type"], size_bytes=row["size_bytes"],
            sha256=row["sanitized_sha256"], width=row["width"], height=row["height"],
            display_order=row["display_order"], status=row["status"], current_job_id=row["current_job_id"],
            error_code=row["error_code"], created_at=row["created_at"],
        )

    async def retry_job(
        self, *, candidate_id: UUID, idempotency_key: UUID, storage: TemporaryPrivateStorage,
        task_runner: InProcessTaskRunner | ManualTaskRunner, provider: StructuredVisionProvider,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> AnalysisJobResponse:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        job = await self.repository.get_latest_job_for_candidate(candidate_id=candidate_id, client_id=repository_owner(request_owner))
        retry_digest = request_hash({"candidate_id": str(candidate_id), "operation": "retry-extraction"})
        if job is None:
            raise ValueError("candidate_extraction_not_retryable")
        if job.status.value == "queued":
            restored = await self.repository.requeue_interrupted_job(
                failed_job_id=job.id, new_job_id=uuid4(), idempotency_key=idempotency_key, request_hash=retry_digest
            )
            if restored is None:
                raise CandidateExtractionInProgress("Candidate already has an active extraction job")
            restored_job, created = restored
            if not created:
                return self._job_response(restored_job)
            raise ValueError("candidate_extraction_not_retryable")
        if job.status.value != "failed" or job.error_code is not ErrorCode.WORKER_INTERRUPTED:
            raise ValueError("candidate_extraction_not_retryable")
        try:
            await storage.read_private(object_key=temporary_image_object_key(job.candidate_image_id))
        except KeyError as exc:
            raise ValueError("candidate_extraction_not_retryable") from exc
        restored = await self.repository.requeue_interrupted_job(
            failed_job_id=job.id, new_job_id=uuid4(), idempotency_key=idempotency_key, request_hash=retry_digest
        )
        if restored is None:
            raise ValueError("candidate_extraction_not_retryable")
        restored_job, created = restored
        if not created:
            return self._job_response(restored_job)
        try:
            await task_runner.enqueue(
                job_id=restored_job.id,
                task=lambda: self._run_extraction_job(job_id=restored_job.id, provider=provider, storage=storage),
            )
        except Exception as enqueue_error:
            await self.repository.fail_extraction_job(job_id=restored_job.id, error_code=ErrorCode.WORKER_INTERRUPTED)
            raise TaskEnqueueError("Task runner did not accept the queued retry") from enqueue_error
        if isinstance(task_runner, InProcessTaskRunner):
            restored_job = await self.repository.get_job_for_client(
                job_id=restored_job.id, client_id=repository_owner(request_owner)
            )
        return self._job_response(restored_job)

    @staticmethod
    def _upload_response(image: object, job: object) -> UploadCandidateImageResponse:
        return UploadCandidateImageResponse(
            image=CandidateImageMetadata(id=image.id, candidate_id=image.candidate_id, content_type=image.content_type, size_bytes=image.size_bytes, sha256=image.sanitized_sha256, width=image.width, height=image.height, display_order=image.display_order, status=image.status, current_job_id=job.id, created_at=image.created_at),
            extraction_job=AnalysisJobResponse(id=job.id, candidate_id=job.candidate_id, candidate_image_id=job.candidate_image_id, status=job.status, stage=job.stage, attempt=job.attempt, error_code=job.error_code, extraction_version_id=job.extraction_version_id, processing_mode=job.processing_mode, created_at=job.created_at, updated_at=job.updated_at),
        )

    @staticmethod
    def _job_response(job: object) -> AnalysisJobResponse:
        return AnalysisJobResponse(
            id=job.id,
            candidate_id=job.candidate_id,
            candidate_image_id=job.candidate_image_id,
            status=job.status,
            stage=job.stage,
            attempt=job.attempt,
            error_code=job.error_code,
            extraction_version_id=job.extraction_version_id,
            decision_version_id=job.decision_version_id,
            decision_delta_id=job.decision_delta_id,
            processing_mode=job.processing_mode,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @staticmethod
    def _session(row: dict[str, object]) -> SelectionSession:
        return SelectionSession(id=row["id"], anonymous_client_id=row["anonymous_client_id"], need=SelectionNeedInput.model_validate(row["need"]), expires_at=row["expires_at"], created_at=row["created_at"], updated_at=row["updated_at"])

    @staticmethod
    def _candidate(row: dict[str, object]) -> Candidate:
        return Candidate(id=row["id"], selection_session_id=row["selection_session_id"], display_label=row["display_label"], display_name=row["display_name"], position=row["display_order"], created_at=row["created_at"])
