"""Offline tests for the MiMo adapter; no credential or network is used."""

from __future__ import annotations

import json
from base64 import b64decode
from io import BytesIO
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.infrastructure.temporary_images import temporary_image_object_key
from guancha_api.application.job_runner import (
    FakeEvidencePayload,
    FakeExtractionJobRunner,
    FakeExtractionPayload,
)
from guancha_api.providers.fake import (
    ProviderNetworkError,
    ProviderRateLimitedError,
    ProviderStructuredOutputError,
    ProviderTimeoutError,
)
from guancha_api.providers.mimo import DEFAULT_MIMO_BASE_URL, MiMoVisionProvider
from guancha_api.repositories.postgres import StoredJob
from guancha_api.schemas.contracts import JobState, ProcessingMode


def _payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "product_name": "铁观音", "tea_category": "乌龙茶", "tea_subtype": None,
        "origin": None, "roast_or_style": None, "aroma_claims": [], "taste_claims": [],
        "season": None, "year_or_batch": None, "grade": None, "weight": None, "price": None,
        "brew_claims": [], "risk_flags": [],
        "evidence": [{
            "field_name": "product_name", "raw_text": "铁观音", "normalized_value": "铁观音",
            "model_confidence": 0.9, "information_status": "unknown",
            "source_type": "merchant-claim", "verification_status": "system-consistent",
            "source_location": "title", "evidence_strength": "high", "source_image_index": 1,
        }],
    }
    value.update(overrides)
    return value


class _ChatCompletions:
    def __init__(self, output: object | Exception) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.output, Exception):
            raise self.output
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.output))])


class _Client:
    def __init__(self, output: object | Exception) -> None:
        self.chat = SimpleNamespace(completions=_ChatCompletions(output))


class _HttpError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


async def _provider(output: object | Exception) -> tuple[MiMoVisionProvider, _Client, list[tuple[str, str]]]:
    storage = InMemoryTemporaryPrivateStorage()
    await storage.put_private(object_key="image", content_type="image/png", data=b"\x89PNG\r\n\x1a\nfixture")
    client = _Client(output)
    factory_calls: list[tuple[str, str]] = []
    provider = MiMoVisionProvider(
        api_key="test-key", model="mimo-v2.5", storage=storage,
        client_factory=lambda key, base_url: (factory_calls.append((key, base_url)) or client),
    )
    return provider, client, factory_calls


