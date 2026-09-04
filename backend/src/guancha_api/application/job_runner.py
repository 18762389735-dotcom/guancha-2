from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from guancha_api.domain.tieguanyin.demo_fallback import DemoFallbackCatalog
from guancha_api.domain.tieguanyin.fixture_catalog import ExtractionFixture
from guancha_api.infrastructure.storage.interfaces import TemporaryPrivateStorage
from guancha_api.infrastructure.temporary_images import (
    TemporaryImageCleanupError,
    delete_temporary_private_image,
    temporary_image_object_key,
)
from guancha_api.providers.execution import (
    ProviderNetworkExhaustedError,
    ProviderSchemaInvalidError,
    StructuredVisionProvider,
    extract_validated_once,
)
from guancha_api.repositories.postgres import AiCallLog, PostgresPhase2Repository
from guancha_api.schemas.contracts import (
    ErrorCode,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceStrength,
    InformationStatus,
    ProcessingMode,
    VerificationStatus,
)


class FakeEvidencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_name: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    normalized_value: str = Field(min_length=1)
    model_confidence: float = Field(ge=0, le=1)
    information_status: InformationStatus
    source_type: EvidenceSourceType
    verification_status: VerificationStatus
    source_location: str = Field(min_length=1)
    evidence_strength: EvidenceStrength
    source_image_index: int = Field(default=1, ge=1, le=2)


class FakeExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Competition-facing summary fields are deliberately nullable. Evidence,
    # rather than guessed defaults, remains the persisted source of truth.
    product_name: str | None = None
    tea_category: str | None = None
    tea_subtype: str | None = None
    origin: str | None = None
    roast_or_style: str | None = None
    aroma_claims: tuple[str, ...] = ()
    taste_claims: tuple[str, ...] = ()
    season: str | None = None
    year_or_batch: str | None = None
    grade: str | None = None
    weight: str | None = None
    price: str | None = None
    brew_claims: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    evidence: tuple[FakeEvidencePayload, ...] = Field(min_length=1)


