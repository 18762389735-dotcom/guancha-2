from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from guancha_api.application.job_runner import FakeExtractionJobRunner
from guancha_api.application.merchant_reply_service import MerchantReplyService
from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.providers.fake import ProviderNetworkError
from guancha_api.providers.merchant_reply_mimo import MiMoMerchantReplyReasoningProvider
from guancha_api.repositories.postgres import AiCallLog, StoredJob
from guancha_api.schemas.contracts import ErrorCode, JobStage, JobState, ProcessingMode


pytestmark = pytest.mark.asyncio


@dataclass
class _Repository:
    job_id: UUID
    image_ids: tuple[UUID, UUID]
    processing_mode: ProcessingMode
    completed_log: AiCallLog | None = None
    completed_evidence: tuple[object, ...] = ()
    failure: ErrorCode | None = None

    async def claim_job(self, *, job_id: UUID) -> bool:
        return job_id == self.job_id

    async def get_claimed_job(self, *, job_id: UUID) -> StoredJob:
        now = datetime.now(timezone.utc)
        return StoredJob(
            id=job_id, candidate_id=uuid4(), candidate_image_id=self.image_ids[0],
            status=JobState.PROCESSING, stage=JobStage.CLAIMED, attempt=1,
            processing_mode=self.processing_mode, created_at=now, updated_at=now,
            input_image_ids=self.image_ids, input_set_version=1,
        )

    async def get_job_input_images(self, *, job_id: UUID) -> tuple[dict[str, object], ...]:
        assert job_id == self.job_id
        return tuple(
            {"id": image_id, "display_order": index, "sanitized_sha256": digest}
            for index, (image_id, digest) in enumerate((
                (self.image_ids[0], "9b299220b23ae3c55c9805f30a373367ff7b82ba4a7efbc8c134b4767df2bd94"),
                (self.image_ids[1], "5d6af4d0f7c604bc1be164e24c85b8e406d3b48c8a15b9d72a233424d398af72"),
            ), start=1)
        )

    async def complete_extraction_job(self, **kwargs: object) -> None:
        self.completed_log = kwargs["ai_log"]  # type: ignore[assignment]
        self.completed_evidence = tuple(kwargs["evidence_items"])  # type: ignore[arg-type]

    async def fail_extraction_job(self, *, job_id: UUID, error_code: ErrorCode) -> None:
        assert job_id == self.job_id
        self.failure = error_code


class _FailingLiveProvider:
    def __init__(self) -> None:
        self.extract_calls = 0

    provider_name = "mimo"
    model_identifier = "test-live-model"
    processing_mode = ProcessingMode.OPENAI_VISION

    async def extract(self, **_: object) -> dict[str, object]:
        self.extract_calls += 1
        raise ProviderNetworkError("provider unavailable")

    async def repair_structure(self, **_: object) -> dict[str, object]:
        raise AssertionError("schema repair must not run for a network failure")


class _TimingOutLiveProvider(_FailingLiveProvider):
    async def extract(self, **_: object) -> dict[str, object]:
        self.extract_calls += 1
        await asyncio.sleep(1)
        return {}


class _SuccessfulLiveProvider(_FailingLiveProvider):
    async def extract(self, **_: object) -> dict[str, object]:
        self.extract_calls += 1
        return {
            "product_name": "示例铁观音",
            "tea_category": "乌龙茶",
            "tea_subtype": "铁观音",
            "evidence": [{
                "field_name": "tea_type", "raw_text": "铁观音", "normalized_value": "tieguanyin",
                "model_confidence": 0.9, "information_status": "explicit",
                "source_type": "product-claim", "verification_status": "unverified",
                "source_location": "title", "evidence_strength": "high",
            }],
        }


