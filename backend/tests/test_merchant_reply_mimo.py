from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from guancha_api.main import _merchant_reply_provider_from_environment
from guancha_api.providers.fake import (
    ProviderNetworkError,
    ProviderStructuredOutputError,
    ProviderTimeoutError,
)
from guancha_api.providers.merchant_reply import FakeMerchantReplyReasoningProvider
from guancha_api.providers.merchant_reply_mimo import (
    DEFAULT_MIMO_MERCHANT_REPLY_TIMEOUT_SECONDS,
    MIMO_MERCHANT_REPLY_MAX_COMPLETION_TOKENS,
    MerchantSemanticNormalization,
    MiMoMerchantReplyReasoningProvider,
)


class _InstructorClient:
    def __init__(self, output: object | Exception) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.output, Exception):
            raise self.output
        if isinstance(self.output, str):
            return MerchantSemanticNormalization.model_validate(json.loads(self.output))
        return MerchantSemanticNormalization.model_validate(self.output)


class _RawClient:
    pass


def _provider(output: object | Exception) -> tuple[MiMoMerchantReplyReasoningProvider, _InstructorClient]:
    instructor_client = _InstructorClient(output)
    provider = MiMoMerchantReplyReasoningProvider(
        api_key="synthetic-mimo-key",
        model="mimo-v2.5",
        client_factory=lambda _key, _base_url: _RawClient(),
        instructor_client_factory=lambda _client: instructor_client,
    )
    return provider, instructor_client


def _output(**overrides: object) -> str:
    value: dict[str, object] = {
        "status": "answered",
        "normalized_value": "light",
        "evidence_text": "整体偏轻一点",
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.asyncio
async def test_mimo_merchant_reply_returns_closed_answer_and_python_owned_conflict() -> None:
    provider, client = _provider(_output())
    parsed = await provider.parse_merchant_reply(
        field_key="roast_level",
        raw_text="这个火候不会很重，整体偏轻一点",
        product_evidence=(
            {"field_name": "roast_level", "normalized_value": "heavy", "information_status": "explicit"},
        ),
    )

    assert parsed.reply_status == "conflicting"
    assert parsed.claims == ({"field_key": "roast_level", "raw_text": "整体偏轻一点", "normalized_value": "light"},)
    assert parsed.conflicts == ("roast_level",)
    request = client.calls[0]
    assert request["response_model"] is MerchantSemanticNormalization
    assert request["max_retries"] == 0
    assert request["max_completion_tokens"] == MIMO_MERCHANT_REPLY_MAX_COMPLETION_TOKENS == 512
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    payload = json.loads(request["messages"][1]["content"])
    assert set(payload) == {"field_key", "field_meaning", "allowed_values", "merchant_raw_text"}
    assert payload["field_key"] == "roast_level"
    assert payload["allowed_values"] == ["light", "medium", "heavy"]
    assert payload["merchant_raw_text"] == "这个火候不会很重，整体偏轻一点"
    assert "product_evidence" not in json.dumps(payload, ensure_ascii=False)
    assert provider.structured_output_mode == "json_schema"
    assert "candidate IDs" in request["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_text", "output", "expected"),
    [
        ("这个火候不会很重，整体偏轻一些", _output(normalized_value="light", evidence_text="整体偏轻一些"), "light"),
        ("焙得挺深的，熟香会比较明显", _output(normalized_value="heavy", evidence_text="焙得挺深"), "heavy"),
        ("今年春天采的这一批", _output(normalized_value="spring", evidence_text="今年春天采的这一批"), "spring"),
        ("秋天采的那批", _output(normalized_value="autumn", evidence_text="秋天采的那批"), "autumn"),
        ("如果你想试，可以先给你寄一点尝尝", _output(normalized_value="true", evidence_text="可以先寄一点"), "true"),
        ("火候还行吧", _output(status="partially-answered", normalized_value=None, evidence_text=None), None),
    ],
)
async def test_semantic_fallback_maps_natural_paraphrases(
    raw_text: str, output: str, expected: str | None,
) -> None:
    field_key = "sample_available" if expected == "true" else "season" if expected in {"spring", "autumn"} else "roast_level"
    provider, client = _provider(output)
    parsed = await provider.parse_merchant_reply(field_key=field_key, raw_text=raw_text, product_evidence=())
    if expected is None:
        assert parsed.reply_status == "partially-answered"
        assert parsed.claims == ()
    else:
        assert parsed.reply_status == "answered"
        assert parsed.claims[0]["normalized_value"] == expected
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_ambiguity"),
    [("partially-answered", 1), ("evasive", 1), ("not-answered", 0)],
)
async def test_mimo_merchant_reply_preserves_unresolved_statuses(status: str, expected_ambiguity: int) -> None:
    provider, _ = _provider(_output(status=status, normalized_value=None, evidence_text=None))
    parsed = await provider.parse_merchant_reply(field_key="season", raw_text="模型语义测试", product_evidence=())
    assert parsed.reply_status == status
    assert parsed.claims == ()
    assert parsed.unresolved_fields == ("season",)
    assert parsed.coverage == 0
    assert parsed.ambiguity == expected_ambiguity
    assert parsed.should_rejudge is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        _output(status="answered", normalized_value=None),
        _output(unexpected="candidate-data"),
        _output(normalized_value={"value": "light"}),
    ],
)
async def test_mimo_merchant_reply_rejects_malformed_or_out_of_scope_output(payload: str) -> None:
    provider, _ = _provider(payload)
    with pytest.raises(ProviderStructuredOutputError):
        await provider.parse_merchant_reply(field_key="roast_level", raw_text="模型语义测试", product_evidence=())


