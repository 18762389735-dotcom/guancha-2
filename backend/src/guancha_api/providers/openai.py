"""Opt-in OpenAI Responses adapter for one competition screenshot.

It is imported only when ``GUANCHA_PROVIDER=openai``. Test runs keep using
``FakeProvider`` and never import the SDK or read an API key.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from guancha_api.infrastructure.storage.interfaces import TemporaryPrivateStorage
from guancha_api.providers.fake import ProviderNetworkError, ProviderStructuredOutputError
from guancha_api.schemas.contracts import ProcessingMode


_SYSTEM_INSTRUCTIONS = """Extract factual tea-product claims from one or two screenshots of the same tea product.
Return JSON only. Do not guess missing facts: use null or an empty array.
Every screenshot-derived evidence item must use source_type product-claim and
verification_status unverified. For every evidence item, record the 1-based
source_image_index of the supplied screenshot where that claim is visible.
Use only the supplied frozen enum values."""

_EXTRACTION_SCHEMA: dict[str, Any] = {
    "name": "guancha_candidate_image_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "product_name", "tea_category", "tea_subtype", "origin", "roast_or_style",
            "aroma_claims", "taste_claims", "season", "year_or_batch", "grade", "weight", "price",
            "brew_claims", "risk_flags", "evidence",
        ],
        "properties": {
            **{name: {"type": ["string", "null"]} for name in (
                "product_name", "tea_category", "tea_subtype", "origin", "roast_or_style",
                "season", "year_or_batch", "grade", "weight", "price",
            )},
            **{name: {"type": "array", "items": {"type": "string"}} for name in (
                "aroma_claims", "taste_claims", "brew_claims", "risk_flags",
            )},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "field_name", "raw_text", "normalized_value", "model_confidence",
                        "information_status", "source_type", "verification_status",
                        "source_location", "evidence_strength", "source_image_index",
                    ],
                    "properties": {
                        "field_name": {"type": "string", "minLength": 1},
                        "raw_text": {"type": "string", "minLength": 1},
                        "normalized_value": {"type": "string", "minLength": 1},
                        "model_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "information_status": {"type": "string", "enum": ["explicit", "inferred", "unknown", "conflict"]},
                        "source_type": {"type": "string", "enum": ["product-claim", "merchant-claim", "user-input", "system-inference", "brew-feedback"]},
                        "verification_status": {"type": "string", "enum": ["unverified", "user-confirmed", "system-consistent", "conflicting"]},
                        "source_location": {"type": "string", "minLength": 1},
                        "evidence_strength": {"type": "string", "enum": ["low", "medium", "high"]},
                        "source_image_index": {"type": "integer", "minimum": 1, "maximum": 2},
                    },
                },
            },
        },
    },
}


class OpenAIResponsesProvider:
    """One-call, JSON-schema constrained Responses API adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        storage: TemporaryPrivateStorage,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._storage = storage
        self._client_factory = client_factory

    @property
    def processing_mode(self) -> ProcessingMode:
        # Kept for the established adapter contract.  The Job Runner writes
        # the public audit value ``live-ai`` after this provider succeeds.
        return ProcessingMode.OPENAI_VISION

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_identifier(self) -> str:
        return self._model

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self._api_key)
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Install guancha-api[openai] to use GUANCHA_PROVIDER=openai"
            ) from exc
        return AsyncOpenAI(api_key=self._api_key)

    async def extract(self, *, image_object_keys: tuple[str, ...] | None = None, image_object_key: str | None = None) -> dict[str, Any]:
        try:
            image_object_keys = image_object_keys or ((image_object_key,) if image_object_key is not None else ())
            if not image_object_keys:
                raise ValueError("at least one image object key is required")
            content = []
            for image_object_key in image_object_keys:
                image = await self._storage.read_private(object_key=image_object_key)
                mime_type = "image/png" if image.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
                content.append({
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}",
                })
            client = self._client()
            response = await client.responses.create(
                model=self._model,
                instructions=_SYSTEM_INSTRUCTIONS,
                input=[{
                    "role": "user",
                    "content": content,
                }],
                text={"format": {"type": "json_schema", "name": _EXTRACTION_SCHEMA["name"], "strict": True, "schema": _EXTRACTION_SCHEMA["schema"]}},
            )
            output_text = getattr(response, "output_text", None)
            if not isinstance(output_text, str):
                raise ValueError("Response has no JSON output text")
            parsed = json.loads(output_text)
            if not isinstance(parsed, dict):
                raise ValueError("Structured output must be an object")
            return parsed
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ProviderStructuredOutputError(
                "OpenAI Responses API returned invalid structured output"
            ) from exc
        except Exception as exc:
            raise ProviderNetworkError("OpenAI Responses request failed") from exc

    async def repair_structure(self, *, invalid_response: dict[str, Any]) -> dict[str, Any]:
        # The real call is deliberately single-shot. Strict JSON Schema means
        # malformed output is a failed Job, not a second paid model request.
        return invalid_response
