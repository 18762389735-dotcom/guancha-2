from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from guancha_api.application.job_runner import FakeExtractionJobRunner
from guancha_api.application.task_runners import InProcessTaskRunner, ManualTaskRunner
from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.providers.fake import FakeProvider
from guancha_api.repositories.postgres import StoredJob
from guancha_api.schemas.contracts import ErrorCode, JobState, ProcessingMode

pytestmark = pytest.mark.asyncio


async def test_in_process_runner_waits_for_request_bound_work_and_shutdowns() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def task() -> None:
        started.set()
        await release.wait()

    runner = InProcessTaskRunner()
    running = asyncio.create_task(runner.enqueue(job_id=uuid4(), task=task))
    await asyncio.wait_for(started.wait(), timeout=0.1)
    assert runner.active_count == 1
    release.set()
    assert await running is True
    assert runner.active_count == 0
    await runner.shutdown()
    assert runner.active_count == 0


async def test_in_process_runner_propagates_request_bound_exception() -> None:
    async def task() -> None:
        raise RuntimeError("expected")

    runner = InProcessTaskRunner()
    with pytest.raises(RuntimeError, match="expected"):
        await runner.enqueue(job_id="job-1", task=task)
    assert runner.active_count == 0


async def test_manual_runner_drain_and_empty_queue_are_explicit() -> None:
    calls: list[str] = []

    async def task() -> None:
        calls.append("run")

    runner = ManualTaskRunner()
    assert runner.pending_count == 0
    assert await runner.run_next() is False
    await runner.enqueue(job_id=uuid4(), task=task)
    await runner.enqueue(job_id=uuid4(), task=task)
    assert runner.pending_count == 2
    assert await runner.drain() == 2
    assert runner.pending_count == 0
    assert calls == ["run", "run"]


async def test_runners_reject_same_pending_job_and_release_identity_after_completion() -> None:
    calls: list[str] = []
    async def work() -> None: calls.append("run")

    manual = ManualTaskRunner()
    assert await manual.enqueue(job_id="same", task=work) is True
    assert await manual.enqueue(job_id="same", task=work) is False
    assert manual.pending_count == 1
    assert await manual.run_next() is True
    assert await manual.enqueue(job_id="same", task=work) is True
    await manual.drain()
    assert calls == ["run", "run"]

    release = asyncio.Event()
    async def blocked() -> None: await release.wait()
    active = InProcessTaskRunner()
    running = asyncio.create_task(active.enqueue(job_id="same", task=blocked))
    await asyncio.sleep(0)
    assert await active.enqueue(job_id="same", task=blocked) is False
    release.set()
    assert await running is True
    assert await active.enqueue(job_id="same", task=work) is True
    await active.shutdown()


async def test_manual_shutdown_discards_pending_work_and_releases_job_ids() -> None:
    runner = ManualTaskRunner()
    calls: list[str] = []
    async def task() -> None: calls.append("ran")
    assert await runner.enqueue(job_id="pending", task=task) is True
    await runner.shutdown()
    assert runner.pending_count == 0
    assert await runner.enqueue(job_id="pending", task=task) is True
    await runner.drain()
    assert calls == ["ran"]


def _payload() -> dict[str, object]:
    return {
        "evidence": [{
            "field_name": "origin", "raw_text": "安溪", "normalized_value": "安溪",
            "model_confidence": 0.9, "information_status": "explicit",
            "source_type": "product-claim", "verification_status": "unverified",
            "source_location": "label.origin", "evidence_strength": "high",
        }],
    }


@dataclass
class _Repository:
    job_id: UUID
    image_id: UUID
    claimed: bool = False
    completed: int = 0
    failed: list[ErrorCode] | None = None

    async def claim_job(self, *, job_id: UUID) -> bool:
        if job_id != self.job_id or self.claimed:
            return False
        self.claimed = True
        return True

    async def get_claimed_job(self, *, job_id: UUID) -> StoredJob:
        return StoredJob(job_id, uuid4(), self.image_id, JobState.PROCESSING, 1,
                         ProcessingMode.FAKE_PROVIDER, datetime.now(timezone.utc), datetime.now(timezone.utc))

    async def complete_extraction_job(self, **kwargs: object) -> None:
        assert kwargs["temporary_image_deleted"] is True
        self.completed += 1

    async def fail_extraction_job(self, **kwargs: object) -> None:
        self.failed = [kwargs["error_code"]]  # type: ignore[list-item]


class _BrokenDeleteStorage(InMemoryTemporaryPrivateStorage):
    async def delete(self, *, object_key: str) -> None:
        raise OSError(object_key)


class _BlockingDeleteStorage(InMemoryTemporaryPrivateStorage):
    def __init__(self) -> None:
        super().__init__()
        self.delete_started = asyncio.Event()
        self.allow_delete = asyncio.Event()

    async def delete(self, *, object_key: str) -> None:
        self.delete_started.set()
        await self.allow_delete.wait()
        await super().delete(object_key=object_key)


