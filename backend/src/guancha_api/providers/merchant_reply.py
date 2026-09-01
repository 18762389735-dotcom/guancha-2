from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from guancha_api.providers.merchant_reply_normalizer import LocalMerchantReplyNormalizer


ReplyStatus = Literal['answered', 'partially-answered', 'evasive', 'not-answered', 'conflicting']


@dataclass(frozen=True)
class MerchantReplyParse:
    reply_status: ReplyStatus
    answered_fields: tuple[str, ...]
    claims: tuple[dict[str, str], ...]
    unresolved_fields: tuple[str, ...]
    conflicts: tuple[str, ...]
    coverage: int
    ambiguity: int
    should_rejudge: bool
    warnings: tuple[str, ...] = ()


class MerchantReplyReasoningProvider(Protocol):
    async def parse_merchant_reply(self, *, field_key: str, raw_text: str, product_evidence: tuple[dict[str, object], ...]) -> MerchantReplyParse: ...


class FakeMerchantReplyReasoningProvider:
    """Deterministic offline provider with a legacy fixture compatibility shell."""

    def __init__(self, normalizer: LocalMerchantReplyNormalizer | None = None) -> None:
        if normalizer is None:
            from guancha_api.providers.merchant_reply_normalizer import LocalMerchantReplyNormalizer

            normalizer = LocalMerchantReplyNormalizer(legacy_compatibility=True)
        self.normalizer = normalizer

    async def parse_merchant_reply(
        self, *, field_key: str, raw_text: str, product_evidence: tuple[dict[str, object], ...]
    ) -> MerchantReplyParse:
        parsed = self.normalizer.normalize(field_key, raw_text, product_evidence)
        if parsed is not None:
            return parsed
        # Fake mode must remain deterministic, but cannot turn an unresolved
        # answer into a claim merely to unblock rejudge.
        return MerchantReplyParse("partially-answered", (), (), (field_key,), (), 0, 1, False)
