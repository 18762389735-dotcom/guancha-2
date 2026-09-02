from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol


class TaskEnqueueError(RuntimeError):
    """A task could not be accepted after its queued Job was persisted."""


class TaskRunner(Protocol):
    """Minimal boundary shared by local and Cloud Function job dispatchers."""

    async def enqueue(self, *, job_id: object, task: Callable[[], Awaitable[None]]) -> bool: ...

    async def shutdown(self) -> None: ...


class InProcessTaskRunner:
    """Run accepted work to completion before returning to the request."""

    def __init__(self) -> None:
        self._active_job_ids: set[object] = set()

    @property
    def active_count(self) -> int:
        return len(self._active_job_ids)

    async def enqueue(self, *, job_id: object, task: Callable[[], Awaitable[None]]) -> bool:
        if job_id in self._active_job_ids:
            return False
        self._active_job_ids.add(job_id)
        try:
            await task()
        finally:
            self._active_job_ids.discard(job_id)
        return True

    async def shutdown(self) -> None:
        """Request-bound work is already complete when shutdown is reached."""


class ManualTaskRunner:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, Callable[[], Awaitable[None]]]] = []
        self._job_ids: set[object] = set()

    @property
    def pending_count(self) -> int:
        return len(self.tasks)

    async def enqueue(self, *, job_id: object, task: Callable[[], Awaitable[None]]) -> bool:
        if job_id in self._job_ids:
            return False
        self._job_ids.add(job_id)
        self.tasks.append((job_id, task))
        return True

    async def run_next(self) -> bool:
        if not self.tasks:
            return False
        job_id, task = self.tasks.pop(0)
        try:
            await task()
        finally:
            self._job_ids.discard(job_id)
        return True

    async def drain(self) -> int:
        completed = 0
        while await self.run_next():
            completed += 1
        return completed

    async def shutdown(self) -> None:
        """Lifecycle-compatible no-op for deterministic tests."""
        self.tasks.clear()
        self._job_ids.clear()
