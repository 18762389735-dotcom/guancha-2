from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from guancha_api.application import phase2_service as phase2_module
from guancha_api.application.decision_service import SessionDecisionService
from guancha_api.application.merchant_reply_service import MerchantReplyService
from guancha_api.application.phase2_service import Phase2ExtractionService
from guancha_api.application.task_runners import InProcessTaskRunner, ManualTaskRunner, TaskEnqueueError
from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.repositories.postgres import StoredJob
from guancha_api.schemas.contracts import ErrorCode, JobStage, JobState, ProcessingMode


pytestmark = pytest.mark.asyncio


def _job(*, job_id: UUID | None = None) -> StoredJob:
    now = datetime.now(timezone.utc)
    return StoredJob(
        id=job_id or uuid4(),
        candidate_id=uuid4(),
        candidate_image_id=uuid4(),
        status=JobState.QUEUED,
        stage=JobStage.QUEUED,
        attempt=1,
        processing_mode=ProcessingMode.LIVE_AI,
        created_at=now,
        updated_at=now,
    )


class _ExtractionRepository:
    def __init__(self, jobs: tuple[StoredJob, ...]) -> None:
        self.jobs = {job.id: job for job in jobs}
        self.failures: list[tuple[UUID, ErrorCode]] = []
        self.reads: list[UUID] = []

    async def list_queued_extraction_jobs_for_session(self, **_: object) -> tuple[StoredJob, ...]:
        return tuple(self.jobs.values())

    async def get_job_for_client(self, *, job_id: UUID, **_: object) -> StoredJob:
        self.reads.append(job_id)
        return self.jobs[job_id]

    async def fail_extraction_job(self, *, job_id: UUID, error_code: ErrorCode) -> None:
        self.failures.append((job_id, error_code))
        self.jobs[job_id] = replace(
            self.jobs[job_id], status=JobState.FAILED, stage=JobStage.FAILED, error_code=error_code
        )


class _ExtractionWorkerRepository:
    def __init__(self, request_repository: _ExtractionRepository) -> None:
        self.request_repository = request_repository
        self.close_calls = 0

    async def claim_job(self, *, job_id: UUID) -> bool:
        return job_id in self.request_repository.jobs

    async def get_claimed_job(self, *, job_id: UUID) -> StoredJob:
        return replace(self.request_repository.jobs[job_id], status=JobState.PROCESSING)

    async def fail_extraction_job(self, *, job_id: UUID, error_code: ErrorCode) -> None:
        await self.request_repository.fail_extraction_job(job_id=job_id, error_code=error_code)

    async def close(self) -> None:
        self.close_calls += 1


async def test_staged_inprocess_extractions_run_concurrently_and_return_terminal_jobs() -> None:
    jobs = (_job(), _job())
    repository = _ExtractionRepository(jobs)
    service = Phase2ExtractionService(repository)  # type: ignore[arg-type]
    runner = InProcessTaskRunner()
    all_started = asyncio.Event()
    active = 0
    peak_active = 0

    async def extraction(*, job_id: UUID, provider: object, storage: object) -> None:
        del provider, storage
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        if active == len(jobs):
            all_started.set()
        await all_started.wait()
        repository.jobs[job_id] = replace(
            repository.jobs[job_id], status=JobState.COMPLETED, stage=JobStage.COMPLETED
        )
        active -= 1

    service._run_extraction_job = extraction  # type: ignore[method-assign]
    result = await service.start_staged_extractions(
        session_id=uuid4(),
        storage=object(),
        task_runner=runner,
        provider=object(),
        client_id=uuid4(),
    )

    assert peak_active == 2
    assert {item.status for item in result} == {JobState.COMPLETED}
    assert runner.active_count == 0
    assert not hasattr(runner, "_active_tasks")
    assert repository.reads == [job.id for job in jobs]


async def test_worker_repository_factory_failure_marks_staged_job_terminal() -> None:
    job = _job()
    repository = _ExtractionRepository((job,))

    async def failing_factory() -> object:
        raise RuntimeError("fresh connection unavailable")

    service = Phase2ExtractionService(repository, worker_repository_factory=failing_factory)  # type: ignore[arg-type]
    with pytest.raises(TaskEnqueueError):
        await service.start_staged_extractions(
            session_id=uuid4(),
            storage=object(),
            task_runner=InProcessTaskRunner(),
            provider=object(),
            client_id=uuid4(),
        )

    assert repository.failures == [(job.id, ErrorCode.WORKER_INTERRUPTED)]
    assert repository.jobs[job.id].status is JobState.FAILED


