from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx

from guancha_api.application.task_runners import TaskEnqueueError
from guancha_api.auth.cloudbase import DEFAULT_CLOUDBASE_REGION, cloudbase_gateway_origin


DEFAULT_HANDOFF_FUNCTION_NAME = "guancha-extraction-handoff"


class CloudBaseHandoffDispatcher:
    """Request-scoped CloudBase Function HTTP dispatcher.

    The supplied access token is used only as the caller Authorization header.
    The function receives the persisted job identity as its entire body; no
    Tencent SDK or platform credential is needed in the Run process.
    """

    def __init__(
        self,
        *,
        env_id: str,
        region: str = DEFAULT_CLOUDBASE_REGION,
        access_token: str,
        function_name: str = DEFAULT_HANDOFF_FUNCTION_NAME,
        timeout_seconds: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        token = access_token.strip()
        name = function_name.strip()
        if not token:
            raise TaskEnqueueError(
                "CloudBase handoff requires the authenticated request token"
            )
        if not name:
            raise TaskEnqueueError("CloudBase handoff function name is required")
        try:
            origin = cloudbase_gateway_origin(env_id=env_id, region=region)
        except ValueError:
            raise TaskEnqueueError(
                "CloudBase handoff requires a valid environment and region"
            ) from None
        if timeout_seconds <= 0:
            raise TaskEnqueueError("CloudBase handoff timeout must be positive")
        self._origin = origin
        self._access_token = token
        self.function_name = name
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    @classmethod
    def from_environment(
        cls,
        *,
        access_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "CloudBaseHandoffDispatcher":
        env_id = os.getenv("CLOUDBASE_ENV_ID", "").strip()
        region = os.getenv("CLOUDBASE_REGION", DEFAULT_CLOUDBASE_REGION).strip()
        function_name = os.getenv(
            "GUANCHA_EXTRACTION_HANDOFF_FUNCTION_NAME",
            DEFAULT_HANDOFF_FUNCTION_NAME,
        ).strip()
        raw_timeout = os.getenv(
            "GUANCHA_EXTRACTION_HANDOFF_TIMEOUT_SECONDS", "8"
        ).strip()
        try:
            timeout = float(raw_timeout)
        except ValueError:
            raise TaskEnqueueError(
                "GUANCHA_EXTRACTION_HANDOFF_TIMEOUT_SECONDS must be numeric"
            ) from None
        return cls(
            env_id=env_id,
            region=region,
            access_token=access_token,
            function_name=function_name,
            timeout_seconds=timeout,
            transport=transport,
        )

    @property
    def origin(self) -> str:
        return self._origin

    async def enqueue(
        self,
        *,
        job_id: UUID,
        task: Callable[[], Awaitable[None]],
    ) -> bool:
        del task
        try:
            normalized_job_id = UUID(str(job_id))
        except (AttributeError, TypeError, ValueError):
            raise TaskEnqueueError(
                "CloudBase handoff requires a UUID job id"
            ) from None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._origin}/v1/functions/{self.function_name}",
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json={"job_id": str(normalized_job_id)},
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise TaskEnqueueError(
                "CloudBase handoff invocation was rejected"
            ) from None
        if response.status_code < 200 or response.status_code >= 300:
            raise TaskEnqueueError(
                "CloudBase handoff invocation was rejected"
            )
        return True

    async def shutdown(self) -> None:
        """No client is kept beyond the request-scoped enqueue operation."""


__all__ = ["CloudBaseHandoffDispatcher", "DEFAULT_HANDOFF_FUNCTION_NAME"]