class _SlowProvider:
    async def extract(self, *, image_object_key: str) -> dict[str, object]:
        del image_object_key
        await asyncio.sleep(1)
        return _payload()

    async def repair_structure(self, *, invalid_response: dict[str, object]) -> dict[str, object]:
        return invalid_response


async def _storage_with_job(job_id: UUID, cls: type[InMemoryTemporaryPrivateStorage] = InMemoryTemporaryPrivateStorage) -> InMemoryTemporaryPrivateStorage:
    storage = cls()
    await storage.put_private(object_key=f"temporary/{job_id}", content_type="image/png", data=b"x")
    return storage


async def test_duplicate_worker_does_not_call_provider_twice() -> None:
    job_id = uuid4()
    repository = _Repository(job_id, uuid4())
    provider = FakeProvider(extraction_response=_payload())
    storage = await _storage_with_job(job_id)
    runner = FakeExtractionJobRunner(repository, provider, storage)  # type: ignore[arg-type]
    await runner.run(job_id=job_id)
    await runner.run(job_id=job_id)
    assert provider.extraction_calls == 1
    assert repository.completed == 1
    assert storage.objects == {}


async def test_runner_marks_timeout_only_after_cleanup() -> None:
    job_id = uuid4()
    repository = _Repository(job_id, uuid4())
    storage = await _storage_with_job(job_id)
    await FakeExtractionJobRunner(repository, _SlowProvider(), storage, timeout_seconds=0.001).run(job_id=job_id)  # type: ignore[arg-type]
    assert repository.failed == [ErrorCode.AI_TIMEOUT]
    assert storage.objects == {}


async def test_runner_marks_network_exhaustion_and_schema_failure() -> None:
    job_id = uuid4()
    network_repo = _Repository(job_id, uuid4())
    network_storage = await _storage_with_job(job_id)
    await FakeExtractionJobRunner(network_repo, FakeProvider(extraction_response=_payload(), network_failures_before_success=2), network_storage).run(job_id=job_id)  # type: ignore[arg-type]
    assert network_repo.failed == [ErrorCode.AI_PROVIDER_ERROR]

    schema_job_id = uuid4()
    schema_repo = _Repository(schema_job_id, uuid4())
    schema_storage = await _storage_with_job(schema_job_id)
    await FakeExtractionJobRunner(schema_repo, FakeProvider(extraction_response={"bad": True}, repair_response={"still": "bad"}), schema_storage).run(job_id=schema_job_id)  # type: ignore[arg-type]
    assert schema_repo.failed == [ErrorCode.AI_SCHEMA_INVALID]


async def test_cancellation_marks_worker_interrupted_and_reraises() -> None:
    job_id = uuid4()
    repository = _Repository(job_id, uuid4())
    storage = await _storage_with_job(job_id)
    task = asyncio.create_task(FakeExtractionJobRunner(repository, _SlowProvider(), storage).run(job_id=job_id))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repository.failed == [ErrorCode.WORKER_INTERRUPTED]
    assert storage.objects == {}


async def test_cleanup_failure_never_completes_or_records_success() -> None:
    job_id = uuid4()
    repository = _Repository(job_id, uuid4())
    storage = await _storage_with_job(job_id, _BrokenDeleteStorage)
    await FakeExtractionJobRunner(repository, FakeProvider(extraction_response=_payload()), storage).run(job_id=job_id)  # type: ignore[arg-type]
    assert repository.completed == 0
    assert repository.failed == [ErrorCode.TEMPORARY_IMAGE_CLEANUP_FAILED]


async def test_cancellation_during_storage_delete_waits_for_cleanup_and_terminal_state() -> None:
    job_id = uuid4()
    repository = _Repository(job_id, uuid4())
    storage = await _storage_with_job(job_id, _BlockingDeleteStorage)
    runner_task = asyncio.create_task(
        FakeExtractionJobRunner(repository, FakeProvider(extraction_response=_payload()), storage).run(job_id=job_id)  # type: ignore[arg-type]
    )
    await asyncio.wait_for(storage.delete_started.wait(), timeout=0.1)
    runner_task.cancel()
    storage.allow_delete.set()
    with pytest.raises(asyncio.CancelledError):
        await runner_task
    assert storage.objects == {}
    assert repository.failed == [ErrorCode.WORKER_INTERRUPTED]
    assert repository.completed == 0


async def test_completion_persistence_error_marks_job_failed_after_cleanup() -> None:
    class _FailingCompletionRepository(_Repository):
        async def complete_extraction_job(self, **kwargs: object) -> None:
            raise RuntimeError("database unavailable")

    job_id = uuid4()
    repository = _FailingCompletionRepository(job_id, uuid4())
    storage = await _storage_with_job(job_id)
    await FakeExtractionJobRunner(repository, FakeProvider(extraction_response=_payload()), storage).run(job_id=job_id)  # type: ignore[arg-type]
    assert storage.objects == {}
    assert repository.failed == [ErrorCode.WORKER_INTERRUPTED]
    assert repository.completed == 0
