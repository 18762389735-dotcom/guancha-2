"""SCF Event entrypoint for one persisted extraction Job identity.

The Event contract is deliberately narrow: the worker loads its database,
private-object storage, and MiMo configuration from its own environment. It
reuses the same claimed-job runner as in-process extraction, so the database
claim remains the duplicate-event authority.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

from guancha_api.application.job_runner import FakeExtractionJobRunner
from guancha_api.infrastructure.storage.factory import temporary_private_storage_from_environment
from guancha_api.infrastructure.storage.interfaces import TemporaryPrivateStorage
from guancha_api.providers.execution import StructuredVisionProvider
from guancha_api.providers.mimo import DEFAULT_MIMO_BASE_URL, MiMoVisionProvider
from guancha_api.repositories.postgres import PostgresPhase2Repository


DEFAULT_WORKER_TIMEOUT_SECONDS = 170.0
logger = logging.getLogger(__name__)


class WorkerEventError(ValueError):
    """The function event is not the narrow extraction contract."""


def validate_worker_event(event: object) -> UUID:
    if not isinstance(event, Mapping) or set(event) != {"job_id"}:
        raise WorkerEventError("worker event must contain only job_id")
    raw_job_id = event.get("job_id")
    if not isinstance(raw_job_id, str):
        raise WorkerEventError("worker job_id must be a UUID string")
    try:
        return UUID(raw_job_id)
    except ValueError:
        raise WorkerEventError("worker job_id must be a UUID string") from None


async def _default_repository_factory() -> PostgresPhase2Repository:
    database_url = os.getenv("GUANCHA_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("GUANCHA_DATABASE_URL is required by the extraction worker")
    return await PostgresPhase2Repository.connect(database_url)


def _default_storage_factory() -> TemporaryPrivateStorage:
    return temporary_private_storage_from_environment()


def _default_provider_factory(storage: TemporaryPrivateStorage) -> StructuredVisionProvider:
    if os.getenv("GUANCHA_PROVIDER", "").strip().lower() != "mimo":
        raise RuntimeError("Cloud Function extraction requires GUANCHA_PROVIDER=mimo")
    api_key = os.getenv("MIMO_API_KEY", "").strip()
    model = os.getenv("GUANCHA_MIMO_MODEL", "").strip()
    if not api_key or not model:
        raise RuntimeError(
            "Cloud Function extraction requires MIMO_API_KEY and GUANCHA_MIMO_MODEL"
        )
    return MiMoVisionProvider(
        api_key=api_key,
        model=model,
        storage=storage,
        base_url=os.getenv("MIMO_BASE_URL", DEFAULT_MIMO_BASE_URL),
    )


class CloudFunctionExtractionWorker:
    """Run the established claimed-job extraction flow in an SCF process."""

    def __init__(
        self,
        *,
        repository_factory: Callable[[], Awaitable[PostgresPhase2Repository]] = _default_repository_factory,
        storage_factory: Callable[[], TemporaryPrivateStorage] = _default_storage_factory,
        provider_factory: Callable[[TemporaryPrivateStorage], StructuredVisionProvider] = _default_provider_factory,
        runner_factory: Callable[..., FakeExtractionJobRunner] = FakeExtractionJobRunner,
        timeout_seconds: float = DEFAULT_WORKER_TIMEOUT_SECONDS,
    ) -> None:
        self.repository_factory = repository_factory
        self.storage_factory = storage_factory
        self.provider_factory = provider_factory
        self.runner_factory = runner_factory
        self.timeout_seconds = timeout_seconds

    async def run(self, event: object) -> dict[str, str]:
        job_id = validate_worker_event(event)
        repository = await self.repository_factory()
        claimed = False
        try:
            if not await repository.claim_job(job_id=job_id):
                return {"job_id": str(job_id), "status": "duplicate"}
            claimed = True
            storage = self.storage_factory()
            provider = self.provider_factory(storage)
            runner = self.runner_factory(
                repository, provider, storage, timeout_seconds=self.timeout_seconds
            )
            await runner.run(job_id=job_id, already_claimed=True)
            return {"job_id": str(job_id), "status": "handled"}
        except asyncio.CancelledError:
            if claimed:
                await self._fail_claimed_job(repository=repository, job_id=job_id)
            raise
        except Exception:
            if claimed:
                await self._fail_claimed_job(repository=repository, job_id=job_id)
            return {
                "job_id": str(job_id),
                "status": "failed",
                "error_code": "worker_interrupted",
            }
        finally:
            await repository.close()

    @staticmethod
    async def _fail_claimed_job(
        *, repository: PostgresPhase2Repository, job_id: UUID
    ) -> None:
        """Best-effort conditional terminalization without exception details."""

        from guancha_api.schemas.contracts import ErrorCode

        try:
            await asyncio.shield(
                repository.fail_extraction_job(
                    job_id=job_id, error_code=ErrorCode.WORKER_INTERRUPTED
                )
            )
        except Exception:
            logger.warning("async extraction worker could not persist terminal state")


async def run_worker_event(event: object) -> dict[str, str]:
    return await CloudFunctionExtractionWorker().run(event)


def main_handler(event: object, context: Any = None) -> dict[str, str]:
    """SCF ordinary Event Function handler; context is intentionally unused."""

    del context
    return asyncio.run(run_worker_event(event))


__all__ = [
    "CloudFunctionExtractionWorker",
    "DEFAULT_WORKER_TIMEOUT_SECONDS",
    "WorkerEventError",
    "main_handler",
    "run_worker_event",
    "validate_worker_event",
]
