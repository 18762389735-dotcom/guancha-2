from __future__ import annotations

import os

from .interfaces import TemporaryPrivateStorage
from .memory import InMemoryTemporaryPrivateStorage


SUPPORTED_STORAGE_BACKENDS = frozenset({"memory", "cos", "cloudbase-http"})


def temporary_private_storage_backend_from_environment() -> str:
    backend = os.getenv("GUANCHA_PRIVATE_STORAGE_BACKEND", "memory").strip().lower()
    if backend not in SUPPORTED_STORAGE_BACKENDS:
        raise ValueError(
            "GUANCHA_PRIVATE_STORAGE_BACKEND must be memory, cos, or cloudbase-http"
        )
    return backend


def temporary_private_storage_from_environment() -> TemporaryPrivateStorage:
    backend = temporary_private_storage_backend_from_environment()
    if backend in {"", "memory"}:
        return InMemoryTemporaryPrivateStorage()
    if backend == "cos":
        from .cos import CosSharedPrivateStorage

        return CosSharedPrivateStorage.from_environment()
    if backend == "cloudbase-http":
        from .cos import StorageConfigurationError

        raise StorageConfigurationError(
            "CloudBase HTTP storage is request-scoped and cannot be initialized at startup"
        )
    from .cos import StorageConfigurationError

    raise StorageConfigurationError(
        "GUANCHA_PRIVATE_STORAGE_BACKEND must be memory, cos, or cloudbase-http"
    )


def cloudbase_http_storage_from_environment(
    *, access_token: str, transport: object | None = None
) -> TemporaryPrivateStorage:
    from .cloudbase_http import CloudBaseHttpTemporaryPrivateStorage

    return CloudBaseHttpTemporaryPrivateStorage.from_environment(
        access_token=access_token,
        transport=transport,  # type: ignore[arg-type]
    )


__all__ = [
    "SUPPORTED_STORAGE_BACKENDS",
    "cloudbase_http_storage_from_environment",
    "temporary_private_storage_backend_from_environment",
    "temporary_private_storage_from_environment",
]
