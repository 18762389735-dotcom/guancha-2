from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from guancha_api.application.task_runners import InProcessTaskRunner, TaskEnqueueError
from guancha_api.functions.extraction_worker import (
    CloudFunctionExtractionWorker,
    WorkerEventError,
    validate_worker_event,
)
from guancha_api.tasks.cloud_function import (
    CloudFunctionExtractionDispatcher,
    SCF_EVENT_INVOCATION_TYPE,
    TencentScfEventInvoker,
    _runtime_credentials,
)

class _Request:
    FunctionName = None
    InvocationType = None
    Namespace = None
    ClientContext = None
    Qualifier = None
    LogType = None
    RoutingKey = None


class _CapturingScfClient:
    def __init__(self) -> None:
        self.requests: list[_Request] = []
        self.fail = False

    def Invoke(self, request: _Request) -> object:
        if self.fail:
            raise RuntimeError("synthetic SDK rejection")
        self.requests.append(request)
        return SimpleNamespace(RequestId="synthetic-request-id")


class _FakeInvoker:
    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []
        self.calls: list[tuple[str, str]] = []
        self.worker_finished = False
        self.fail = False

    async def invoke(self, *, function_name: str, namespace: str, event: dict[str, str]) -> None:
        if self.fail:
            raise RuntimeError("synthetic rejection")
        self.calls.append((function_name, namespace))
        self.events.append(dict(event))


@pytest.mark.asyncio
async def test_sdk_invoker_builds_event_request_with_only_job_id() -> None:
    client = _CapturingScfClient()
    invoker = TencentScfEventInvoker(client=client, request_factory=_Request)
    job_id = uuid4()

    await invoker.invoke(
        function_name="guancha-extraction-worker",
        namespace="default",
        event={"job_id": str(job_id)},
    )

    request = client.requests[0]
    assert request.FunctionName == "guancha-extraction-worker"
    assert request.InvocationType == SCF_EVENT_INVOCATION_TYPE
    assert request.Namespace == "default"
    assert json.loads(request.ClientContext) == {"job_id": str(job_id)}
    assert request.LogType is None
    assert request.RoutingKey is None


@pytest.mark.asyncio
async def test_dispatcher_does_not_execute_local_callback_or_wait_for_worker() -> None:
    invoker = _FakeInvoker()
    dispatcher = CloudFunctionExtractionDispatcher(
        invoker=invoker, function_name="synthetic-function"
    )
    callback_called = False

    async def local_business_callback() -> None:
        nonlocal callback_called
        callback_called = True
        invoker.worker_finished = True

    accepted = await dispatcher.enqueue(job_id=uuid4(), task=local_business_callback)

    assert accepted is True
    assert callback_called is False
    assert invoker.worker_finished is False
    assert len(invoker.events) == 1
    assert set(invoker.events[0]) == {"job_id"}


@pytest.mark.asyncio
async def test_dispatcher_returns_while_external_worker_simulation_is_slow() -> None:
    if os.getenv("RUN_GATE3A_SLOW_POC") != "1":
        pytest.skip("RUN_GATE3A_SLOW_POC=1 is required for the 60-second simulation")
    invoker = _FakeInvoker()
    dispatcher = CloudFunctionExtractionDispatcher(
        invoker=invoker, function_name="synthetic-function"
    )
    worker_done = asyncio.Event()

    async def external_worker_simulation() -> None:
        await asyncio.sleep(60.1)
        worker_done.set()

    worker_task = asyncio.create_task(external_worker_simulation())
    try:
        started = asyncio.get_running_loop().time()
        assert await dispatcher.enqueue(job_id=uuid4(), task=lambda: asyncio.sleep(0))
        accepted_in_seconds = asyncio.get_running_loop().time() - started
        assert accepted_in_seconds < 1
        assert not worker_done.is_set()
        await worker_task
        assert worker_done.is_set()
    finally:
        if not worker_task.done():
            worker_task.cancel()
            await worker_task


@pytest.mark.asyncio
async def test_dispatcher_rejection_is_task_enqueue_error_without_sdk_text() -> None:
    invoker = _FakeInvoker()
    invoker.fail = True
    dispatcher = CloudFunctionExtractionDispatcher(
        invoker=invoker, function_name="synthetic-function"
    )

    with pytest.raises(TaskEnqueueError, match="Event invocation was rejected"):
        await dispatcher.enqueue(job_id=uuid4(), task=lambda: asyncio.sleep(0))