@pytest.mark.asyncio
async def test_mimo_adapter_constructs_one_image_json_mode_request_and_preserves_unknown() -> None:
    provider, client, factory_calls = await _provider(json.dumps(_payload()))

    result = await provider.extract(image_object_key="image")

    assert provider.provider_name == "mimo"
    assert provider.model_identifier == "mimo-v2.5"
    assert provider.processing_mode is ProcessingMode.OPENAI_VISION
    assert result["origin"] is None
    assert result["evidence"][0]["information_status"] == "unknown"
    assert factory_calls == [("test-key", DEFAULT_MIMO_BASE_URL)]
    request = client.chat.completions.calls[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "sample_available" in request["messages"][0]["content"]
    assert 'normalized_value "true"' in request["messages"][0]["content"]
    assert '"false"' in request["messages"][0]["content"]
    assert "non-empty string" in request["messages"][0]["content"]
    assert "新茶 alone is not a season" in request["messages"][0]["content"]
    assert "season_claim_conflict" in request["messages"][0]["content"]
    assert "暂无/不支持/售罄" in request["messages"][0]["content"]
    content = request["messages"][1]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert request["max_completion_tokens"] == 4096
    assert "source_image_index" in content[0]["text"]


def test_provider_schema_matches_non_empty_evidence_contract() -> None:
    from guancha_api.providers.openai import _EXTRACTION_SCHEMA

    evidence = _EXTRACTION_SCHEMA["schema"]["properties"]["evidence"]
    assert evidence["minItems"] == 1
    properties = evidence["items"]["properties"]
    for field_name in ("field_name", "raw_text", "normalized_value", "source_location"):
        assert properties[field_name]["minLength"] == 1


@pytest.mark.parametrize(("value", "expected"), [(True, "true"), (False, "false")])
def test_runner_normalizes_only_sample_available_boolean(value: bool, expected: str) -> None:
    payload = _payload(evidence=[{**_payload()["evidence"][0], "field_name": "sample_available", "normalized_value": value}])

    parsed = FakeExtractionJobRunner._validate_payload(payload, image_count=1)

    assert parsed.evidence[0].normalized_value == expected


def test_runner_does_not_stringify_unrelated_normalized_value_object() -> None:
    payload = _payload(evidence=[{**_payload()["evidence"][0], "normalized_value": {"value": "tea"}}])

    with pytest.raises(ValidationError):
        FakeExtractionJobRunner._validate_payload(payload, image_count=1)


@pytest.mark.asyncio
async def test_mimo_adapter_sends_two_candidate_images_in_one_ordered_request() -> None:
    storage = InMemoryTemporaryPrivateStorage()
    await storage.put_private(object_key="candidate-a-1", content_type="image/png", data=b"\x89PNG\r\n\x1a\nfirst")
    await storage.put_private(object_key="candidate-a-2", content_type="image/png", data=b"\x89PNG\r\n\x1a\nsecond")
    client = _Client(json.dumps(_payload()))
    provider = MiMoVisionProvider(
        api_key="test-key", model="mimo-v2.5", storage=storage,
        client_factory=lambda _key, _base_url: client,
    )

    await provider.extract(image_object_keys=("candidate-a-1", "candidate-a-2"))

    content = client.chat.completions.calls[0]["messages"][1]["content"]
    image_parts = [part for part in content if part["type"] == "image_url"]
    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"] != image_parts[1]["image_url"]["url"]


@pytest.mark.asyncio
async def test_mimo_adapter_downsizes_only_the_ephemeral_model_input() -> None:
    storage = InMemoryTemporaryPrivateStorage()
    original = BytesIO()
    Image.new("RGB", (2000, 1000), "white").save(original, format="JPEG")
    await storage.put_private(
        object_key="large-image", content_type="image/jpeg", data=original.getvalue()
    )
    client = _Client(json.dumps(_payload()))
    provider = MiMoVisionProvider(
        api_key="test-key", model="mimo-v2.5", storage=storage,
        client_factory=lambda _key, _base_url: client,
    )

    await provider.extract(image_object_key="large-image")

    url = client.chat.completions.calls[0]["messages"][1]["content"][1]["image_url"]["url"]
    sent = b64decode(url.split(",", 1)[1])
    with Image.open(BytesIO(sent)) as resized:
        assert resized.size == (1536, 768)
    assert await storage.read_private(object_key="large-image") == original.getvalue()


@pytest.mark.asyncio
async def test_mimo_adapter_rejects_invalid_json_without_repair_or_fallback() -> None:
    provider, _, _ = await _provider("not-json")
    with pytest.raises(ProviderStructuredOutputError, match="invalid structured output"):
        await provider.extract(image_object_key="image")


@pytest.mark.asyncio
async def test_mimo_adapter_rejects_truncated_or_length_finished_output() -> None:
    truncated, _, _ = await _provider('{"product_name":')
    with pytest.raises(ProviderStructuredOutputError):
        await truncated.extract(image_object_key="image")
    provider, client, _ = await _provider(json.dumps(_payload()))
    client.chat.completions.output = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content=json.dumps(_payload())))]
    )
    with pytest.raises(ProviderStructuredOutputError):
        await provider.extract(image_object_key="image")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        _payload(evidence=[{**_payload()["evidence"][0], "information_status": "verified"}]),
        _payload(price=123),
        _payload(unexpected_field="must-fail"),
    ],
)
async def test_mimo_invalid_payload_never_reaches_persistence(payload: dict[str, object]) -> None:
    storage = InMemoryTemporaryPrivateStorage()
    job_id, image_id = uuid4(), uuid4()
    await storage.put_private(object_key=temporary_image_object_key(job_id), content_type="image/png", data=b"fixture")
    provider = MiMoVisionProvider(
        api_key="test-key", model="mimo-v2.5", storage=storage,
        client_factory=lambda _key, _url: _Client(json.dumps(payload)),
    )

    @dataclass
    class FailingRepository(_RunnerRepository):
        failed_code: object | None = None
        async def fail_extraction_job(self, **kwargs: object) -> None:
            self.failed_code = kwargs["error_code"]

    repository = FailingRepository(job_id=job_id, image_id=image_id)
    await FakeExtractionJobRunner(repository, provider, storage).run(job_id=job_id)  # type: ignore[arg-type]
    assert repository.completed_kwargs is None
    assert repository.failed_code is not None


