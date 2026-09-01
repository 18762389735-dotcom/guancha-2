"""Conservative, local normalization for merchant replies.

The local rules are the product vocabulary boundary.  A semantic provider may
interpret natural language that these rules cannot safely classify, but it may
not expand the canonical values or own conflict/rejudge decisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from guancha_api.providers.merchant_reply import MerchantReplyParse


@dataclass(frozen=True)
class MerchantFieldNormalization:
    meaning: str
    allowed_values: tuple[str, ...] = ()


MERCHANT_FIELD_NORMALIZATION: Final[dict[str, MerchantFieldNormalization]] = {
    "price": MerchantFieldNormalization("实际到手价格"),
    "weight_grams": MerchantFieldNormalization("净含量", ()),
    "tea_subtype": MerchantFieldNormalization("具体茶类"),
    "aroma_style": MerchantFieldNormalization("具体香型", ("qingxiang", "nongxiang", "chenxiang")),
    "roast_level": MerchantFieldNormalization("具体焙火程度", ("light", "medium", "heavy")),
    "season": MerchantFieldNormalization("采摘季节", ("spring", "autumn")),
    "origin_text": MerchantFieldNormalization("具体产地"),
    "sample_available": MerchantFieldNormalization("是否提供小样或试饮装", ("true", "false")),
    "return_policy": MerchantFieldNormalization("试饮或退换规则"),
    "year_or_batch": MerchantFieldNormalization("年份或批次"),
    "process_text": MerchantFieldNormalization("制作工艺说明"),
}


_ROAST_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "light": ("轻火", "轻焙", "浅焙", "低焙"),
    "medium": ("中火", "中焙"),
    "heavy": ("足火", "重火", "重焙", "深焙", "高焙"),
}
_AROMA_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "qingxiang": ("清香型", "清香"),
    "nongxiang": ("浓香型", "浓香"),
    "chenxiang": ("陈香型", "陈香"),
}
_LEGACY_AMBIGUOUS_ROAST_TERMS: Final[dict[str, str]] = {
    # These are retained only for the deterministic Fake provider's historical
    # fixtures.  The default LocalMerchantReplyNormalizer never enables them.
    "轻": "light",
    "浅": "light",
    "重": "heavy",
    "深": "heavy",
    "浓": "heavy",
    "清香": "light",
    "浓香": "heavy",
}
_SEASON_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "spring": ("春茶", "春季采摘"),
    "autumn": ("秋茶", "秋季采摘"),
}
_RETURN_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "seven_day_return": ("七天无理由",),
    "return_supported": ("支持退货",),
    "no_return": ("不退不换",),
}
_ORIGIN_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "anxi": ("安溪",),
    "gande": ("感德",),
    "xiping": ("西坪",),
    "xianghua": ("祥华",),
}
_SUBTYPE_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "tieguanyin": ("铁观音",),
    "huangjingui": ("黄金桂",),
    "benshan": ("本山",),
}
_NOT_ANSWERED_TERMS: Final[tuple[str, ...]] = (
    "不知道", "不清楚", "不确定", "没问过", "不太清楚",
    "没问这个", "没有问这个", "以实物为准",
)
_EVASIVE_TERMS: Final[tuple[str, ...]] = (
    "老客户很多", "很多老客户回购", "大师做的", "品质很好", "高山茶", "卖得很好",
)
_NEGATION_PREFIXES: Final[tuple[str, ...]] = (
    "不", "不是", "没有", "不算", "不会", "不太", "并非",
)


def field_spec(field_key: str) -> MerchantFieldNormalization:
    """Return the bounded prompt/validation specification for a question field."""

    return MERCHANT_FIELD_NORMALIZATION.get(field_key, MerchantFieldNormalization(field_key))


def unresolved_parse(field_key: str, status: str) -> MerchantReplyParse:
    ambiguity = 1 if status in {"partially-answered", "evasive"} else 0
    return MerchantReplyParse(status, (), (), (field_key,), (), 0, ambiguity, False)


def parse_canonical_merchant_value(
    *,
    field_key: str,
    raw_text: str,
    normalized_value: str,
    evidence_text: str | None,
    product_evidence: tuple[dict[str, object], ...],
) -> MerchantReplyParse:
    """Build the existing parse contract and keep conflict detection in Python."""

    conflict = _has_explicit_product_conflict(
        field_key=field_key,
        normalized_value=normalized_value,
        product_evidence=product_evidence,
    )
    claim = {
        "field_key": field_key,
        "raw_text": evidence_text or raw_text,
        "normalized_value": normalized_value,
    }
    return MerchantReplyParse(
        "conflicting" if conflict else "answered",
        (field_key,),
        (claim,),
        (field_key,) if conflict else (),
        (field_key,) if conflict else (),
        1,
        0,
        True,
    )


def _has_explicit_product_conflict(
    *, field_key: str, normalized_value: str, product_evidence: tuple[dict[str, object], ...]
) -> bool:
    merchant_value = normalized_value.strip().casefold()
    for row in product_evidence:
        if row.get("field_name") != field_key or row.get("information_status") != "explicit":
            continue
        source_type = row.get("source_type")
        if source_type not in (None, "product-claim"):
            continue
        existing = str(row.get("normalized_value") or "").strip()
        if existing and existing.casefold() not in {"unknown", merchant_value}:
            return True
    return False


def _contains_unnegated(text: str, term: str) -> bool:
    """Avoid treating a locally matched phrase as a positive assertion when negated."""

    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return False
        prefix = text[max(0, index - 3):index]
        if not any(prefix.endswith(negation) for negation in _NEGATION_PREFIXES):
            return True
        start = index + len(term)


class LocalMerchantReplyNormalizer:
    """Resolve only unambiguous local domain phrases.

    ``legacy_compatibility`` is used solely by the Fake provider so old
    deterministic fixtures remain stable.  Production MiMo uses the default
    conservative mode.
    """

    def __init__(self, *, legacy_compatibility: bool = False) -> None:
        self._legacy_compatibility = legacy_compatibility

    def normalize(
        self,
        field_key: str,
        raw_text: str,
        product_evidence: tuple[dict[str, object], ...],
    ) -> MerchantReplyParse | None:
        text = raw_text.strip()
        if not text:
            return unresolved_parse(field_key, "not-answered")
        if any(token in text for token in _NOT_ANSWERED_TERMS):
            return unresolved_parse(field_key, "not-answered")
        if any(token in text for token in _EVASIVE_TERMS):
            return unresolved_parse(field_key, "evasive")

        value = self._canonical_value(field_key, text)
        if value is None:
            return None
        return parse_canonical_merchant_value(
            field_key=field_key,
            raw_text=text,
            normalized_value=value,
            evidence_text=None,
            product_evidence=product_evidence,
        )

    def _canonical_value(self, field_key: str, text: str) -> str | None:
        if field_key == "roast_level":
            for value, terms in _ROAST_TERMS.items():
                if any(_contains_unnegated(text, term) for term in terms):
                    return value
            if self._legacy_compatibility:
                for term, value in _LEGACY_AMBIGUOUS_ROAST_TERMS.items():
                    if text == term or (term in {"清香", "浓香"} and _contains_unnegated(text, term)):
                        return value
            return None

        if field_key == "season":
            for value, terms in _SEASON_TERMS.items():
                if any(_contains_unnegated(text, term) for term in terms):
                    return value
            return None

        if field_key == "sample_available":
            # Check negative phrases first: negation wins over positive words.
            if any(term in text for term in ("不提供", "没有", "不可以", "不可", "不支持")):
                return "false"
            if (
                any(term in text for term in ("提供小样", "提供试饮装", "支持试饮", "有试饮装", "有小样", "可以试饮", "可以寄小样"))
                or re.search(r"提供\s*\d+(?:\.\d+)?\s*(?:g|克)?\s*(?:小样|试饮装)", text, flags=re.IGNORECASE)
            ):
                return "true"
            if self._legacy_compatibility and (
                text in {"可以", "可试饮", "提供", "支持", "有"} or "有小样" in text
            ):
                return "true"
            return None

        term_map = {
            "aroma_style": _AROMA_TERMS,
            "return_policy": _RETURN_TERMS,
            "origin_text": _ORIGIN_TERMS,
            "tea_subtype": _SUBTYPE_TERMS,
        }.get(field_key)
        if term_map is not None:
            for value, terms in term_map.items():
                if any(_contains_unnegated(text, term) for term in terms):
                    return value
            return None

        patterns = {
            "price": r"(?:￥|¥|价格\s*[:：]?)\s*(\d+(?:\.\d{1,2})?)",
            "weight_grams": r"(\d+(?:\.\d+)?)\s*(?:g|克)",
            "year_or_batch": r"((?:20)?\d{2}(?:年|春|秋)?(?:新茶|批次)?)",
            "process_text": r"(传统工艺|手工制作|炭焙|电焙|轻焙|足火)",
        }
        pattern = patterns.get(field_key)
        if pattern is None:
            return None
        found = re.search(pattern, text, flags=re.IGNORECASE)
        return found.group(1) if found else None
