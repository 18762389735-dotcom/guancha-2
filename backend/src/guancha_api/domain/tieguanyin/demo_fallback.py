from __future__ import annotations

from hashlib import sha256
from typing import Iterable
from uuid import UUID

from guancha_api.domain.tieguanyin.fixture_catalog import ExtractionFixture, FixtureCatalog


EXTRACTION_CONTRACT_VERSION = "phase3-joint-images-v1"
DOMAIN = "tieguanyin"
PROMPT_VERSION = "openai-responses-v1"


def image_set_fingerprint(images: Iterable[tuple[int, str]]) -> str:
    canonical = "|".join(f"{order}:{digest.lower()}" for order, digest in sorted(images))
    return sha256(canonical.encode("ascii")).hexdigest()


def fixture_cache_key(*, candidate_id: UUID, image_fingerprint: str) -> str:
    material = "|".join((DOMAIN, "prd-fixture-v1", EXTRACTION_CONTRACT_VERSION, str(candidate_id), image_fingerprint))
    return sha256(material.encode("ascii")).hexdigest()


class DemoFallbackCatalog:
    def __init__(self, catalog: FixtureCatalog | None = None) -> None:
        self.catalog = catalog or FixtureCatalog()

    def match(self, *, candidate_id: UUID, images: Iterable[tuple[int, str]]) -> ExtractionFixture | None:
        actual_fingerprint = image_set_fingerprint(images)
        # Keep the candidate in the cache identity even though the manifest
        # match itself is based on the exact ordered sanitized pixels.
        fixture_cache_key(candidate_id=candidate_id, image_fingerprint=actual_fingerprint)
        for item in self.catalog.demo_image_set_fixtures():
            if item.approved_for_cache_fallback and item.image_set_fingerprint == actual_fingerprint:
                return self.catalog.load_extraction(item.extraction_fixture_id)
        return None