@pytest.mark.asyncio
async def test_mimo_evidence_cannot_reference_a_second_image_in_a_one_image_job() -> None:
    storage = InMemoryTemporaryPrivateStorage()
    job_id, image_id = uuid4(), uuid4()
    await storage.put_private(object_key=temporary_image_object_key(job_id), content_type="image/png", data=b"fixture")
    invalid = _payload(evidence=[{**_payload()["evidence"][0], "source_image_index": 2}])
    provider = MiMoVisionProvider(
        api_key="test-key", model="mimo-v2.5", storage=storage,
        client_factory=lambda _key, _url: _Client(json.dumps(invalid)),
    )

    @dataclass
    class FailingRepository(_RunnerRepository):
        failed_code: object | None = None
        async def fail_extraction_job(self, **kwargs: object) -> None:
            self.failed_code = kwargs["error_code"]

    repository = FailingRepository(job_id=job_id, image_id=image_id)
    await FakeExtractionJobRunner(repository, provider, storage).run(job_id=job_id)  # type: ignore[arg-type]
    assert repository.completed_kwargs is None
    assert repository.failed_code is not None


@pytest.mark.asyncio
async def test_mimo_adapter_maps_transport_failure_without_exposing_provider_response() -> None:
    provider, _, _ = await _provider(OSError("rate limited"))
    with pytest.raises(ProviderNetworkError, match="MiMo API request failed"):
        await provider.extract(image_object_key="image")


@pytest.mark.asyncio
async def test_mimo_adapter_maps_timeout_and_rate_limit_without_upstream_message() -> None:
    timeout_provider, _, _ = await _provider(_HttpError(504))
    with pytest.raises(ProviderTimeoutError, match="MiMo API request timed out"):
        await timeout_provider.extract(image_object_key="image")

    limited_provider, _, _ = await _provider(_HttpError(429))
    with pytest.raises(ProviderRateLimitedError, match="MiMo API rate limit exceeded"):
        await limited_provider.extract(image_object_key="image")


