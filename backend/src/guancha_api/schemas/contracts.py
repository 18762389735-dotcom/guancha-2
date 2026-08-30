from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class JobState(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class JobStage(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    PROVIDER = "provider"
    PERSISTING = "persisting"
    CLEANING = "cleaning"
    COMPLETED = "completed"
    FAILED = "failed"


class CandidateImageStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


class CandidateStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class ExtractionStatus(StrEnum):
    EMPTY = "empty"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class ActionBucket(StrEnum):
    CURRENTLY_SELECTABLE = "currently-selectable"
    ASK_BEFORE_BUYING = "ask-before-buying"
    SAMPLE_FIRST = "sample-first"
    NOT_RECOMMENDED_NOW = "not-recommended-now"
    INSUFFICIENT_INFORMATION = "insufficient-information"


class ReplyAssessment(StrEnum):
    ANSWERED = "answered"
    PARTIAL = "partial"
    EVASIVE = "evasive"
    NOT_ANSWERED = "not-answered"
    CONFLICTING = "conflicting"


class ProcessingMode(StrEnum):
    FAKE_PROVIDER = "fake-provider"
    OPENAI_VISION = "openai-vision"
    TEST_FIXTURE = "test-fixture"
    # ``live-ai`` is intentionally a presentation-neutral audit marker.  It
    # means a configured external vision provider returned a valid payload;
    # it is never assigned to FakeProvider output.
    LIVE_AI = "live-ai"
    # A narrowly approved, SHA-256 exact demo fixture used only after a real
    # provider failure.  It must remain distinguishable in every job/log row.
    CACHE_FALLBACK = "cache-fallback"


class InformationStatus(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class EvidenceSourceType(StrEnum):
    PRODUCT_CLAIM = "product-claim"
    MERCHANT_CLAIM = "merchant-claim"
    USER_INPUT = "user-input"
    SYSTEM_INFERENCE = "system-inference"
    BREW_FEEDBACK = "brew-feedback"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    USER_CONFIRMED = "user-confirmed"
    SYSTEM_CONSISTENT = "system-consistent"
    CONFLICTING = "conflicting"


class EvidenceStrength(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PreferenceTargetType(StrEnum):
    TEA_STYLE = "tea-style"
    AROMA = "aroma"
    ROAST = "roast"
    BITTERNESS = "bitterness"
    ASTRINGENCY = "astringency"
    SWEETNESS = "sweetness"
    MOUTHFEEL = "mouthfeel"
    AFTERTASTE = "aftertaste"
    SALIVATION = "salivation"
    FINISH = "finish"


class PreferencePolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class BrewAdjustmentParameter(StrEnum):
    WATER_TEMPERATURE = "water_temperature"
    STEEP_TIME = "steep_time"
    TEA_AMOUNT = "tea_amount"
    WATER_VOLUME = "water_volume"


class BrewAdjustmentDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    MISSING_CLIENT_ID = "missing_client_id"
    INVALID_CLIENT_ID = "invalid_client_id"
    MISSING_IDEMPOTENCY_KEY = "missing_idempotency_key"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"
    RESOURCE_NOT_OWNED = "resource_not_owned"
    SELECTION_SESSION_NOT_FOUND = "selection_session_not_found"
    CANDIDATE_NOT_FOUND = "candidate_not_found"
    CANDIDATE_IMAGE_NOT_FOUND = "candidate_image_not_found"
    CANDIDATE_LIMIT_EXCEEDED = "candidate_limit_exceeded"
    CANDIDATE_IMAGE_LIMIT_EXCEEDED = "candidate_image_limit_exceeded"
    INVALID_IMAGE_TYPE = "invalid_image_type"
    IMAGE_TOO_LARGE = "image_too_large"
    UNSAFE_OR_CORRUPT_IMAGE = "unsafe_or_corrupt_image"
    IMAGE_TOO_LOW_RESOLUTION = "image_too_low_resolution"
    IMAGE_PIXEL_LIMIT_EXCEEDED = "image_pixel_limit_exceeded"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    CANDIDATE_EXTRACTION_IN_PROGRESS = "candidate_extraction_in_progress"
    CANDIDATE_EXTRACTION_NOT_RETRYABLE = "candidate_extraction_not_retryable"
    AI_TIMEOUT = "ai_timeout"
    AI_PROVIDER_ERROR = "ai_provider_error"
    AI_SCHEMA_INVALID = "ai_schema_invalid"
    WORKER_INTERRUPTED = "worker_interrupted"
    TEMPORARY_IMAGE_CLEANUP_FAILED = "temporary_image_cleanup_failed"
    CURRENT_DECISION_NOT_AVAILABLE = "current_decision_not_available"
    DECISION_STALE = "decision_stale"
    QUESTIONS_NOT_AVAILABLE = "questions_not_available"
    MERCHANT_REPLY_NOT_FOUND = "merchant_reply_not_found"
    QUESTION_NOT_AVAILABLE = "question_not_available"
    DECISION_DELTA_NOT_FOUND = "decision_delta_not_found"
    BREW_FEEDBACK_INVALID = "brew_feedback_invalid"
    BREW_SESSION_NOT_FOUND = "brew_session_not_found"
    TEA_RECORD_NOT_FOUND = "tea_record_not_found"
    INSUFFICIENT_FEEDBACK = "insufficient_feedback"
    FEEDBACK_ANALYSIS_FAILED = "feedback_analysis_failed"
    FEEDBACK_DUPLICATE = "feedback_duplicate"
    AUTHENTICATION_REQUIRED = "authentication_required"
    INVALID_ACCESS_TOKEN = "invalid_access_token"
    AUTHENTICATION_SERVICE_UNAVAILABLE = "authentication_service_unavailable"
    AUTH_NOT_CONFIGURED = "auth_not_configured"
    CONTRACT_NOT_IMPLEMENTED = "contract_not_implemented"
    INTERNAL_ERROR = "internal_error"


class PollIntervalsSeconds(ContractModel):
    initial: int = Field(ge=1)
    after_initial: int = Field(ge=1)
    background: int = Field(ge=1)


class PublicAuthConfig(ContractModel):
    required: bool = False
    configured: bool = False
    provider: str = "cloudbase"
    env_id: str | None = None
    region: str = "ap-shanghai"
    publishable_key: str | None = None


class PublicConfig(ContractModel):
    candidate_limit: int = Field(5, ge=1, le=5, description="Product absolute maximum")
    candidate_image_limit: int = Field(2, ge=1, le=2, description="Product absolute maximum")
    phase2_candidate_limit: int = Field(5, ge=1, le=5, description="Enabled candidate maximum")
    phase2_candidate_image_limit: int = Field(2, ge=1, le=2, description="Enabled image maximum per candidate")
    allowed_image_mime_types: tuple[str, ...] = ("image/jpeg", "image/png")
    max_image_bytes: int = Field(5_242_880, ge=1)
    poll_intervals_seconds: PollIntervalsSeconds = PollIntervalsSeconds(
        initial=1, after_initial=2, background=5
    )
    max_concurrent_candidate_extractions: int = Field(3, ge=1)
    auth: PublicAuthConfig = PublicAuthConfig()


class CurrentUserResponse(ContractModel):
    id: UUID
    authenticated: bool = True
    created_at: datetime


class SelectionNeedInput(ContractModel):
    """Raw user input only; parsing and evaluation are intentionally deferred."""

    taste_text: str | None = Field(default=None, max_length=500)
    purpose_text: str | None = Field(default=None, max_length=120)
    budget_text: str | None = Field(default=None, max_length=120)
    risk_attitude_text: str | None = Field(default=None, max_length=120)


class SelectionSession(ContractModel):
    id: UUID
    anonymous_client_id: UUID | None
    need: SelectionNeedInput
    evidence_version_id: UUID | None = None
    context_version_id: UUID | None = None
    current_decision_version_id: UUID | None = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class Candidate(ContractModel):
    id: UUID
    selection_session_id: UUID
    display_label: str = Field(min_length=1, max_length=32)
    display_name: str | None = Field(default=None, max_length=200)
    position: int = Field(ge=1, le=5)
    status: CandidateStatus = CandidateStatus.ACTIVE
    current_extraction_version_id: UUID | None = None
    created_at: datetime


class ImageInput(ContractModel):
    content_type: str
    size_bytes: int = Field(ge=1, le=5_242_880)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")

    @model_validator(mode="after")
    def validate_allowed_content_type(self) -> "ImageInput":
        if self.content_type not in {"image/jpeg", "image/png"}:
            raise ValueError("content_type must be image/jpeg or image/png")
        return self


class CandidateImage(ContractModel):
    id: UUID
    candidate_id: UUID
    content_type: str
    size_bytes: int
    sha256: str
    status: CandidateImageStatus
    current_job_id: UUID | None = None
    error_code: str | None = None
    created_at: datetime


class JobStatus(ContractModel):
    id: UUID
    status: JobState
    progress: int | None = Field(default=None, ge=0, le=100)
    error_code: str | None = None
    result_resource: str | None = None
    result_version_id: UUID | None = None
    processing_mode: ProcessingMode | None = None


class EvidenceReference(ContractModel):
    information_status: InformationStatus
    source_type: EvidenceSourceType
    verification_status: VerificationStatus
    source_image_id: UUID | None = None
    source_location: str | None = Field(default=None, max_length=200)
    evidence_strength: EvidenceStrength


class CreateSelectionSessionRequest(ContractModel):
    need: SelectionNeedInput = SelectionNeedInput()
    # A browser-local snapshot only.  It is intentionally not promoted to a
    # server-side preference profile or persisted as a product fact.
    recent_preference_evidence: tuple[dict[str, object], ...] = ()


class UpdateSelectionNeedRequest(ContractModel):
    """Explicit replacement of the raw need snapshot for one session."""

    need: SelectionNeedInput
    recent_preference_evidence: tuple[dict[str, object], ...] = ()


class CreateCandidateRequest(ContractModel):
    display_label: str = Field(default="A", min_length=1, max_length=32)
    display_name: str | None = Field(default=None, max_length=200)


class CandidateImageMetadata(ContractModel):
    id: UUID
    candidate_id: UUID
    content_type: str
    size_bytes: int = Field(ge=1, le=5_242_880)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    display_order: int = Field(ge=1, le=2)
    status: CandidateImageStatus
    current_job_id: UUID | None = None
    error_code: ErrorCode | None = None
    created_at: datetime


class AnalysisJobResponse(ContractModel):
    id: UUID
    candidate_id: UUID
    candidate_image_id: UUID
    status: JobState
    stage: JobStage | None
    attempt: int = Field(ge=1, le=2)
    error_code: ErrorCode | None
    extraction_version_id: UUID | None
    decision_version_id: UUID | None = None
    decision_delta_id: UUID | None = None
    processing_mode: ProcessingMode | None = None
    created_at: datetime
    updated_at: datetime


class UploadCandidateImageResponse(ContractModel):
    """Atomic upload result: image metadata and its first extraction Job."""

    image: CandidateImageMetadata
    extraction_job: AnalysisJobResponse


class EvidenceItem(ContractModel):
    id: UUID
    extraction_version_id: UUID
    field_name: str = Field(min_length=1, max_length=100)
    raw_text: str | None = Field(default=None, max_length=4000)
    normalized_value: str | None = Field(default=None, max_length=2000)
    model_confidence: float | None = Field(default=None, ge=0, le=1)
    information_status: InformationStatus
    source_type: EvidenceSourceType
    verification_status: VerificationStatus
    source_image_id: UUID
    source_location: str = Field(min_length=1, max_length=200)
    evidence_strength: EvidenceStrength
    created_at: datetime


class ExtractionVersionResponse(ContractModel):
    id: UUID
    candidate_id: UUID
    source_image_id: UUID
    source_image_ids: tuple[UUID, ...] = Field(min_length=1, max_length=2)
    status: ExtractionStatus
    schema_version: str = Field(min_length=1, max_length=40)
    evidence_items: tuple[EvidenceItem, ...] = ()
    created_at: datetime


class ExtractionVersion(ContractModel):
    """Immutable extraction snapshot. No update DTO exists by design."""

    id: UUID
    candidate_id: UUID
    source_image_ids: tuple[UUID, ...] = Field(min_length=1, max_length=2)
    extraction_status: ExtractionStatus
    evidence: tuple[EvidenceReference, ...] = ()
    created_at: datetime


class DecisionVersion(ContractModel):
    id: UUID
    selection_session_id: UUID
    anonymous_client_id: UUID | None
    version: int = Field(ge=1)
    status: ExtractionStatus
    rule_version: str
    top_candidate_id: UUID | None = None
    created_at: datetime


class CandidateDecision(ContractModel):
    id: UUID
    decision_version_id: UUID
    candidate_id: UUID
    extraction_version_id: UUID
    action_bucket: ActionBucket
    rank_within_bucket: int = Field(ge=1)
    overall_order: int = Field(ge=1)
    reasons: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    missing_critical_fields: tuple[str, ...] = ()
    score_components: dict[str, int] = {}
    created_at: datetime


class DecisionVersionResponse(DecisionVersion):
    candidate_decisions: tuple[CandidateDecision, ...] = ()


class FollowupQuestion(ContractModel):
    id: UUID
    decision_version_id: UUID
    selection_session_id: UUID
    candidate_id: UUID
    field_key: str = Field(min_length=1, max_length=64)
    question_text: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    affected_decision: tuple[str, ...] = ()
    answer_branches: tuple[str, ...] = ()
    priority: int = Field(ge=0, le=4)
    status: str = Field(pattern=r"^completed$")
    created_at: datetime


class CreateMerchantReplyRequest(ContractModel):
    decision_version_id: UUID
    followup_question_id: UUID
    raw_text: str = Field(min_length=1, max_length=4000)


class MerchantReply(ContractModel):
    id: UUID
    selection_session_id: UUID
    decision_version_id: UUID
    followup_question_id: UUID
    candidate_id: UUID
    raw_text: str
    status: str
    processing_status: str
    parse_status: str | None = None
    created_at: datetime


class MerchantClaim(ContractModel):
    id: UUID
    merchant_reply_id: UUID
    candidate_id: UUID
    field_key: str
    raw_text: str
    normalized_value: str | None = None
    information_status: InformationStatus
    source_type: EvidenceSourceType
    verification_status: VerificationStatus
    evidence_strength: EvidenceStrength
    created_at: datetime


class DecisionDelta(ContractModel):
    id: UUID
    selection_session_id: UUID
    old_decision_version_id: UUID
    new_decision_version_id: UUID
    merchant_reply_id: UUID
    merchant_reply_ids: tuple[UUID, ...] = ()
    added_facts: tuple[str, ...] = ()
    updated_fields: tuple[str, ...] = ()
    unresolved_fields: tuple[str, ...] = ()
    resolved_risks: tuple[str, ...] = ()
    added_risks: tuple[str, ...] = ()
    ranking_changed: bool
    action_tier_changed: bool
    old_top_candidate_id: UUID | None = None
    new_top_candidate_id: UUID | None = None
    explanation: str
    created_at: datetime


class CreateRejudgeRequest(ContractModel):
    """Trigger one aggregate rejudge for the session's current decision.

    The server chooses an internal audit anchor after validating the complete
    reply set; callers never select a single reply as the rejudge input.
    """
    pass


class LegacyMerchantReplyBridge(ContractModel):
    id: UUID
    session_id: UUID
    candidate_id: UUID
    decision_version_id: UUID
    question_ids: tuple[UUID, ...] = Field(min_length=1, max_length=3)
    raw_reply: str = Field(min_length=1, max_length=4000)
    assessment: ReplyAssessment | None = None
    created_at: datetime


class BrewFeedback(ContractModel):
    """Bridge DTO only. P0 tea stock and brewing diary remain local-first."""

    tea_stock_item_id: str = Field(min_length=1, max_length=120)
    brew_session_id: str = Field(min_length=1, max_length=120)
    preference_evidence_summary: str | None = Field(default=None, max_length=500)


class BrewParameters(ContractModel):
    tea_amount: float | None = Field(default=None, gt=0, le=30)
    water_volume: float | None = Field(default=None, gt=0, le=1000)
    water_temperature: float | None = Field(default=None, ge=40, le=100)
    steep_time: float | None = Field(default=None, gt=0, le=300)
    infusion_number: int | None = Field(default=None, ge=1, le=20)


class StructuredBrewFeedback(ContractModel):
    aroma: str | None = Field(default=None, max_length=120)
    bitterness: str | None = Field(default=None, max_length=120)
    astringency: str | None = Field(default=None, max_length=120)
    sweetness: str | None = Field(default=None, max_length=120)
    mouthfeel: str | None = Field(default=None, max_length=120)
    aftertaste: str | None = Field(default=None, max_length=120)
    salivation: str | None = Field(default=None, max_length=120)
    finish: str | None = Field(default=None, max_length=120)
    overall_rating: int | None = Field(default=None, ge=1, le=5)
    free_text_note: str | None = Field(default=None, max_length=500)


class BrewFeedbackAnalysisRequest(ContractModel):
    brew_session_id: str = Field(min_length=1, max_length=120)
    tea_record_id: str = Field(min_length=1, max_length=120)
    candidate_id: UUID | None = None
    extraction_version_id: UUID | None = None
    system_recommended_parameters: BrewParameters
    actual_brew_parameters: BrewParameters
    structured_feedback: StructuredBrewFeedback
    taste_card_snapshot: dict[str, object] = Field(default_factory=dict)
    recent_preference_evidence: tuple[dict[str, object], ...] = ()
    client_feedback_id: UUID


class PreferenceEvidence(ContractModel):
    id: UUID
    target_type: PreferenceTargetType
    target_value: str
    polarity: PreferencePolarity
    confidence: str = Field(pattern=r"^low$")
    issue_source: str = Field(pattern=r"^(tea|brewing|uncertain)$")
    source_brew_session_id: str
    created_at: datetime


class BrewAdjustment(ContractModel):
    parameter: BrewAdjustmentParameter | None = None
    direction: BrewAdjustmentDirection | None = None
    suggested_delta: float | None = None
    reason: str
    confidence: str = Field(pattern=r"^low$")

    @model_validator(mode="after")
    def validate_single_complete_adjustment(self) -> "BrewAdjustment":
        values = (self.parameter, self.direction, self.suggested_delta)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError("an adjustment must include parameter, direction, and suggested_delta together")
        return self


class BrewFeedbackAnalysisResponse(ContractModel):
    attribution: str = Field(pattern=r"^(tea|brewing|uncertain)$")
    attribution_reasons: tuple[str, ...] = Field(max_length=3)
    next_brew_adjustment: BrewAdjustment
    preference_evidence: tuple[PreferenceEvidence, ...] = Field(default=(), max_length=3)
    impact_explanation: str
    warnings: tuple[str, ...] = ()
