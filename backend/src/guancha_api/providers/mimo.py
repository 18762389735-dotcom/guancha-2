"""Opt-in Xiaomi MiMo Vision adapter using its OpenAI-compatible API.

MiMo documents image input for ``mimo-v2.5`` and JSON-object output, rather
than JSON Schema enforcement.  This adapter therefore asks for JSON mode and
leaves the existing frozen Pydantic validation as the sole acceptance gate.
Invalid output is a failed extraction; it is never repaired or guessed.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

from guancha_api.infrastructure.storage.interfaces import TemporaryPrivateStorage
from guancha_api.providers.fake import (
    ProviderNetworkError,
    ProviderRateLimitedError,
    ProviderStructuredOutputError,
    ProviderTimeoutError,
)
from guancha_api.providers.openai import _EXTRACTION_SCHEMA
from guancha_api.schemas.contracts import ProcessingMode


DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
_MIMO_MAX_IMAGE_EDGE = 1536

# Keep this local to MiMo: it is a narrow extraction guide for the next
# single-image evaluation pass, not a change to the frozen shared schema.
_MIMO_SYSTEM_INSTRUCTIONS = """Extract only visible tea-product claims from one screenshot.
Return one JSON object and no prose. Do not infer facts that are absent.
For every evidence item, field_name, raw_text, normalized_value, and
source_location must be non-empty strings. model_confidence must be a number
from 0 to 1. source_image_index must be an integer from 1 through the number
of supplied images. When visible product claims exist, evidence must contain
at least one item. Use null only for nullable top-level scalar fields; do not
use null for required evidence fields. Never output a boolean, number, or
object for normalized_value.
Use tea_category for a broad class such as 乌龙茶 and tea_subtype for the named tea,
such as 铁观音; never put 清香型 or 浓香型 into tea_subtype. Preserve explicit
清香型/浓香型 in roast_or_style or aroma_claims. Preserve only explicitly printed
春茶 or 秋茶 in season; preserve a printed year or batch in year_or_batch. A year
or 新茶 alone is not a season. If the
screenshot explicitly offers 小样、试饮、品鉴或体验装, include an evidence item with
field_name sample_available and normalized_value "true". Use the string
"false" when the visible claim explicitly says it is unavailable. Do not mark it true for
暂无/不支持/售罄, for a gift included only with a full purchase, for 咨询客服, or
for a generic 欢迎品鉴 statement; otherwise omit that evidence.
If visible claims conflict (for example both 春茶 and 秋季采摘), do not choose one
season: set season to null, include the conflicting evidence using field_name
season, and add
the risk flag season_claim_conflict.
Every screenshot evidence item must remain product-claim and unverified."""


def _image_for_mimo(image: bytes) -> tuple[bytes, str]:
    """Create an ephemeral smaller model input without changing stored media.

    Upload sanitization and private storage retain the authoritative image.  This
    provider-only derivative limits the visual-token load for tall product-page
    screenshots, while keeping all processing local and private.
    """
    default_mime = "image/png" if image.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
    try:
        with Image.open(BytesIO(image)) as opened:
            if max(opened.size) <= _MIMO_MAX_IMAGE_EDGE:
                return image, default_mime
            rendered = opened.convert("RGB")
            rendered.thumbnail(
                (_MIMO_MAX_IMAGE_EDGE, _MIMO_MAX_IMAGE_EDGE), Image.Resampling.LANCZOS
            )
            output = BytesIO()
            rendered.save(output, format="JPEG", quality=88, optimize=True)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError):
        # Storage is populated only after upload sanitization in the production
        # path.  Preserve the stored bytes for defensive compatibility rather
        # than making the adapter a second image-validation boundary.
        return image, default_mime


class MiMoVisionProvider:
    """One-call MiMo adapter that preserves the shared extraction contract."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        storage: TemporaryPrivateStorage,
        base_url: str = DEFAULT_MIMO_BASE_URL,
        client_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._storage = storage
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory

    @property
    def processing_mode(self) -> ProcessingMode:
        return ProcessingMode.OPENAI_VISION

    @property
    def provider_name(self) -> str:
        return "mimo"

    @property
    def model_identifier(self) -> str:
        return self._model

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self._api_key, self._base_url)
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Install guancha-api[openai] to use GUANCHA_PROVIDER=mimo"
            ) from exc
        return AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

    async def extract(
        self,
        *,
        image_object_keys: tuple[str, ...] | None = None,
        image_object_key: str | None = None,
    ) -> dict[str, Any]:
        try:
            keys = image_object_keys or (
                (image_object_key,) if image_object_key is not None else ()
            )
            if not keys:
                raise ValueError("at least one image object key is required")

            content: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": (
                        "Analyze the supplied tea-product screenshot(s). Return one JSON "
                        "object only, matching this schema exactly. Do not infer missing "
                        "facts; use null or empty arrays. Each evidence item must include "
                        "the 1-based source_image_index where its claim is visible. Schema: "
                        f"{json.dumps(_EXTRACTION_SCHEMA['schema'], ensure_ascii=False)}"
                    ),
                }
            ]
            for key in keys:
                image = await self._storage.read_private(object_key=key)
                model_image, mime_type = _image_for_mimo(image)
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{mime_type};base64,"
                                f"{base64.b64encode(model_image).decode('ascii')}"
                            )
                        },
                    }
                )

            response = await self._client().chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _MIMO_SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": content},
                ],
                response_format={"type": "json_object"},
                # Bound output generation.  The official minimal example uses
                # 1024 tokens, but Guancha's strict extraction contains an
                # Evidence array and needs room for a valid complete object.
                max_completion_tokens=4096,
                # MiMo enables deep thinking by default.  Screenshot extraction
                # is a one-pass, schema-bound task: hidden reasoning consumes
                # the same completion budget and can delay the final JSON.
                extra_body={"thinking": {"type": "disabled"}},
            )
            choices = getattr(response, "choices", None)
            message = choices[0].message if isinstance(choices, list) and choices else None
            finish_reason = getattr(choices[0], "finish_reason", None) if isinstance(choices, list) and choices else None
            if finish_reason not in (None, "stop"):
                raise ValueError("MiMo response did not finish normally")
            output_text = getattr(message, "content", None)
            if not isinstance(output_text, str):
                raise ValueError("MiMo response has no JSON content")
            parsed = json.loads(output_text)
            if not isinstance(parsed, dict):
                raise ValueError("MiMo JSON output must be an object")
            return parsed
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise ProviderStructuredOutputError(
                "MiMo API returned invalid structured output"
            ) from exc
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if isinstance(exc, TimeoutError) or status_code in {408, 504}:
                raise ProviderTimeoutError("MiMo API request timed out") from exc
            if status_code == 429:
                raise ProviderRateLimitedError("MiMo API rate limit exceeded") from exc
            raise ProviderNetworkError("MiMo API request failed") from exc

    async def repair_structure(self, *, invalid_response: dict[str, Any]) -> dict[str, Any]:
        # A schema miss is a failed job.  Do not spend a second paid request to
        # repair model output, and never synthesize missing product claims.
        return invalid_response