async def test_staged_timeout_returns_terminal_ai_timeout_instead_of_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    repository = _ExtractionRepository((job,))
    worker_repositories: list[_ExtractionWorkerRepository] = []

    async def factory() -> _ExtractionWorkerRepository:
        worker = _ExtractionWorkerRepository(repository)
        worker_repositories.append(worker)
        return worker

    class SlowProvider:
        async def extract(self, *, image_object_key: str) -> dict[str, object]:
            del image_object_key
            await asyncio.sleep(0.02)
            return {}

        async def repair_structure(self, *, invalid_response: dict[str, object]) -> dict[str, object]:
            return invalid_response

    storage = InMemoryTemporaryPrivateStorage()
    await storage.put_private(
        object_key=f"temporary/{job.id}", content_type="image/png", data=b"image"
    )
    monkeypatch.setattr(phase2_module, "REQUEST_BOUND_EXTRACTION_TIMEOUT_SECONDS", 0.001)
    service = Phase2ExtractionService(repository, worker_repository_factory=factory)  # type: ignore[arg-type]

    result = await service.start_staged_extractions(
        session_id=uuid4(),
        storage=storage,
        task_runner=InProcessTaskRunner(),
        provider=SlowProvider(),  # type: ignore[arg-type]
        client_id=uuid4(),
    )

    assert result[0].status is JobState.FAILED
    assert result[0].error_code is ErrorCode.AI_TIMEOUT
    assert repository.jobs[job.id].status is JobState.FAILED
    assert len(worker_repositories) == 1
    assert worker_repositories[0].close_calls == 1


async def test_worker_repository_is_closed_once_on_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class WorkerRepository:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    class WorkerRunner:
        should_fail = False

        def __init__(
            self,
            repository: WorkerRepository,
            provider: object,
            storage: object,
            *,
            timeout_seconds: float,
        ) -> None:
            del provider, storage
            self.repository = repository
            self.timeout_seconds = timeout_seconds
            repository.timeout_seconds = timeout_seconds

        async def run(self, *, job_id: UUID) -> None:
            del job_id
            if self.should_fail:
                raise RuntimeError("worker failed")

    monkeypatch.setattr(phase2_module, "FakeExtractionJobRunner", WorkerRunner)
    request_repository = _ExtractionRepository(())
    success_repository = WorkerRepository()
    success_service = Phase2ExtractionService(
        request_repository, worker_repository_factory=lambda: _ready(success_repository)
    )  # type: ignore[arg-type]
    await success_service._run_extraction_job(job_id=uuid4(), provider=object(), storage=object())
    assert success_repository.close_calls == 1
    assert success_repository.timeout_seconds == 50

    failed_repository = WorkerRepository()
    WorkerRunner.should_fail = True
    failed_service = Phase2ExtractionService(
        request_repository, worker_repository_factory=lambda: _ready(failed_repository)
    )  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="worker failed"):
        await failed_service._run_extraction_job(job_id=uuid4(), provider=object(), storage=object())
    assert failed_repository.close_calls == 1
    assert failed_repository.timeout_seconds == 50


async def _ready(value: object) -> object:
    return value


class _DecisionRepository:
    def __init__(self, job: StoredJob) -> None:
        self.job = job

    async def decision_inputs_for_session(self, **_: object) -> tuple[dict[str, object], list[dict[str, object]]]:
        return {"need": {}}, []

    async def create_session_decision_job(self, **_: object) -> tuple[StoredJob, bool]:
        return self.job, True

    async def get_job_for_client(self, **_: object) -> StoredJob:
        return self.job


async def test_inprocess_decision_returns_terminal_job_before_analyze_returns() -> None:
    repository = _DecisionRepository(_job())
    service = SessionDecisionService(repository)  # type: ignore[arg-type]

    async def run(**_: object) -> None:
        repository.job = replace(repository.job, status=JobState.COMPLETED, stage=JobStage.COMPLETED)

    service.run = run  # type: ignore[method-assign]
    result = await service.analyze(
        session_id=uuid4(), idempotency_key=uuid4(), task_runner=InProcessTaskRunner(), client_id=uuid4()
    )
    assert result.status is JobState.COMPLETED


class _MerchantRepository:
    def __init__(self, job: StoredJob) -> None:
        self.job = job

    async def aggregate_rejudge_anchor(self, **_: object) -> UUID:
        return uuid4()

    async def create_merchant_rejudgement_job(self, **_: object) -> tuple[StoredJob, bool]:
        return self.job, True

    async def get_job_for_client(self, **_: object) -> StoredJob:
        return self.job


async def test_inprocess_rejudge_returns_terminal_job_before_rejudge_returns() -> None:
    repository = _MerchantRepository(_job())
    service = MerchantReplyService(repository)  # type: ignore[arg-type]

    async def run_rejudge(**_: object) -> None:
        repository.job = replace(repository.job, status=JobState.COMPLETED, stage=JobStage.COMPLETED)

    service.run_rejudge = run_rejudge  # type: ignore[method-assign]
    result = await service.rejudge(
        session_id=uuid4(), idempotency_key=uuid4(), task_runner=InProcessTaskRunner(), client_id=uuid4()
    )
    assert result.status is JobState.COMPLETED


async def test_manual_runner_keeps_queued_semantics() -> None:
    job = _job()
    repository = _ExtractionRepository((job,))
    service = Phase2ExtractionService(repository)  # type: ignore[arg-type]
    runner = ManualTaskRunner()

    async def extraction(*, job_id: UUID, provider: object, storage: object) -> None:
        del provider, storage
        repository.jobs[job_id] = replace(
            repository.jobs[job_id], status=JobState.COMPLETED, stage=JobStage.COMPLETED
        )

    service._run_extraction_job = extraction  # type: ignore[method-assign]
    result = await service.start_staged_extractions(
        session_id=uuid4(), storage=object(), task_runner=runner, provider=object(), client_id=uuid4()
    )
    assert result[0].status is JobState.QUEUED
    assert runner.pending_count == 1
    await runner.drain()
    assert repository.jobs[job.id].status is JobState.COMPLETED
