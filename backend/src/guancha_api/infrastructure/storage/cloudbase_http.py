from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from guancha_api.auth.cloudbase import DEFAULT_CLOUDBASE_REGION, cloudbase_gateway_origin

from .cos import StorageConfigurationError, _normalize_path
from .interfaces import SharedPrivateStorageError, TemporaryImageObject, TemporaryPrivateStorage


class CloudBaseHttpTemporaryPrivateStorage(TemporaryPrivateStorage):
    """Request-scoped adapter for classic CloudBase Storage HTTP APIs.

    The bearer token is deliberately supplied by the authenticated request and
    retained only by this adapter instance.  The application factory never
    constructs this class, so an app can start in this mode without a token.
    Signed upload/download values are consumed inside the operation and are
    never returned as a business object or included in an exception.
    """

    def __init__(
        self,
        *,
        env_id: str,
        region: str = DEFAULT_CLOUDBASE_REGION,
        access_token: str,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_token = access_token.strip()
        if not normalized_token:
            raise StorageConfigurationError(
                "CloudBase HTTP storage requires the authenticated request token"
            )
        try:
            self._origin = cloudbase_gateway_origin(env_id=env_id, region=region)
        except ValueError as exc:
            raise StorageConfigurationError(
                "CloudBase HTTP storage requires a valid environment and region"
            ) from exc
        if timeout_seconds <= 0:
            raise StorageConfigurationError(
                "CloudBase HTTP storage timeout must be positive"
            )
        self._env_id = env_id.strip()
        self._access_token = normalized_token
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    @classmethod
    def from_environment(
        cls,
        *,
        access_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "CloudBaseHttpTemporaryPrivateStorage":
        env_id = os.getenv("CLOUDBASE_ENV_ID", "").strip()
        region = os.getenv(
            "CLOUDBASE_REGION", DEFAULT_CLOUDBASE_REGION
        ).strip()
        raw_timeout = os.getenv(
            "GUANCHA_PRIVATE_STORAGE_HTTP_TIMEOUT_SECONDS", "15"
        ).strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise StorageConfigurationError(
                "GUANCHA_PRIVATE_STORAGE_HTTP_TIMEOUT_SECONDS must be numeric"
            ) from exc
        return cls(
            env_id=env_id,
            region=region,
            access_token=access_token,
            timeout_seconds=timeout,
            transport=transport,
        )

    @property
    def origin(self) -> str:
        return self._origin

    @staticmethod
    def _logical_key(object_key: str) -> str:
        try:
            return _normalize_path(object_key, label="logical object key")
        except StorageConfigurationError:
            raise

    def _cloud_object_id(self, object_key: str) -> str:
        return f"cloud://{self._env_id}.bucket/{self._logical_key(object_key)}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: object) -> object:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._origin}{path}",
                    headers=self._headers(),
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise SharedPrivateStorageError(
                "CloudBase private storage request failed"
            ) from None
        if response.status_code == 404:
            raise KeyError("cloudbase-object")
        if response.status_code < 200 or response.status_code >= 300:
            raise SharedPrivateStorageError(
                "CloudBase private storage request failed"
            )
        try:
            return response.json()
        except ValueError:
            raise SharedPrivateStorageError(
                "CloudBase private storage response was invalid"
            ) from None

    @staticmethod
    def _first_item(payload: object) -> Mapping[str, Any]:
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], Mapping):
            raise SharedPrivateStorageError(
                "CloudBase private storage response was invalid"
            )
        item = payload[0]
        code = item.get("code")
        if isinstance(code, str):
            if code in {"OBJECT_NOT_EXIST", "NOT_FOUND"}:
                raise KeyError("cloudbase-object")
            raise SharedPrivateStorageError(
                "CloudBase private storage operation was rejected"
            )
        return item

    async def put_private(
        self, *, object_key: str, content_type: str, data: bytes
    ) -> TemporaryImageObject:
        logical_key = self._logical_key(object_key)
        try:
            item = self._first_item(
                await self._post(
                    "/v1/storages/get-objects-upload-info",
                    [{"objectId": logical_key}],
                )
            )
            upload_url = item.get("uploadUrl")
            authorization = item.get("authorization")
            token = item.get("token")
            cloud_object_meta = item.get("cloudObjectMeta")
            if not all(
                isinstance(value, str) and value
                for value in (upload_url, authorization, token, cloud_object_meta)
            ):
                raise SharedPrivateStorageError(
                    "CloudBase private storage upload metadata was invalid"
                )
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.put(
                    upload_url,
                    headers={
                        "Authorization": authorization,
                        "X-Cos-Security-Token": token,
                        "X-Cos-Meta-Fileid": cloud_object_meta,
                        "Content-Type": content_type,
                    },
                    content=data,
                )
            if response.status_code < 200 or response.status_code >= 300:
                raise SharedPrivateStorageError(
                    "CloudBase private storage upload failed"
                )
        except KeyError:
            raise
        except SharedPrivateStorageError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise SharedPrivateStorageError(
                "CloudBase private storage upload failed"
            ) from None
        return TemporaryImageObject(
            object_key=object_key,
            content_type=content_type,
            size_bytes=len(data),
        )

    async def read_private(self, *, object_key: str) -> bytes:
        cloud_object_id = self._cloud_object_id(object_key)
        try:
            item = self._first_item(
                await self._post(
                    "/v1/storages/get-objects-download-info",
                    [{"cloudObjectId": cloud_object_id}],
                )
            )
            download_url = item.get("downloadUrl")
            if not isinstance(download_url, str) or not download_url:
                raise SharedPrivateStorageError(
                    "CloudBase private storage download metadata was invalid"
                )
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(download_url)
            if response.status_code == 404:
                raise KeyError(object_key)
            if response.status_code < 200 or response.status_code >= 300:
                raise SharedPrivateStorageError(
                    "CloudBase private storage download failed"
                )
            return bytes(response.content)
        except KeyError:
            raise KeyError(object_key) from None
        except SharedPrivateStorageError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise SharedPrivateStorageError(
                "CloudBase private storage download failed"
            ) from None

    async def delete(self, *, object_key: str) -> None:
        try:
            payload = await self._post(
                "/v1/storages/delete-objects",
                [{"cloudObjectId": self._cloud_object_id(object_key)}],
            )
            try:
                self._first_item(payload)
            except KeyError:
                return
        except KeyError:
            return
        except SharedPrivateStorageError:
            raise


__all__ = ["CloudBaseHttpTemporaryPrivateStorage"]
