from __future__ import annotations

import pytest

from guancha_api.application.task_runners import InProcessTaskRunner
from guancha_api import main


def test_extraction_execution_defaults_to_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUANCHA_EXTRACTION_EXECUTION", raising=False)
    assert isinstance(main._extraction_task_runner_from_environment(), InProcessTaskRunner)


def test_extraction_execution_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUANCHA_EXTRACTION_EXECUTION", "unknown")
    with pytest.raises(RuntimeError, match="in-process or cloud-function"):
        main._extraction_task_runner_from_environment()


def test_cloud_function_execution_selection_is_extraction_only(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDispatcher:
        requested_region: str | None = None

        @classmethod
        def from_environment(cls, *, region: str) -> "FakeDispatcher":
            cls.requested_region = region
            return cls()

        async def enqueue(self, **_: object) -> bool:
            return True

        async def shutdown(self) -> None:
            return None

    monkeypatch.setattr(main, "CloudFunctionExtractionDispatcher", FakeDispatcher)
    monkeypatch.setenv("GUANCHA_EXTRACTION_EXECUTION", "cloud-function")
    monkeypatch.setenv("GUANCHA_EXTRACTION_FUNCTION_REGION", "ap-shanghai")

    app = main.create_app()

    assert isinstance(app.state.task_runner, InProcessTaskRunner)
    assert isinstance(app.state.extraction_task_runner, FakeDispatcher)
    assert FakeDispatcher.requested_region == "ap-shanghai"
