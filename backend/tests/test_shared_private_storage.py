from __future__ import annotations

import io

import pytest

_cos_exception = pytest.importorskip("qcloud_cos.cos_exception")
CosServiceError = _cos_exception.CosServiceError

from guancha_api.infrastructure.storage import (
    CosSharedPrivateStorage,
    InMemoryTemporaryPrivateStorage,
    SharedPrivateStorageError,
    StorageConfigurationError,
    temporary_private_storage_from_environment,
)


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def get_raw_stream(self) -> io.BytesIO:
        return io.BytesIO(self._data)


class _SharedFakeCosClient:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = objects
        self.calls: list[tuple[str, dict[str, object]]] = []

    def put_object(self, **kwargs: object) -> None:
        self.calls.append(("put", kwargs))
        self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))] = bytes(kwargs["Body"])  # type: ignore[arg-type]

    def get_object(self, **kwargs: object) -> dict[str, _Body]:
        self.calls.append(("get", kwargs))
        key = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if key not in self.objects:
            raise CosServiceError("get_object", "missing", 404)
        return {"Body": _Body(self.objects[key])}

    def delete_object(self, **kwargs: object) -> None:
        self.calls.append(("delete", kwargs))
        self.objects.pop((str(kwargs["Bucket"]), str(kwargs["Key"])), None)


class _FailingCosClient:
    def put_object(self, **kwargs: object) -> None:
        raise CosServiceError("put_object", "permission denied", 403)

    def get_object(self, **kwargs: object) -> dict[str, _Body]:
        raise CosServiceError("get_object", "permission denied", 403)

    def delete_object(self, **kwargs: object) -> None:
        raise CosServiceError("delete_object", "permission denied", 403)


def _storage(client: object, *, prefix: str = "gate3b-poc/run-1") -> CosSharedPrivateStorage:
    return CosSharedPrivateStorage(
        bucket="isolated-test-bucket",
        region="ap-shanghai",
        prefix=prefix,
        client=client,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_put_read_delete_and_private_acl() -> None:
    objects: dict[tuple[str, str], bytes] = {}
    client = _SharedFakeCosClient(objects)
    storage = _storage(client)
    payload = b"synthetic-gate3b-image"

    reference = await storage.put_private(
        object_key="temporary/image-a",
        content_type="image/png",
        data=payload,
    )

    assert reference.object_key == "temporary/image-a"
    assert reference.size_bytes == len(payload)
    assert await storage.read_private(object_key="temporary/image-a") == payload
    put_kwargs = client.calls[0][1]
    assert put_kwargs["ACL"] == "private"
    assert put_kwargs["ContentType"] == "image/png"
    assert "URL" not in put_kwargs
    assert not hasattr(storage, "get_public_url")

    await storage.delete(object_key="temporary/image-a")
    with pytest.raises(KeyError):
        await storage.read_private(object_key="temporary/image-a")


@pytest.mark.asyncio
async def test_distinct_keys_prefix_isolation_and_independent_instances() -> None:
    objects: dict[tuple[str, str], bytes] = {}
    writer = _storage(_SharedFakeCosClient(objects), prefix="gate3b-poc/run-a")
    reader = _storage(_SharedFakeCosClient(objects), prefix="gate3b-poc/run-a")
    other_namespace = _storage(_SharedFakeCosClient(objects), prefix="gate3b-poc/run-b")

    await writer.put_private(object_key="temporary/a", content_type="image/png", data=b"A")
    await writer.put_private(object_key="temporary/b", content_type="image/png", data=b"B")

    assert await reader.read_private(object_key="temporary/a") == b"A"
    assert await reader.read_private(object_key="temporary/b") == b"B"
    with pytest.raises(KeyError):
        await other_namespace.read_private(object_key="temporary/a")


@pytest.mark.asyncio
async def test_provider_error_maps_without_leaking_provider_details() -> None:
    storage = _storage(_FailingCosClient())

    with pytest.raises(SharedPrivateStorageError) as error:
        await storage.put_private(
            object_key="temporary/failure",
            content_type="image/jpeg",
            data=b"x",
        )

    assert str(error.value) == "shared private storage operation failed"
    assert "permission" not in str(error.value)


def test_memory_backend_is_the_explicitly_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUANCHA_PRIVATE_STORAGE_BACKEND", raising=False)
    assert isinstance(temporary_private_storage_from_environment(), InMemoryTemporaryPrivateStorage)

    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_BACKEND", "memory")
    assert isinstance(temporary_private_storage_from_environment(), InMemoryTemporaryPrivateStorage)


def test_cos_backend_configuration_is_explicit_and_uses_standard_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qcloud_cos

    class _Config:
        pass

    created: dict[str, object] = {}

    def fake_config(**kwargs: object) -> _Config:
        created["config"] = kwargs
        return _Config()

    def fake_client(config: object, *, retry: int) -> object:
        created["client_config"] = config
        created["retry"] = retry
        return object()

    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_BACKEND", "cos")
    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_COS_BUCKET", "isolated-test-bucket")
    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_COS_REGION", "ap-shanghai")
    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_COS_PREFIX", "gate3b-poc/run-1")
    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_COS_RETRIES", "2")
    monkeypatch.setenv("TENCENTCLOUD_SECRETID", "test-only-id")
    monkeypatch.setenv("TENCENTCLOUD_SECRETKEY", "test-only-key")
    monkeypatch.setenv("TENCENTCLOUD_SESSIONTOKEN", "test-only-token")
    monkeypatch.setattr(qcloud_cos, "CosConfig", fake_config)
    monkeypatch.setattr(qcloud_cos, "CosS3Client", fake_client)

    storage = temporary_private_storage_from_environment()

    assert isinstance(storage, CosSharedPrivateStorage)
    assert created["config"] == {
        "Region": "ap-shanghai",
        "SecretId": "test-only-id",
        "SecretKey": "test-only-key",
        "Token": "test-only-token",
        "Timeout": 30.0,
    }
    assert created["retry"] == 2


def test_cos_backend_requires_explicit_complete_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_BACKEND", "cos")
    for name in (
        "GUANCHA_PRIVATE_STORAGE_COS_BUCKET",
        "GUANCHA_PRIVATE_STORAGE_COS_REGION",
        "GUANCHA_PRIVATE_STORAGE_COS_PREFIX",
        "TENCENTCLOUD_SECRETID",
        "TENCENTCLOUD_SECRETKEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(StorageConfigurationError):
        temporary_private_storage_from_environment()
