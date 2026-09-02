"""Small SCF Event dispatcher for the isolated Gate 3A proof of concept.

This module intentionally does not change the default application task runner.
The dispatcher is an opt-in TaskRunner-compatible adapter: it accepts a
persisted job identity and asks SCF to deliver that identity to a function. It
never executes the supplied local callback.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol
from uuid import UUID

from guancha_api.application.task_runners import TaskEnqueueError


DEFAULT_CLOUDBASE_REGION = "ap-shanghai"
DEFAULT_CLOUDFUNCTION_NAMESPACE = "default"
SCF_EVENT_INVOCATION_TYPE = "Event"


class ScfEventInvoker(Protocol):
    async def invoke(
        self,
        *,
        function_name: str,
        namespace: str,
        event: Mapping[str, str],
    ) -> None: ...


def _event_job_id(job_id: object) -> str:
    try:
        return str(UUID(str(job_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise TaskEnqueueError("Cloud Function dispatch requires a UUID job id") from None


def _runtime_credentials() -> tuple[str, str, str | None]:
    """Read platform-injected credentials without putting values in errors."""

    secret_id = os.getenv("TENCENTCLOUD_SECRETID", "").strip()
    secret_key = os.getenv("TENCENTCLOUD_SECRETKEY", "").strip()
    session_token = os.getenv("TENCENTCLOUD_SESSIONTOKEN", "").strip() or None
    if not secret_id or not secret_key:
        raise TaskEnqueueError(
            "Cloud Function dispatch requires runtime Tencent Cloud credentials"
        )
    return secret_id, secret_key, session_token


class TencentScfEventInvoker:
    """Use the Tencent Cloud SDK's asynchronous SCF Invoke API.

    The SDK client is synchronous, so its short control-plane request is run
    in an awaited thread. No business work is detached from the HTTP request.
    """

    def __init__(
        self,
        *,
        client: Any,
        request_factory: Callable[[], Any],
        region: str = DEFAULT_CLOUDBASE_REGION,
    ) -> None:
        self._client = client
        self._request_factory = request_factory
        self.region = region

    @classmethod
    def from_environment(
        cls, *, region: str = DEFAULT_CLOUDBASE_REGION
    ) -> "TencentScfEventInvoker":
        secret_id, secret_key, session_token = _runtime_credentials()
        try:
            from tencentcloud.common import credential
            from tencentcloud.scf.v20180416 import models, scf_client
        except ImportError:
            raise TaskEnqueueError(
                "Cloud Function dispatch requires the tencentcloud-sdk-python dependency"
            ) from None

        try:
            sdk_credential = credential.Credential(
                secret_id, secret_key, session_token
            )
            client = scf_client.ScfClient(sdk_credential, region)
        except Exception:
            raise TaskEnqueueError("Cloud Function SDK client configuration failed") from None
        return cls(client=client, request_factory=models.InvokeRequest, region=region)

    async def invoke(
        self,
        *,
        function_name: str,
        namespace: str,
        event: Mapping[str, str],
    ) -> None:
        if set(event) != {"job_id"} or not isinstance(event.get("job_id"), str):
            raise TaskEnqueueError("Cloud Function event must contain only job_id")

        request = self._request_factory()
        request.FunctionName = function_name
        request.InvocationType = SCF_EVENT_INVOCATION_TYPE
        request.Namespace = namespace
        request.ClientContext = json.dumps(
            dict(event), ensure_ascii=False, separators=(",", ":")
        )
        try:
            # Invoke is the SDK's asynchronous API. InvokeFunctionRequest is
            # the separate synchronous request model and has no InvocationType.
            await asyncio.to_thread(self._client.Invoke, request)
        except Exception:
            # Do not propagate SDK exception text: it may contain request
            # metadata or credential-adjacent details.
            raise TaskEnqueueError("Cloud Function Event invocation was rejected") from None


class CloudFunctionExtractionDispatcher:
    """TaskRunner-compatible opt-in dispatcher for extraction Job identities."""

    def __init__(
        self,
        *,
        invoker: ScfEventInvoker,
        function_name: str,
        namespace: str = DEFAULT_CLOUDFUNCTION_NAMESPACE,
    ) -> None:
        self.invoker = invoker
        self.function_name = function_name
        self.namespace = namespace

    @classmethod
    def from_environment(
        cls, *, region: str = DEFAULT_CLOUDBASE_REGION
    ) -> "CloudFunctionExtractionDispatcher":
        function_name = os.getenv("GUANCHA_EXTRACTION_FUNCTION_NAME", "").strip()
        namespace = os.getenv(
            "GUANCHA_EXTRACTION_FUNCTION_NAMESPACE", DEFAULT_CLOUDFUNCTION_NAMESPACE
        ).strip()
        if not function_name or not namespace:
            raise TaskEnqueueError(
                "Cloud Function dispatch requires an explicit function name and namespace"
            )
        return cls(
            invoker=TencentScfEventInvoker.from_environment(region=region),
            function_name=function_name,
            namespace=namespace,
        )

    async def enqueue(
        self,
        *,
        job_id: UUID,
        task: Callable[[], Awaitable[None]],
    ) -> bool:
        del task  # The worker, not this process, owns business execution.
        event = {"job_id": _event_job_id(job_id)}
        try:
            await self.invoker.invoke(
                function_name=self.function_name,
                namespace=self.namespace,
                event=event,
            )
        except TaskEnqueueError:
            raise
        except Exception:
            raise TaskEnqueueError("Cloud Function Event invocation was rejected") from None
        return True

    async def shutdown(self) -> None:
        """No local business task exists to shut down."""


__all__ = [
    "CloudFunctionExtractionDispatcher",
    "DEFAULT_CLOUDBASE_REGION",
    "DEFAULT_CLOUDFUNCTION_NAMESPACE",
    "SCF_EVENT_INVOCATION_TYPE",
    "TencentScfEventInvoker",
]
