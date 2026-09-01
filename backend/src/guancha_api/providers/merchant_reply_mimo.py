"""MiMo semantic fallback for one merchant reply.

Local domain rules run first. MiMo only maps a natural-language answer into
the already approved field vocabulary; conflict detection and rejudge remain
Python/application responsibilities.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator, model_validator

from guancha_api.providers.fake import (
    ProviderNetworkError,
    ProviderRateLimitedError,
    ProviderStructuredOutputError,
    ProviderTimeoutError,
)
from guancha_api.providers.merchant_reply import MerchantReplyParse
from guancha_api.providers.merchant_reply_normalizer import (
    LocalMerchantReplyNormalizer,
    MERCHANT_FIELD_NORMALIZATION,
    field_spec,
    parse_canonical_merchant_value,
    unresolved_parse,
)
from guancha_api.providers.mimo import DEFAULT_MIMO_BASE_URL


DEFAULT_MIMO_MERCHANT_REPLY_TIMEOUT_SECONDS = 12.0
MIMO_MERCHANT_REPLY_MAX_COMPLETION_TOKENS = 512
MIMO_MERCHANT_REPLY_STRUCTURED_OUTPUT_MODE = "json_schema"

_MIMO_MERCHANT_REPLY_SYSTEM_INSTRUCTIONS = """You interpret exactly one Chinese merchant reply to one Guancha follow-up question.
Return one structured object and no prose. Never answer general tea questions.
Map only what the merchant directly says about the requested field. If the
reply is ambiguous, promotional, or does not answer that field, mark it
unresolved. Do not invent a value.

The output has exactly these fields:
reply_status, normalized_value, evidence_text.
status is one of answered, partially-answered, evasive, not-answered.
For answered, normalized_value must be one supplied allowed canonical value
when allowed_values is non-empty. When allowed_values is empty, use only a
concise explicit merchant-stated string for that open-text field. In both cases
evidence_text must be a concise supporting quote or paraphrase. For all other
statuses, normalized_value and evidence_text must be null.

Understand natural Chinese paraphrases, not only exact keywords. For example:
“这个火候不会很重，整体偏轻一些” means light when light is allowed;
“焙得比较深，熟香会明显一点” means heavy when heavy is allowed;
“今年春天采的这一批” means spring when spring is allowed;
“这是秋季那批” means autumn when autumn is allowed;
“可以先给你寄一小袋试一下” means true when true is allowed;
“现在没有试饮装” means false when false is allowed.
“这个我也不太清楚” is not-answered; “我们这个很多老客户回购的” is evasive;
“火候还可以” is partially-answered unless it is clear enough to normalize.

Never emit candidate IDs, question IDs, decision data, rankings, scores, risk
decisions, database IDs, or any additional fields."""


class MerchantSemanticNormalization(BaseModel):
    """The only structured data MiMo is allowed to return."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["answered", "partially-answered", "evasive", "not-answered"]
    normalized_value: StrictStr | None = Field(default=None, min_length=1, max_length=500)
    evidence_text: StrictStr | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("normalized_value", "evidence_text")
    @classmethod
    def non_blank_strings(cls, value: StrictStr | None) -> StrictStr | None:
        if value is not None and not value.strip():
            raise ValueError("text values must not be blank")
        return value

    @model_validator(mode="after")
    def status_value_contract(self) -> "MerchantSemanticNormalization":
        if self.status == "answered" and self.normalized_value is None:
            raise ValueError("answered replies require normalized_value")
        if self.status != "answered" and (self.normalized_value is not None or self.evidence_text is not None):
            raise ValueError("unresolved replies cannot contain a claim")
        return self


# Keep the old import name available to focused tests and downstream callers.
MiMoMerchantReplyOutput = MerchantSemanticNormalization


