from __future__ import annotations

import os

from .interfaces import TemporaryPrivateStorage
from .memory import InMemoryTemporaryPrivateStorage


def temporary_private_storage_from_environment() -> TemporaryPrivateStorage:
    backend = os.getenv("GUANCHA_PRIVATE_STORAGE_BACKEND", "memory").strip().lower()
    if backend in {"", "memory"}:
        return InMemoryTemporaryPrivateStorage()
    if backend == "cos":
        from .cos import CosSharedPrivateStorage

        return CosSharedPrivateStorage.from_environment()
    from .cos import StorageConfigurationError

    raise StorageConfigurationError(
        "GUANCHA_PRIVATE_STORAGE_BACKEND must be memory or cos"
    )


__all__ = ["temporary_private_storage_from_environment"]
