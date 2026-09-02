from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Protocol

from .interfaces import (
    SharedPrivateStorageError,
    TemporaryImageObject,
    TemporaryPrivateStorage,
)


class StorageConfigurationError(ValueError):
    """The explicitly selected shared-storage backend is not configured safely."""


class _CosObjectClient(Protocol):
    def put_object(self, *, Bucket: str, Body: bytes, Key: str, **kwargs: Any) -> Any: ...

    def get_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]: ...

    def delete_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class CosPrivateStorageSettings:
    bucket: str
    region: str
    prefix: str
    timeout_seconds: float = 30.0
    retries: int = 3

    @classmethod
    def from_environment(cls) -> "CosPrivateStorageSettings":
        bucket = os.getenv("GUANCHA_PRIVATE_STORAGE_COS_BUCKET", "").strip()
        region = os.getenv("GUANCHA_PRIVATE_STORAGE_COS_REGION", "").strip()
        prefix = os.getenv("GUANCHA_PRIVATE_STORAGE_COS_PREFIX", "").strip()
        if not bucket or not region or not prefix:
            raise StorageConfigurationError(
                "COS private storage requires bucket, region, and prefix"
            )
        timeout_seconds = _positive_float(
            "GUANCHA_PRIVATE_STORAGE_COS_TIMEOUT_SECONDS", default=30.0
        )
        retries = _non_negative_int("GUANCHA_PRIVATE_STORAGE_COS_RETRIES", default=3)
        return cls(
            bucket=bucket,
            region=region,
            prefix=_normalize_path(prefix, label="COS prefix"),
            timeout_seconds=timeout_seconds,
            retries=retries,
        )


class CosSharedPrivateStorage(TemporaryPrivateStorage):
    """Thin async facade over Tencent's synchronous COS XML SDK.

    The business layer continues to exchange logical keys such as
    ``temporary/<image_id>``.  The configured prefix is applied only inside
    this adapter, keeping bucket and physical-key details out of the domain.
    """

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        prefix: str,
        client: _CosObjectClient,
        timeout_seconds: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.prefix = _normalize_path(prefix, label="COS prefix")
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._retries = retries

    @classmethod
    def from_environment(cls) -> "CosSharedPrivateStorage":
        settings = CosPrivateStorageSettings.from_environment()
        secret_id = os.getenv("TENCENTCLOUD_SECRETID", "").strip()
        secret_key = os.getenv("TENCENTCLOUD_SECRETKEY", "").strip()
        session_token = os.getenv("TENCENTCLOUD_SESSIONTOKEN", "").strip() or None
        if not secret_id or not secret_key:
            raise StorageConfigurationError(
                "COS private storage requires standard Tencent Cloud credentials"
            )
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError as exc:
            raise StorageConfigurationError(
                "Install the cloud-storage dependency to enable COS private storage"
            ) from exc
        config = CosConfig(
            Region=settings.region,
            SecretId=secret_id,
            SecretKey=secret_key,
            Token=session_token,
            Timeout=settings.timeout_seconds,
        )
        return cls(
            bucket=settings.bucket,
            region=settings.region,
            prefix=settings.prefix,
            client=CosS3Client(config, retry=settings.retries),
            timeout_seconds=settings.timeout_seconds,
            retries=settings.retries,
        )

    def _physical_key(self, object_key: str) -> str:
        logical_key = _normalize_path(object_key, label="logical object key")
        return f"{self.prefix}/{logical_key}"

    async def put_private(
        self, *, object_key: str, content_type: str, data: bytes
    ) -> TemporaryImageObject:
        physical_key = self._physical_key(object_key)
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self.bucket,
                Body=data,
                Key=physical_key,
                ContentType=content_type,
                ACL="private",
            )
        except Exception as exc:
            raise _map_cos_error(exc, object_key=object_key) from exc
        return TemporaryImageObject(
            object_key=object_key,
            content_type=content_type,
            size_bytes=len(data),
        )

    async def read_private(self, *, object_key: str) -> bytes:
        physical_key = self._physical_key(object_key)
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self.bucket,
                Key=physical_key,
            )
            body = response.get("Body")
            if body is None or not hasattr(body, "get_raw_stream"):
                raise SharedPrivateStorageError("COS object response had no readable body")
            stream = body.get_raw_stream()
            try:
                data = stream.read()
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            if not isinstance(data, bytes):
                raise SharedPrivateStorageError("COS object response was not bytes")
            return data
        except Exception as exc:
            raise _map_cos_error(exc, object_key=object_key) from exc

    async def delete(self, *, object_key: str) -> None:
        physical_key = self._physical_key(object_key)
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self.bucket,
                Key=physical_key,
            )
        except Exception as exc:
            mapped = _map_cos_error(exc, object_key=object_key)
            if isinstance(mapped, KeyError):
                return
            raise mapped from exc


def _normalize_path(value: str, *, label: str) -> str:
    normalized = value.strip().strip("/")
    parts = normalized.split("/") if normalized else []
    if not normalized or "\\" in value or any(part in {"", ".", ".."} for part in parts):
        raise StorageConfigurationError(f"{label} must be a relative POSIX path")
    return normalized


def _positive_float(name: str, *, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise StorageConfigurationError(f"{name} must be numeric") from exc
    if value <= 0:
        raise StorageConfigurationError(f"{name} must be positive")
    return value


def _non_negative_int(name: str, *, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise StorageConfigurationError(f"{name} must be an integer") from exc
    if value < 0:
        raise StorageConfigurationError(f"{name} must not be negative")
    return value


def _map_cos_error(exc: Exception, *, object_key: str) -> Exception:
    if isinstance(exc, KeyError):
        return KeyError(object_key)
    status = _cos_error_value(exc, "get_status_code")
    code = _cos_error_value(exc, "get_error_code")
    if status == 404 or code in {"NoSuchKey", "NoSuchResource", "NotFound"}:
        return KeyError(object_key)
    if isinstance(exc, SharedPrivateStorageError):
        return exc
    return SharedPrivateStorageError("shared private storage operation failed")


def _cos_error_value(exc: Exception, method_name: str) -> Any:
    method = getattr(exc, method_name, None)
    if not callable(method):
        return None
    try:
        return method()
    except Exception:
        return None


__all__ = [
    "CosPrivateStorageSettings",
    "CosSharedPrivateStorage",
    "StorageConfigurationError",
]