@pytest.mark.asyncio
async def test_out_of_vocabulary_model_value_becomes_unresolved_without_new_ontology() -> None:
    provider, client = _provider(_output(normalized_value="fresh", evidence_text="清爽型"))
    parsed = await provider.parse_merchant_reply(field_key="roast_level", raw_text="自然语言", product_evidence=())
    assert len(client.calls) == 1
    assert parsed.reply_status == "partially-answered"
    assert parsed.claims == ()
    assert parsed.unresolved_fields == ("roast_level",)


def test_mimo_merchant_reply_timeout_is_bounded_and_uses_json_schema_mode() -> None:
    assert DEFAULT_MIMO_MERCHANT_REPLY_TIMEOUT_SECONDS == 12.0
    provider, _ = _provider(_output())
    assert provider.timeout_seconds <= 12
    assert provider.structured_output_mode == "json_schema"
    with pytest.raises(ValueError):
        MiMoMerchantReplyReasoningProvider(api_key="key", model="model", timeout_seconds=12.1)


def test_default_instructor_adapter_selects_json_schema_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    fake_mode = types.SimpleNamespace(JSON_SCHEMA=object())
    fake_instructor = types.ModuleType("instructor")
    fake_instructor.Mode = fake_mode
    fake_v2 = types.ModuleType("instructor.v2")
    fake_providers = types.ModuleType("instructor.v2.providers")
    fake_openai = types.ModuleType("instructor.v2.providers.openai")

    def fake_from_openai(client: object, *, mode: object) -> object:
        captured["client"] = client
        captured["mode"] = mode
        return object()

    fake_openai.from_openai = fake_from_openai
    monkeypatch.setitem(sys.modules, "instructor", fake_instructor)
    monkeypatch.setitem(sys.modules, "instructor.v2", fake_v2)
    monkeypatch.setitem(sys.modules, "instructor.v2.providers", fake_providers)
    monkeypatch.setitem(sys.modules, "instructor.v2.providers.openai", fake_openai)

    raw_client = object()
    provider = MiMoMerchantReplyReasoningProvider(
        api_key="synthetic-mimo-key", model="mimo-v2.5",
        client_factory=lambda _key, _base_url: raw_client,
    )
    provider._instructor_client()
    assert captured == {"client": raw_client, "mode": fake_mode.JSON_SCHEMA}


@pytest.mark.asyncio
async def test_mimo_merchant_reply_maps_timeout_and_does_not_expose_api_key() -> None:
    provider, _ = _provider(RuntimeError("synthetic-mimo-key leaked by upstream"))
    with pytest.raises(ProviderNetworkError) as error:
        await provider.parse_merchant_reply(field_key="season", raw_text="模型语义测试", product_evidence=())
    assert "synthetic-mimo-key" not in str(error.value)

    timeout_provider, _ = _provider(asyncio.TimeoutError())
    with pytest.raises(ProviderTimeoutError):
        await timeout_provider.parse_merchant_reply(field_key="season", raw_text="模型语义测试", product_evidence=())


@pytest.mark.asyncio
async def test_local_first_answers_do_not_call_instructor() -> None:
    provider, client = _provider(_output(normalized_value="heavy"))
    cases = (
        ("roast_level", "轻火", "light"),
        ("roast_level", "中火", "medium"),
        ("sample_available", "提供试饮装", "true"),
        ("sample_available", "提供10g试饮装", "true"),
        ("season", "今年春茶", "spring"),
    )
    for field_key, raw_text, expected in cases:
        parsed = await provider.parse_merchant_reply(field_key=field_key, raw_text=raw_text, product_evidence=())
        assert parsed.claims[0]["normalized_value"] == expected
    assert client.calls == []


def test_merchant_provider_resolution_follows_vision_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUANCHA_PROVIDER", "fake")
    monkeypatch.delenv("GUANCHA_MERCHANT_REPLY_PROVIDER", raising=False)
    assert isinstance(_merchant_reply_provider_from_environment(), FakeMerchantReplyReasoningProvider)

    monkeypatch.setenv("GUANCHA_MERCHANT_REPLY_PROVIDER", "fake")
    assert isinstance(_merchant_reply_provider_from_environment(), FakeMerchantReplyReasoningProvider)

    monkeypatch.delenv("GUANCHA_MERCHANT_REPLY_PROVIDER", raising=False)
    monkeypatch.setenv("GUANCHA_PROVIDER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "synthetic-mimo-key")
    monkeypatch.setenv("GUANCHA_MIMO_MODEL", "mimo-v2.5")
    assert isinstance(_merchant_reply_provider_from_environment(), MiMoMerchantReplyReasoningProvider)

    monkeypatch.setenv("GUANCHA_PROVIDER", "fake")
    monkeypatch.setenv("GUANCHA_MERCHANT_REPLY_PROVIDER", "mimo")
    assert isinstance(_merchant_reply_provider_from_environment(), MiMoMerchantReplyReasoningProvider)

    monkeypatch.setenv("GUANCHA_PROVIDER", "mimo")
    monkeypatch.setenv("GUANCHA_MERCHANT_REPLY_PROVIDER", "fake")
    assert isinstance(_merchant_reply_provider_from_environment(), FakeMerchantReplyReasoningProvider)

    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.delenv("GUANCHA_MIMO_MODEL", raising=False)
    monkeypatch.setenv("GUANCHA_MERCHANT_REPLY_PROVIDER", "mimo")
    with pytest.raises(RuntimeError, match="requires MIMO_API_KEY and GUANCHA_MIMO_MODEL"):
        _merchant_reply_provider_from_environment()
