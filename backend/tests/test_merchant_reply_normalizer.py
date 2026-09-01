from __future__ import annotations

import asyncio

from guancha_api.providers.merchant_reply import FakeMerchantReplyReasoningProvider
from guancha_api.providers.merchant_reply_normalizer import LocalMerchantReplyNormalizer


def test_local_normalizer_uses_only_unambiguous_roast_terms() -> None:
    normalizer = LocalMerchantReplyNormalizer()
    assert normalizer.normalize("roast_level", "轻火", ()).claims[0]["normalized_value"] == "light"
    assert normalizer.normalize("roast_level", "轻焙", ()).claims[0]["normalized_value"] == "light"
    assert normalizer.normalize("roast_level", "中火", ()).claims[0]["normalized_value"] == "medium"
    assert normalizer.normalize("roast_level", "足火", ()).claims[0]["normalized_value"] == "heavy"
    assert normalizer.normalize("roast_level", "清香", ()) is None
    assert normalizer.normalize("roast_level", "浓香", ()) is None
    assert normalizer.normalize("roast_level", "这个火候不会很重，整体偏轻一点", ()) is None


def test_local_normalizer_does_not_infer_season_from_generic_freshness_words() -> None:
    normalizer = LocalMerchantReplyNormalizer()
    assert normalizer.normalize("season", "春茶", ()).claims[0]["normalized_value"] == "spring"
    assert normalizer.normalize("season", "春季采摘", ()).claims[0]["normalized_value"] == "spring"
    assert normalizer.normalize("season", "秋茶", ()).claims[0]["normalized_value"] == "autumn"
    assert normalizer.normalize("season", "新茶", ()) is None
    assert normalizer.normalize("season", "今年的", ()) is None
    assert normalizer.normalize("season", "2026年", ()) is None


def test_local_normalizer_sample_negation_wins() -> None:
    normalizer = LocalMerchantReplyNormalizer()
    for text in ("没有小样", "不提供试饮", "不支持试饮"):
        parsed = normalizer.normalize("sample_available", text, ())
        assert parsed is not None
        assert parsed.claims[0]["normalized_value"] == "false"
    for text in ("提供小样", "支持试饮", "有试饮装", "可以试饮", "可以寄小样"):
        parsed = normalizer.normalize("sample_available", text, ())
        assert parsed is not None
        assert parsed.claims[0]["normalized_value"] == "true"


def test_local_normalizer_handles_closed_unanswered_and_evasive_states() -> None:
    normalizer = LocalMerchantReplyNormalizer()
    assert normalizer.normalize("season", "这个我也不太清楚", ()).reply_status == "not-answered"
    assert normalizer.normalize("season", "我们这个很多老客户回购的", ()).reply_status == "evasive"
    assert normalizer.normalize("season", "火候还可以", ()) is None


def test_fake_provider_keeps_legacy_fixture_compatibility_without_changing_production_defaults() -> None:
    provider = FakeMerchantReplyReasoningProvider()
    assert asyncio.run(provider.parse_merchant_reply(field_key="roast_level", raw_text="浅", product_evidence=())).claims[0]["normalized_value"] == "light"
    assert asyncio.run(provider.parse_merchant_reply(field_key="roast_level", raw_text="浓", product_evidence=())).claims[0]["normalized_value"] == "heavy"