@pytest.mark.asyncio
async def test_dispatcher_rejects_non_uuid_job_id() -> None:
    dispatcher = CloudFunctionExtractionDispatcher(
        invoker=_FakeInvoker(), function_name="synthetic-function"
    )
    with pytest.raises(TaskEnqueueError, match="UUID job id"):
        await dispatcher.enqueue(job_id="not-a-uuid", task=lambda: asyncio.sleep(0))  # type: ignore[arg-type]


def test_dispatcher_fails_closed_without_runtime_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUANCHA_EXTRACTION_FUNCTION_NAME", "synthetic-function")
    for name in (
        "TENCENTCLOUD_SECRETID",
        "TENCENTCLOUD_SECRETKEY",
        "TENCENTCLOUD_SESSIONTOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(TaskEnqueueError, match="runtime Tencent Cloud credentials"):
        CloudFunctionExtractionDispatcher.from_environment()


def test_dispatcher_requires_explicit_function_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUANCHA_EXTRACTION_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("TENCENTCLOUD_SECRETID", "synthetic-id")
    monkeypatch.setenv("TENCENTCLOUD_SECRETKEY", "synthetic-key")
    with pytest.raises(TaskEnqueueError, match="explicit function name"):
        CloudFunctionExtractionDispatcher.from_environment()


def test_runtime_credentials_support_session_token_without_exposing_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENCENTCLOUD_SECRETID", "synthetic-id")
    monkeypatch.setenv("TENCENTCLOUD_SECRETKEY", "synthetic-key")
    monkeypatch.setenv("TENCENTCLOUD_SESSIONTOKEN", "synthetic-session")

    # Exercise the SDK constructor without invoking any cloud API.
    invoker = TencentScfEventInvoker.from_environment()
    assert invoker.region == "ap-shanghai"
    assert _runtime_credentials() == (
        "synthetic-id",
        "synthetic-key",
        "synthetic-session",
    )


def test_installed_tencent_sdk_exposes_async_event_api() -> None:
    models = pytest.importorskip("tencentcloud.scf.v20180416.models")
    client_module = pytest.importorskip("tencentcloud.scf.v20180416.scf_client")
    assert hasattr(models, "InvokeRequest")
    assert hasattr(client_module.ScfClient, "Invoke")


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {"job_id": "not-a-uuid"},
        {"job_id": str(uuid4()), "extra": "forbidden"},
        {"job_id": uuid4()},
    ],
)
def test_worker_rejects_malformed_job_id(event: object) -> None:
    with pytest.raises(WorkerEventError):
        validate_worker_event(event)


@dataclass
class _WorkerRepository:
    close_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1


class _Runner:
    def __init__(self, repository: object, provider: object, storage: object, *, timeout_seconds: float) -> None:
        self.repository = repository
        self.provider = provider
        self.storage = storage
        self.timeout_seconds = timeout_seconds
        self.job_ids: list[UUID] = []

    async def run(self, *, job_id: UUID) -> None:
        self.job_ids.append(job_id)


@pytest.mark.asyncio
async def test_worker_delegates_to_the_existing_claimed_job_runner_and_closes_repository() -> None:
    job_id = uuid4()
    repository = _WorkerRepository()
    runners: list[_Runner] = []

    def runner_factory(*args: object, **kwargs: object) -> _Runner:
        runner = _Runner(*args, **kwargs)  # type: ignore[arg-type]
        runners.append(runner)
        return runner

    worker = CloudFunctionExtractionWorker(
        repository_factory=lambda: _ready(repository),  # type: ignore[arg-type]
        storage_factory=lambda: object(),
        provider_factory=lambda storage: object(),  # type: ignore[arg-type]
        runner_factory=runner_factory,  # type: ignore[arg-type]
    )

    result = await worker.run({"job_id": str(job_id)})

    assert result == {"job_id": str(job_id), "status": "handled"}
    assert runners[0].job_ids == [job_id]
    assert runners[0].timeout_seconds == 170.0
    assert repository.close_calls == 1


@pytest.mark.asyncio
async def test_worker_repository_failure_does_not_log_or_return_credentials() -> None:
    async def failing_factory() -> object:
        raise RuntimeError("synthetic repository failure")

    worker = CloudFunctionExtractionWorker(repository_factory=failing_factory)  # type: ignore[arg-type]

    with pytest.raises(Exception) as error:
        await worker.run({"job_id": str(uuid4())})
    assert "synthetic.invalid" not in str(error.value)


@pytest.mark.asyncio
async def test_default_application_path_remains_in_process() -> None:
    from guancha_api.main import create_app

    app = create_app()
    assert isinstance(app.state.task_runner, InProcessTaskRunner)
    assert isinstance(app.state.extraction_task_runner, InProcessTaskRunner)


async def _ready(value: object) -> object:
    return value
