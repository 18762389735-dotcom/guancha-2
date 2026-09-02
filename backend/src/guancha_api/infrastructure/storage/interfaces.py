from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TemporaryImageCleanupError(RuntimeError):
    """A private-object delete failed after an upload operation needed cleanup."""


class SharedPrivateStorageError(RuntimeError):
    """A shared private-object operation failed without exposing provider details."""


@dataclass(frozen=True, slots=True)
class TemporaryImageObject:
    """Private object reference. It is intentionally not a public URL."""

    object_key: str
    content_type: str
    size_bytes: int


class TemporaryPrivateStorage(Protocol):
    """Port for short-lived, private image objects only."""

    async def put_private(
        self, *, object_key: str, content_type: str, data: bytes
    ) -> TemporaryImageObject: ...

    async def delete(self, *, object_key: str) -> None: ...

    async def read_private(self, *, object_key: str) -> bytes: ...