def test_provider_factory_selects_mimo_and_requires_its_own_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
    from guancha_api.main import _provider_from_environment

    monkeypatch.setenv("GUANCHA_PROVIDER", "mimo")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.delenv("GUANCHA_MIMO_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="MIMO_API_KEY and GUANCHA_MIMO_MODEL"):
        _provider_from_environment(InMemoryTemporaryPrivateStorage())

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    monkeypatch.setenv("GUANCHA_MIMO_MODEL", "mimo-v2.5")
    assert isinstance(_provider_from_environment(InMemoryTemporaryPrivateStorage()), MiMoVisionProvider)


@dataclass
class _RunnerRepository:
    job_id: UUID
    image_id: UUID
    completed_kwargs: dict[str, object] | None = None

    async def claim_job(self, *, job_id: UUID) -> bool:
        return job_id == self.job_id

    async def get_claimed_job(self, *, job_id: UUID) -> StoredJob:
        assert job_id == self.job_id
        now = datetime.now(timezone.utc)
        return StoredJob(
            self.job_id, uuid4(), self.image_id, JobState.PROCESSING, 1,
            ProcessingMode.FAKE_PROVIDER, now, now,
        )

    async def complete_extraction_job(self, **kwargs: object) -> None:
        self.completed_kwargs = kwargs

    async def fail_extraction_job(self, **kwargs: object) -> None:
        raise AssertionError(f"MiMo success path unexpectedly failed: {kwargs}")


@pytest.mark.asyncio
async def test_mimo_success_is_live_ai_and_screenshot_evidence_is_forced_to_unverified_product_claim() -> None:
    storage = InMemoryTemporaryPrivateStorage()
    job_id, image_id = uuid4(), uuid4()
    await storage.put_private(
        object_key=temporary_image_object_key(job_id),
        content_type="image/png",
        data=b"\x89PNG\r\n\x1a\nfixture",
    )
    client = _Client(json.dumps(_payload()))
    provider = MiMoVisionProvider(
        api_key="test-key", model="mimo-v2.5", storage=storage,
        client_factory=lambda _key, _url: client,
    )
    repository = _RunnerRepository(job_id=job_id, image_id=image_id)

    await FakeExtractionJobRunner(repository, provider, storage).run(job_id=job_id)  # type: ignore[arg-type]

    assert repository.completed_kwargs is not None
    ai_log = repository.completed_kwargs["ai_log"]
    evidence = repository.completed_kwargs["evidence_items"]
    assert ai_log.provider == "mimo"
    assert ai_log.processing_mode is ProcessingMode.LIVE_AI
    assert evidence[0].source_type.value == "product-claim"
    assert evidence[0].verification_status.value == "unverified"


@pytest.mark.asyncio
async def test_runner_marks_conflicting_visible_season_claims_without_guessing_one() -> None:
    storage = InMemoryTemporaryPrivateStorage()
    job_id, image_id = uuid4(), uuid4()
    await storage.put_private(object_key=temporary_image_object_key(job_id), content_type="image/png", data=b"fixture")
    conflict_evidence = [
        {"field_name": "season", "raw_text": "春茶", "normalized_value": "spring", "model_confidence": 0.9, "information_status": "explicit", "source_type": "product-claim", "verification_status": "unverified", "source_location": "title", "evidence_strength": "high"},
        {"field_name": "season", "raw_text": "秋季采摘", "normalized_value": "autumn", "model_confidence": 0.9, "information_status": "explicit", "source_type": "product-claim", "verification_status": "unverified", "source_location": "banner", "evidence_strength": "high"},
    ]
    client = _Client(json.dumps(_payload(season="spring", year_or_batch="2025", evidence=conflict_evidence)))
    provider = MiMoVisionProvider(api_key="test-key", model="mimo-v2.5", storage=storage, client_factory=lambda _key, _url: client)
    repository = _RunnerRepository(job_id=job_id, image_id=image_id)

    await FakeExtractionJobRunner(repository, provider, storage).run(job_id=job_id)  # type: ignore[arg-type]

    assert repository.completed_kwargs is not None
    evidence = repository.completed_kwargs["evidence_items"]
    season = [item for item in evidence if item.field_name == "season"]
    assert {item.information_status.value for item in season} == {"conflict"}
    assert any(item.field_name == "risk_flag" and item.normalized_value == "season_claim_conflict" for item in evidence)


@pytest.mark.parametrize(
    ("raw_text", "expected_value"),
    [
        ("\\u652f\\u6301\\u8bd5\\u996e\\u88c5", "true"),
        ("\\u6682\\u65e0\\u8bd5\\u996e\\u88c5", "false"),
        ("\\u4e0d\\u652f\\u6301\\u8bd5\\u559d", "false"),
        ("\\u8bd5\\u996e\\u88c5\\u5df2\\u552e\\u7f44", "false"),
        ("\\u5c0f\\u6837\\u8bf7\\u54a8\\u8be2\\u5ba2\\u670d", None),
        ("\\u8d2d\\u4e70\\u6b63\\u88c5\\u8d60\\u54c1\\u9274\\u88c5", None),
        ("\\u6b22\\u8fce\\u54c1\\u9274", None),
    ],
)
def test_sample_available_requires_an_actual_offer_not_a_keyword(raw_text: str, expected_value: str | None) -> None:
    result = FakeExtractionPayload(
        evidence=(FakeEvidencePayload(
            field_name="sample_available", raw_text=raw_text, normalized_value="true",
            model_confidence=0.9, information_status="explicit", source_type="product-claim",
            verification_status="unverified", source_location="body", evidence_strength="medium",
        ),),
    )
    normalized = FakeExtractionJobRunner._normalize_screenshot_claim_boundaries(result)
    values = [item.normalized_value for item in normalized.evidence if item.field_name == "sample_available"]
    assert values == ([] if expected_value is None else [expected_value])


@pytest.mark.parametrize(
    ("raw_text", "normalized_value", "expected_season"),
    [
        ("\\u6625\\u8336", "spring", "spring"),
        ("\\u79cb\\u5b63\\u91c7\\u6458", "autumn", "autumn"),
        ("2025\\u65b0\\u8336", "2025", None),
        ("\\u4eca\\u5e74\\u65b0\\u8336", "current-year", None),
    ],
)
def test_season_requires_explicit_spring_or_autumn_evidence(
    raw_text: str, normalized_value: str, expected_season: str | None
) -> None:
    result = FakeExtractionPayload(
        season="spring",
        evidence=(FakeEvidencePayload(
            field_name="season", raw_text=raw_text, normalized_value=normalized_value,
            model_confidence=0.9, information_status="explicit", source_type="product-claim",
            verification_status="unverified", source_location="body", evidence_strength="medium",
        ),),
    )
    normalized = FakeExtractionJobRunner._normalize_screenshot_claim_boundaries(result)
    assert normalized.season == expected_season
