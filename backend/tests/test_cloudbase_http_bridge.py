from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from guancha_api.infrastructure.storage.cloudbase_http import (
    CloudBaseHttpTemporaryPrivateStorage,
)
from guancha_api.infrastructure.storage.interfaces import SharedPrivateStorageError
from guancha_api.infrastructure.storage.factory import (
    temporary_private_storage_backend_from_environment,
    temporary_private_storage_from_environment,
)
from guancha_api.main import create_app
from guancha_api.tasks.cloudbase_handoff import CloudBaseHandoffDispatcher


def _storage_transport(seen: list[httpx.Request], payload: bytes = b"synthetic-png") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST" and request.url.path.endswith("get-objects-upload-info"):
            return httpx.Response(
                200,
                json=[
                    {
                        "uploadUrl": "https://upload.invalid/object",
                        "authorization": "temporary-upload-authorization",
                        "token": "temporary-upload-token",
                        "cloudObjectMeta": "temporary-file-id",
                    }
                ],
                request=request,
            )
        if request.method == "PUT":
            assert request.content == payload
            return httpx.Response(204, request=request)
        if request.method == "POST" and request.url.path.endswith("get-objects-download-info"):
            return httpx.Response(
                200,
                json=[{"downloadUrl": "https://download.invalid/object"}],
                request=request,
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                content=payload,
                headers={"content-type": "image/png"},
                request=request,
            )
        if request.method == "POST" and request.url.path.endswith("delete-objects"):
            return httpx.Response(200, json=[{}], request=request)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_cloudbase_http_storage_crud_uses_logical_key_and_bearer() -> None:
    seen: list[httpx.Request] = []
    token = "synthetic-user-access-token"
    storage = CloudBaseHttpTemporaryPrivateStorage(
        env_id="synthetic-env",
        region="ap-shanghai",
        access_token=token,
        transport=_storage_transport(seen),
    )
    object_key = f"temporary/{uuid4()}"

    stored = await storage.put_private(
        object_key=object_key,
        content_type="image/png",
        data=b"synthetic-png",
    )
    assert stored.object_key == object_key
    assert await storage.read_private(object_key=object_key) == b"synthetic-png"
    await storage.delete(object_key=object_key)

    gateway_requests = [request for request in seen if request.url.host != "upload.invalid" and request.url.host != "download.invalid"]
    assert [request.url.path for request in gateway_requests] == [
        "/v1/storages/get-objects-upload-info",
        "/v1/storages/get-objects-download-info",
        "/v1/storages/delete-objects",
    ]
    for request in gateway_requests:
        assert request.headers["authorization"] == f"Bearer {token}"
    assert json.loads(gateway_requests[0].content) == [{"objectId": object_key}]
    assert json.loads(gateway_requests[1].content) == [
        {"cloudObjectId": f"cloud://synthetic-env.bucket/{object_key}"}
    ]
    assert json.loads(gateway_requests[2].content) == [
        {"cloudObjectId": f"cloud://synthetic-env.bucket/{object_key}"}
    ]
    upload_request = next(request for request in seen if request.url.host == "upload.invalid")
    assert "Bearer" not in upload_request.headers.get("authorization", "")
    assert upload_request.headers["x-cos-security-token"] == "temporary-upload-token"
    assert not hasattr(stored, "public_url")


@pytest.mark.asyncio
async def test_cloudbase_http_storage_does_not_leak_token_in_errors() -> None:
    token = "synthetic-token-that-must-not-escape"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text=f"provider diagnostic contains {token}",
            request=request,
        )

    storage = CloudBaseHttpTemporaryPrivateStorage(
        env_id="synthetic-env",
        access_token=token,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SharedPrivateStorageError) as error:
        await storage.read_private(object_key=f"temporary/{uuid4()}")
    assert token not in str(error.value)


@pytest.mark.asyncio
async def test_cloudbase_http_storage_is_request_scoped_for_two_tokens() -> None:
    seen: list[httpx.Request] = []
    first = CloudBaseHttpTemporaryPrivateStorage(
        env_id="synthetic-env",
        access_token="token-a",
        transport=_storage_transport(seen),
    )
    second = CloudBaseHttpTemporaryPrivateStorage(
        env_id="synthetic-env",
        access_token="token-b",
        transport=_storage_transport(seen),
    )
    await first.read_private(object_key=f"temporary/{uuid4()}")
    await second.read_private(object_key=f"temporary/{uuid4()}")
    gateway_requests = [request for request in seen if request.method == "POST"]
    assert gateway_requests[0].headers["authorization"] == "Bearer token-a"
    assert gateway_requests[1].headers["authorization"] == "Bearer token-b"


def test_cloudbase_http_backend_is_not_eagerly_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_BACKEND", "cloudbase-http")
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "synthetic-env")
    assert temporary_private_storage_backend_from_environment() == "cloudbase-http"
    app = create_app()
    assert app.state.storage_backend == "cloudbase-http"
    assert app.state.temporary_storage is None
    with pytest.raises(ValueError, match="request-scoped"):
        temporary_private_storage_from_environment()


def test_cloudbase_handoff_requires_explicit_request_scoped_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUANCHA_PRIVATE_STORAGE_BACKEND", "cloudbase-http")
    monkeypatch.setenv("GUANCHA_EXTRACTION_EXECUTION", "cloudbase-handoff")
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "synthetic-env")
    app = create_app()
    assert app.state.extraction_execution == "cloudbase-handoff"
    assert app.state.extraction_task_runner is None


@pytest.mark.asyncio
async def test_cloudbase_handoff_sends_only_job_id_with_request_bearer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"status": "accepted"}, request=request)

    job_id = uuid4()
    dispatcher = CloudBaseHandoffDispatcher(
        env_id="synthetic-env",
        access_token="request-token",
        transport=httpx.MockTransport(handler),
    )
    accepted = await dispatcher.enqueue(job_id=job_id, task=lambda: pytest.fail("local callback must not run"))

    assert accepted is True
    assert len(seen) == 1
    request = seen[0]
    assert request.headers["authorization"] == "Bearer request-token"
    assert json.loads(request.content) == {"job_id": str(job_id)}
    assert set(json.loads(request.content)) == {"job_id"}