class _FailingMimoProvider(MiMoMerchantReplyReasoningProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test-key", model="test-model")
        self.parse_calls = 0

    async def parse_merchant_reply(self, **_: object):
        self.parse_calls += 1
        raise ProviderNetworkError("provider unavailable")


class _MerchantRepository:
    def __init__(self, reply_id: UUID) -> None:
        self.reply_id = reply_id
        self.persisted: tuple[str, tuple[dict[str, str], ...]] | None = None
        self.failed = False

    async def claim_merchant_reply_for_parse(self, *, reply_id: UUID, client_id: object):
        assert reply_id == self.reply_id
        return (
            {"id": reply_id, "field_key": "season", "raw_text": "这是商家的示例回复"},
            (),
        )

    async def persist_merchant_reply_parse(self, *, reply_id: UUID, client_id: object, parsed_status: str, claims: tuple[dict[str, str], ...]) -> None:
        self.persisted = (parsed_status, claims)

    async def fail_merchant_reply_parse(self, *, reply_id: UUID, client_id: object) -> None:
        self.failed = True


async def _runner(processing_mode: ProcessingMode, provider: object = _FailingLiveProvider()):
    job_id, image_ids = uuid4(), (uuid4(), uuid4())
    repository = _Repository(job_id, image_ids, processing_mode)
    storage = InMemoryTemporaryPrivateStorage()
    for image_id in image_ids:
        await storage.put_private(object_key=f"temporary/{image_id}", content_type="image/png", data=b"fixture")
    await FakeExtractionJobRunner(repository, provider, storage, timeout_seconds=0.01).run(job_id=job_id)  # type: ignore[arg-type]
    return repository


async def test_approved_sample_provider_failure_uses_existing_fixture() -> None:
    repository = await _runner(ProcessingMode.CACHE_FALLBACK)

    assert repository.failure is None
    assert repository.completed_log is not None
    assert repository.completed_log.processing_mode is ProcessingMode.CACHE_FALLBACK
    assert {item.field_name for item in repository.completed_evidence} >= {"tea_type", "sample_available"}


async def test_approved_sample_skips_live_provider_and_uses_fixture_result() -> None:
    provider = _SuccessfulLiveProvider()
    repository = await _runner(ProcessingMode.CACHE_FALLBACK, provider)

    assert repository.failure is None
    assert repository.completed_log is not None
    assert repository.completed_log.processing_mode is ProcessingMode.CACHE_FALLBACK
    assert provider.extract_calls == 0
    assert {item.field_name for item in repository.completed_evidence} >= {"tea_type", "sample_available"}


async def test_approved_sample_works_when_live_provider_is_unconfigured() -> None:
    provider = _FailingLiveProvider()
    provider.provider_name = "unconfigured"
    repository = await _runner(ProcessingMode.CACHE_FALLBACK, provider)

    assert repository.failure is None
    assert repository.completed_log is not None
    assert provider.extract_calls == 0


async def test_sample_timeout_uses_existing_fixture() -> None:
    provider = _TimingOutLiveProvider()
    repository = await _runner(ProcessingMode.CACHE_FALLBACK, provider)

    assert repository.failure is None
    assert repository.completed_log is not None
    assert repository.completed_log.processing_mode is ProcessingMode.CACHE_FALLBACK
    assert provider.extract_calls == 0


async def test_real_upload_provider_failure_stays_on_normal_error_path() -> None:
    repository = await _runner(ProcessingMode.OPENAI_VISION)

    assert repository.completed_log is None
    assert repository.failure is ErrorCode.AI_PROVIDER_ERROR


async def test_merchant_fixture_fallback_requires_explicit_sample_marker() -> None:
    provider = object()
    service = MerchantReplyService(repository=object(), provider=provider)  # type: ignore[arg-type]

    result = service._demo_reply_fallback(
        field_key="season", raw_text="这是商家的示例回复", allow_demo_fallback=True
    )
    assert result is not None
    assert result.claims == ({"field_key": "season", "raw_text": "2025年春茶", "normalized_value": "spring"},)

    blocked = service._demo_reply_fallback(
        field_key="season", raw_text="这是商家的示例回复", allow_demo_fallback=False
    )
    assert blocked is None


async def test_sample_merchant_provider_failure_uses_fixture_before_persisting() -> None:
    reply_id = uuid4()
    repository = _MerchantRepository(reply_id)
    provider = _FailingMimoProvider()
    service = MerchantReplyService(repository=repository, provider=provider)  # type: ignore[arg-type]

    await service.parse(reply_id=reply_id, allow_demo_fallback=True, client_id=uuid4())

    assert repository.failed is False
    assert repository.persisted == (
        "answered",
        ({"field_key": "season", "raw_text": "2025年春茶", "normalized_value": "spring"},),
    )
    assert provider.parse_calls == 0


async def test_non_sample_merchant_provider_failure_stays_on_error_path() -> None:
    reply_id = uuid4()
    repository = _MerchantRepository(reply_id)
    provider = _FailingMimoProvider()
    service = MerchantReplyService(repository=repository, provider=provider)  # type: ignore[arg-type]

    with pytest.raises(ProviderNetworkError):
        await service.parse(reply_id=reply_id, allow_demo_fallback=False, client_id=uuid4())

    assert repository.failed is True
    assert repository.persisted is None
    assert provider.parse_calls == 1