class FakeExtractionJobRunner:
    """One claimed job consumes one pre-stored private image by deterministic key."""

    def __init__(
        self,
        repository: PostgresPhase2Repository,
        provider: StructuredVisionProvider,
        storage: TemporaryPrivateStorage,
        *,
        timeout_seconds: float = 90,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.storage = storage
        self.timeout_seconds = timeout_seconds

    async def _fail(self, *, job_id: UUID, error_code: ErrorCode) -> None:
        # Shield the terminal state write from caller cancellation.
        await asyncio.shield(
            self.repository.fail_extraction_job(job_id=job_id, error_code=error_code)
        )

    async def _cleanup(self, *, object_key: str) -> asyncio.CancelledError | None:
        """Complete storage cleanup even if the enclosing worker is cancelled."""
        cleanup_task = asyncio.create_task(
            delete_temporary_private_image(self.storage, object_key=object_key)
        )
        try:
            await asyncio.shield(cleanup_task)
            return None
        except asyncio.CancelledError as cancellation:
            # The delete task retains its own cancellation boundary; wait for its real outcome.
            await asyncio.shield(cleanup_task)
            return cancellation

    async def run(self, *, job_id: UUID, already_claimed: bool = False) -> None:
        if not already_claimed and not await self.repository.claim_job(job_id=job_id):
            return
        job = await self.repository.get_claimed_job(job_id=job_id)
        input_image_ids = job.input_image_ids or (job.candidate_image_id,)
        object_keys = tuple(temporary_image_object_key(image_id) for image_id in input_image_ids)
        legacy_cleanup = not job.input_image_ids or job.input_set_version == 0
        result: FakeExtractionPayload | None = None
        used_fallback = False
        failure: BaseException | None = None

        try:
            # Sanitized private objects stay available until image deletion so
            # a later second screenshot can be submitted in one joint call.
            for object_key in object_keys:
                try:
                    await self.storage.read_private(object_key=object_key)
                except KeyError:
                    if not legacy_cleanup:
                        raise
                    object_keys = (temporary_image_object_key(job_id),)
                    await self.storage.read_private(object_key=object_keys[0])
            async with asyncio.timeout(self.timeout_seconds):
                result = await extract_validated_once(
                    self.provider,
                    image_object_keys=object_keys,
                    validate=lambda payload: self._validate_payload(
                        payload, image_count=len(input_image_ids)
                    ),
                )
        except asyncio.CancelledError as exc:
            failure = exc
        except BaseException as exc:
            failure = exc

        cancellation_during_cleanup: asyncio.CancelledError | None = None
        if legacy_cleanup:
            try:
                cancellation_during_cleanup = await self._cleanup(object_key=object_keys[0])
            except TemporaryImageCleanupError:
                await self._fail(job_id=job_id, error_code=ErrorCode.TEMPORARY_IMAGE_CLEANUP_FAILED)
                if isinstance(failure, asyncio.CancelledError):
                    raise failure
                return
        if cancellation_during_cleanup is not None:
            await self._fail(job_id=job_id, error_code=ErrorCode.WORKER_INTERRUPTED)
            raise cancellation_during_cleanup
        if isinstance(failure, asyncio.CancelledError):
            await self._fail(job_id=job_id, error_code=ErrorCode.WORKER_INTERRUPTED)
            raise failure
        # A deterministic result is allowed only for an explicitly marked
        # sample job.  The marker is persisted in the existing processing_mode
        # column; fixture hashes alone can never authorize this path for a
        # normal user upload.  Storage/read failures are intentionally not
        # treated as provider failures.
        if (
            failure is not None
            and job.processing_mode is ProcessingMode.CACHE_FALLBACK
            and self.provider.provider_name in {"openai", "mimo"}
            and isinstance(
                failure,
                (TimeoutError, ProviderNetworkExhaustedError, ProviderSchemaInvalidError),
            )
        ):
            fallback = await self._fallback_after_provider_failure(
                job_id=job_id,
                candidate_id=job.candidate_id,
                input_image_ids=input_image_ids,
            )
            if fallback is not None:
                result = fallback
                failure = None
                used_fallback = True
        if isinstance(failure, TimeoutError):
            await self._fail(job_id=job_id, error_code=ErrorCode.AI_TIMEOUT)
            return
        if isinstance(failure, ProviderNetworkExhaustedError):
            await self._fail(job_id=job_id, error_code=ErrorCode.AI_PROVIDER_ERROR)
            return
        if isinstance(failure, ProviderSchemaInvalidError):
            await self._fail(job_id=job_id, error_code=ErrorCode.AI_SCHEMA_INVALID)
            return
        if failure is not None:
            await self._fail(job_id=job_id, error_code=ErrorCode.WORKER_INTERRUPTED)
            return

        assert result is not None
        result = self._normalize_screenshot_claim_boundaries(result)
        now = datetime.now(timezone.utc)
        version_id = uuid4()
        evidence = [
            EvidenceItem(
                id=uuid4(),
                extraction_version_id=version_id,
                field_name=self._canonical_field_name(item.field_name),
                raw_text=item.raw_text,
                normalized_value=item.normalized_value,
                model_confidence=item.model_confidence,
                information_status=item.information_status,
                # A screenshot is a product-page claim, not independent
                # verification. Provider output cannot promote it.
                source_type=EvidenceSourceType.PRODUCT_CLAIM,
                verification_status=VerificationStatus.UNVERIFIED,
                source_image_id=input_image_ids[item.source_image_index - 1],
                source_location=item.source_location,
                evidence_strength=item.evidence_strength,
                created_at=now,
            )
            for item in result.evidence
        ]
        # The compact MVP schema persists display fields as evidence too. This
        # keeps the DB contract unchanged while ensuring a real structured
        # response can be rendered without inventing values on the frontend.
        existing_fields = {item.field_name for item in evidence}
        scalar_fields = (
            "product_name", "tea_category", "tea_subtype", "origin",
            "roast_or_style", "season", "year_or_batch", "grade", "weight", "price",
        )
        for field_name in scalar_fields:
            value = getattr(result, field_name)
            persisted_name = self._canonical_field_name(field_name)
            if value is not None and persisted_name not in existing_fields:
                evidence.append(self._display_evidence(version_id, input_image_ids[0], persisted_name, value, now))
        for field_name in ("aroma_claims", "taste_claims", "brew_claims", "risk_flags"):
            for value in getattr(result, field_name):
                evidence.append(self._display_evidence(version_id, input_image_ids[0], field_name.rstrip("s"), value, now))
        try:
            await self.repository.complete_extraction_job(
                job_id=job_id,
                version_id=version_id,
                schema_version="phase3-joint-images-v1",
                evidence_items=tuple(evidence),
                ai_log=AiCallLog(
                    id=uuid4(), analysis_job_id=job_id,
                    provider=self.provider.provider_name,
                    model_identifier=self.provider.model_identifier,
                    processing_mode=(ProcessingMode.CACHE_FALLBACK if used_fallback else self._success_mode()),
                    provider_version="phase8-v1",
                    request_metadata={"prompt_version": f"{self.provider.provider_name}-vision-v1", "schema_version": "phase3-joint-images-v1"},
                ),
                temporary_image_deleted=legacy_cleanup,
            )
        except Exception:
            # The repository transaction has rolled back; no partial version is exposed.
            await self._fail(job_id=job_id, error_code=ErrorCode.WORKER_INTERRUPTED)

    async def _fallback_after_provider_failure(
        self, *, job_id: UUID, candidate_id: UUID, input_image_ids: tuple[UUID, ...]
    ) -> FakeExtractionPayload | None:
        """Load a result only for an exact, committed project demo image set."""
        rows = await self.repository.get_job_input_images(job_id=job_id)
        if len(rows) != len(input_image_ids):
            return None
        fixture = DemoFallbackCatalog().match(
            candidate_id=candidate_id,
            images=(
                (int(row["display_order"]), str(row["sanitized_sha256"]))
                for row in rows
            ),
        )
        return self._fixture_payload(fixture) if fixture is not None else None

    @staticmethod
    def _fixture_payload(fixture: ExtractionFixture) -> FakeExtractionPayload:
        fields = fixture.fields
        evidence = tuple(
            FakeEvidencePayload(
                field_name=str(item["field_name"]),
                raw_text=str(item.get("raw_text") or item["field_name"]),
                normalized_value=str(item.get("normalized_value") or item.get("raw_text") or item["field_name"]),
                model_confidence=0.9,
                information_status=InformationStatus(item["information_status"]),
                source_type=EvidenceSourceType(item["source_type"]),
                verification_status=VerificationStatus(item["verification_status"]),
                source_location=str(item["source_location"]),
                evidence_strength=EvidenceStrength(item["evidence_strength"]),
                source_image_index=1,
            )
            for item in fixture.evidence
        )
        aroma = fields.get("aroma_style")
        aroma_claims = tuple(aroma) if isinstance(aroma, list) else ((str(aroma),) if aroma else ())
        return FakeExtractionPayload(
            product_name=str(fields["tea_type"]) if fields.get("tea_type") else None,
            tea_category="乌龙茶",
            tea_subtype=str(fields["tea_type"]) if fields.get("tea_type") else None,
            origin=str(fields["origin_text"]) if fields.get("origin_text") else None,
            roast_or_style=str(fields["roast_level"]) if fields.get("roast_level") else None,
            aroma_claims=aroma_claims,
            season=str(fields["season"]) if fields.get("season") else None,
            year_or_batch=str(fields["year_or_batch"]) if fields.get("year_or_batch") else None,
            weight=str(fields["weight_grams"]) if fields.get("weight_grams") is not None else None,
            price=str(fields["price"]) if fields.get("price") is not None else None,
            risk_flags=tuple(str(item) for item in (fields.get("missing_fields") or ())),
            evidence=evidence,
        )

    @staticmethod
    def _provider_error_code(failure: BaseException) -> ErrorCode:
        if isinstance(failure, TimeoutError):
            return ErrorCode.AI_TIMEOUT
        if isinstance(failure, ProviderSchemaInvalidError):
            return ErrorCode.AI_SCHEMA_INVALID
        return ErrorCode.AI_PROVIDER_ERROR

    def _success_mode(self) -> ProcessingMode:
        return (
            ProcessingMode.LIVE_AI
            if self.provider.provider_name in {"openai", "mimo"}
            else self.provider.processing_mode
        )

    @staticmethod
    def _normalize_screenshot_claim_boundaries(result: FakeExtractionPayload) -> FakeExtractionPayload:
        """Apply conservative, image-evidence-only handling of ambiguous claims."""
        normalized_evidence: list[FakeEvidencePayload] = []
        for item in result.evidence:
            if item.field_name != "sample_available" or item.normalized_value.casefold() != "true":
                normalized_evidence.append(item)
                continue
            text = item.raw_text.casefold()
            if any(marker in text for marker in ("\\u6682\\u65e0", "\\u4e0d\\u652f\\u6301", "\\u552e\\u7f44", "\\u7f3a\\u8d27")):
                normalized_evidence.append(item.model_copy(update={"normalized_value": "false"}))
            elif any(marker in text for marker in ("\\u54a8\\u8be2\\u5ba2\\u670d", "\\u6b22\\u8fce\\u54c1\\u9274", "\\u8d60")):
                continue
            else:
                normalized_evidence.append(item)
        season_items = [item for item in normalized_evidence if item.field_name == "season"]
        season_values = {
            value
            for item in season_items
            for value in (
                "spring" if ("spring" in f"{item.raw_text} {item.normalized_value}".casefold() or "\\u6625" in item.raw_text) else None,
                "autumn" if ("autumn" in f"{item.raw_text} {item.normalized_value}".casefold() or "\\u79cb" in item.raw_text) else None,
            )
            if value is not None
        }
        if season_values == {"spring", "autumn"}:
            normalized_evidence = [
                item.model_copy(update={"information_status": InformationStatus.CONFLICT})
                if item.field_name == "season" else item
                for item in normalized_evidence
            ]
            risk_flags = tuple(dict.fromkeys((*result.risk_flags, "season_claim_conflict")))
            return result.model_copy(update={
                "season": None, "risk_flags": risk_flags, "evidence": tuple(normalized_evidence)
            })
        return result.model_copy(update={
            "season": next(iter(season_values), None), "evidence": tuple(normalized_evidence)
        })

    @staticmethod
    def _mark_conflicting_season_claims(result: FakeExtractionPayload) -> FakeExtractionPayload:
        """Prefer visible evidence over one model-selected season when claims conflict."""
        season_items = [item for item in result.evidence if item.field_name == "season"]
        values = " ".join(f"{item.raw_text} {item.normalized_value}".lower() for item in season_items)
        has_spring = "spring" in values or "春" in values
        has_autumn = "autumn" in values or "秋" in values
        if not (has_spring and has_autumn):
            return result
        evidence = tuple(
            item.model_copy(update={"information_status": InformationStatus.CONFLICT})
            if item.field_name == "season" else item
            for item in result.evidence
        )
        risk_flags = tuple(dict.fromkeys((*result.risk_flags, "season_claim_conflict")))
        return result.model_copy(
            update={"season": None, "risk_flags": risk_flags, "evidence": evidence}
        )

    @staticmethod
    def _canonical_field_name(field_name: str) -> str:
        return field_name

    @staticmethod
    def _normalize_provider_payload(payload: dict[str, object]) -> dict[str, object]:
        """Apply only the documented MiMo boolean compatibility conversion."""
        evidence = payload.get("evidence")
        if not isinstance(evidence, list):
            return payload
        normalized_evidence: list[object] = []
        changed = False
        for item in evidence:
            if (
                isinstance(item, dict)
                and item.get("field_name") == "sample_available"
                and isinstance(item.get("normalized_value"), bool)
            ):
                item = {
                    **item,
                    "normalized_value": "true" if item["normalized_value"] else "false",
                }
                changed = True
            normalized_evidence.append(item)
        return {**payload, "evidence": normalized_evidence} if changed else payload

    @staticmethod
    def _validate_payload(payload: dict[str, object], *, image_count: int) -> FakeExtractionPayload:
        """Reject an Evidence image reference that cannot exist in this Job."""
        parsed = FakeExtractionPayload.model_validate(
            FakeExtractionJobRunner._normalize_provider_payload(payload)
        )
        if any(item.source_image_index > image_count for item in parsed.evidence):
            raise ValueError("evidence source_image_index is outside the candidate image set")
        return parsed

    @staticmethod
    def _display_evidence(
        version_id: UUID, image_id: UUID, field_name: str, value: str, now: datetime
    ) -> EvidenceItem:
        return EvidenceItem(
            id=uuid4(), extraction_version_id=version_id, field_name=field_name,
            raw_text=value, normalized_value=value, model_confidence=None,
            information_status=InformationStatus.EXPLICIT,
            source_type=EvidenceSourceType.PRODUCT_CLAIM,
            verification_status=VerificationStatus.UNVERIFIED,
            source_image_id=image_id, source_location="structured-provider",
            evidence_strength=EvidenceStrength.MEDIUM, created_at=now,
        )