class MiMoMerchantReplyReasoningProvider:
    """Local-first merchant parser with one bounded Instructor/MiMo fallback."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_MIMO_BASE_URL,
        timeout_seconds: float = DEFAULT_MIMO_MERCHANT_REPLY_TIMEOUT_SECONDS,
        client_factory: Callable[[str, str], Any] | None = None,
        instructor_client_factory: Callable[[Any], Any] | None = None,
        local_normalizer: LocalMerchantReplyNormalizer | None = None,
    ) -> None:
        if not api_key or not model:
            raise ValueError("MiMo merchant reply provider requires API configuration")
        if timeout_seconds <= 0 or timeout_seconds > DEFAULT_MIMO_MERCHANT_REPLY_TIMEOUT_SECONDS:
            raise ValueError("MiMo merchant reply timeout must be between 0 and 12 seconds")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory
        self._instructor_client_factory = instructor_client_factory
        self._local_normalizer = local_normalizer or LocalMerchantReplyNormalizer()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def structured_output_mode(self) -> str:
        return MIMO_MERCHANT_REPLY_STRUCTURED_OUTPUT_MODE

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self._api_key, self._base_url)
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError("Install guancha-api[openai] to use MiMo merchant reply reasoning") from None
        return AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, timeout=self._timeout_seconds)

    def _instructor_client(self) -> Any:
        client = self._client()
        if self._instructor_client_factory is not None:
            return self._instructor_client_factory(client)
        try:
            from instructor import Mode
            from instructor.v2.providers.openai import from_openai
        except ImportError:
            raise RuntimeError("Install guancha-api[openai] to use MiMo merchant reply reasoning") from None
        return from_openai(client, mode=Mode.JSON_SCHEMA)

    async def _request(self, *, field_key: str, raw_text: str) -> MerchantSemanticNormalization:
        spec = field_spec(field_key)
        request_payload = {
            "field_key": field_key,
            "field_meaning": spec.meaning,
            "allowed_values": list(spec.allowed_values),
            "merchant_raw_text": raw_text,
        }
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._instructor_client().create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _MIMO_MERCHANT_REPLY_SYSTEM_INSTRUCTIONS},
                        {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
                    ],
                    response_model=MerchantSemanticNormalization,
                    max_retries=0,
                    max_completion_tokens=MIMO_MERCHANT_REPLY_MAX_COMPLETION_TOKENS,
                    extra_body={"thinking": {"type": "disabled"}},
                )
        except ProviderStructuredOutputError:
            raise
        except TimeoutError:
            raise ProviderTimeoutError("MiMo merchant reply request timed out") from None
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code in {408, 504}:
                raise ProviderTimeoutError("MiMo merchant reply request timed out") from None
            if status_code == 429:
                raise ProviderRateLimitedError("MiMo merchant reply rate limit exceeded") from None
            # Instructor can surface a Pydantic validation exception or its
            # retry wrapper when the provider violates the closed model.
            if isinstance(exc, (ValidationError, json.JSONDecodeError)) or exc.__class__.__name__ in {"InstructorRetryException", "ValidationError"}:
                raise ProviderStructuredOutputError("MiMo merchant reply returned invalid structured output") from None
            raise ProviderNetworkError("MiMo merchant reply request failed") from None

        try:
            if isinstance(response, MerchantSemanticNormalization):
                return response
            if isinstance(response, BaseModel):
                response = response.model_dump(mode="python")
            return MerchantSemanticNormalization.model_validate(response)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStructuredOutputError("MiMo merchant reply returned invalid structured output") from None

    async def parse_merchant_reply(
        self,
        *,
        field_key: str,
        raw_text: str,
        product_evidence: tuple[dict[str, object], ...],
    ) -> MerchantReplyParse:
        if field_key not in MERCHANT_FIELD_NORMALIZATION:
            raise ProviderStructuredOutputError("MiMo merchant reply received an unsupported field")
        local_result = self._local_normalizer.normalize(field_key, raw_text, product_evidence)
        if local_result is not None:
            return local_result

        result = await self._request(field_key=field_key, raw_text=raw_text)
        if result.status != "answered":
            return unresolved_parse(field_key, result.status)

        assert result.normalized_value is not None
        allowed_values = field_spec(field_key).allowed_values
        if allowed_values and result.normalized_value not in allowed_values:
            # An LLM value outside the local ontology is unresolved, never a
            # new business value and never a reason to mutate the rules engine.
            return unresolved_parse(field_key, "partially-answered")
        return parse_canonical_merchant_value(
            field_key=field_key,
            raw_text=raw_text,
            normalized_value=result.normalized_value,
            evidence_text=result.evidence_text,
            product_evidence=product_evidence,
        )
