from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FixtureCatalogError(ValueError):
    """A committed demo fixture is missing, malformed, or not approved."""


@dataclass(frozen=True)
class DemoImageSet:
    candidate_fixture_id: str
    image_fixture_ids: tuple[str, ...]
    image_set_fingerprint: str
    extraction_fixture_id: str
    approved_for_cache_fallback: bool


@dataclass(frozen=True)
class DemoImage:
    fixture_id: str
    candidate_fixture_id: str
    display_order: int
    path: str
    sha256: str
    mime_type: str


@dataclass(frozen=True)
class ExtractionFixture:
    fixture_id: str
    fields: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class MerchantReplyFixture:
    fixture_id: str
    merchant_text: str
    expected_claims: tuple[dict[str, Any], ...]


class FixtureCatalog:
    """Read-only access to committed, project-owned demo fixtures."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path(__file__).resolve().parents[5] / "test-fixtures").resolve()

    def demo_image_set_fixtures(self) -> tuple[DemoImageSet, ...]:
        manifest = self._manifest()
        result = []
        for item in manifest.get("demo_image_sets", ()):
            if not isinstance(item, dict):
                raise FixtureCatalogError("demo image set metadata is malformed")
            result.append(DemoImageSet(
                candidate_fixture_id=self._required_text(item, "candidate_fixture_id"),
                image_fixture_ids=tuple(self._required_text_value(value) for value in item.get("image_fixture_ids", ())),
                image_set_fingerprint=self._required_text(item, "image_set_fingerprint"),
                extraction_fixture_id=self._required_text(item, "extraction_fixture_id"),
                approved_for_cache_fallback=item.get("approved_for_cache_fallback") is True,
            ))
        return tuple(result)

    def demo_image_fixtures(self) -> tuple[DemoImage, ...]:
        manifest = self._manifest()
        result = []
        for item in manifest.get("demo_images", ()):
            if not isinstance(item, dict):
                raise FixtureCatalogError("demo image metadata is malformed")
            result.append(DemoImage(
                fixture_id=self._required_text(item, "fixture_id"),
                candidate_fixture_id=self._required_text(item, "candidate_fixture_id"),
                display_order=int(item["display_order"]),
                path=self._required_text(item, "path"),
                sha256=self._required_text(item, "sha256"),
                mime_type=self._required_text(item, "mime_type"),
            ))
        return tuple(result)

    def load_extraction(self, fixture_id: str) -> ExtractionFixture:
        item = self._manifest_entry(fixture_id, "extraction")
        document = self._load_document(item["path"])
        if document.get("fixture_id") != fixture_id or document.get("fixture_kind") != "extraction":
            raise FixtureCatalogError("extraction fixture metadata does not match manifest")
        fields = document.get("fields")
        evidence = document.get("evidence")
        if not isinstance(fields, dict) or not isinstance(evidence, list) or not evidence:
            raise FixtureCatalogError("extraction fixture payload is malformed")
        return ExtractionFixture(fixture_id, dict(fields), tuple(self._mapping(item) for item in evidence))

    def load_merchant_reply(self, fixture_id: str) -> MerchantReplyFixture:
        item = self._manifest_entry(fixture_id, "merchant-reply")
        document = self._load_document(item["path"])
        if document.get("fixture_id") != fixture_id or document.get("fixture_kind") != "merchant-reply":
            raise FixtureCatalogError("merchant reply fixture metadata does not match manifest")
        merchant_text = document.get("merchant_text")
        claims = document.get("expected_claims")
        if not isinstance(merchant_text, str) or not merchant_text.strip() or not isinstance(claims, list):
            raise FixtureCatalogError("merchant reply fixture payload is malformed")
        return MerchantReplyFixture(fixture_id, merchant_text, tuple(self._mapping(item) for item in claims))

    def _manifest(self) -> dict[str, Any]:
        document = self._load_document("manifest.yaml")
        if document.get("schema_version") != "prd-fixture-v1":
            raise FixtureCatalogError("unsupported fixture manifest")
        return document

    def _manifest_entry(self, fixture_id: str, fixture_type: str) -> dict[str, Any]:
        for item in self._manifest().get("fixtures", ()):
            if isinstance(item, dict) and item.get("fixture_id") == fixture_id and item.get("fixture_type") == fixture_type:
                return item
        raise FixtureCatalogError(f"unknown {fixture_type} fixture")

    def _load_document(self, relative_path: str) -> dict[str, Any]:
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents or not path.is_file():
            raise FixtureCatalogError("fixture path is outside the committed fixture root")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FixtureCatalogError("fixture document cannot be read") from error
        if not isinstance(document, dict):
            raise FixtureCatalogError("fixture document is malformed")
        return document

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise FixtureCatalogError("fixture item is malformed")
        return dict(value)

    @staticmethod
    def _required_text(value: dict[str, Any], key: str) -> str:
        result = value.get(key)
        if not isinstance(result, str) or not result.strip():
            raise FixtureCatalogError(f"fixture metadata field {key} is malformed")
        return result

    @staticmethod
    def _required_text_value(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise FixtureCatalogError("fixture metadata list item is malformed")
        return value
