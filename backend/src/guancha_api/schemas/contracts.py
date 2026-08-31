from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    PREFERENCES_REVISION_CONFLICT = "preferences_revision_conflict"
    WAREHOUSE_REVISION_CONFLICT = "warehouse_revision_conflict"
    BREW_JOURNAL_REVISION_CONFLICT = "brew_journal_revision_conflict"
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


_AUTH_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_auth_email(value: str) -> str:
    normalized = value.strip().lower()
    if not _AUTH_EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("invalid email")
    return normalized


def _validate_auth_password(value: str) -> str:
    if not re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d).{8,32}", value):
        raise ValueError("invalid password")
    return value


class RegisterStartRequest(ContractModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _validate_auth_email(value)


class RegisterStartResponse(ContractModel):
    verification_id: str = Field(min_length=1, max_length=8192)
    expires_in: int = Field(gt=0)


class RegisterCompleteRequest(ContractModel):
    email: str = Field(min_length=3, max_length=320)
    verification_id: str = Field(min_length=1, max_length=8192)
    verification_code: str = Field(min_length=4, max_length=12, pattern=r"^\d+$")
    password: str = Field(min_length=8, max_length=32)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _validate_auth_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_auth_password(value)


class SignInRequest(ContractModel):
    username: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return _validate_auth_email(value)

class AuthTokenResponse(ContractModel):
    access_token: str = Field(min_length=1, max_length=8192)
    expires_in: int = Field(gt=0)
    sub: str = Field(min_length=1, max_length=512)
    token_type: str = "Bearer"


# These exact values mirror the existing frontend/stores.js whitelist.  The
# server owns the same boundary so a client cannot smuggle unrelated state into
# the durable profile JSON document.
_PREFERENCE_O1_OPTIONS = {
    "tea": frozenset({"绿茶", "花香茶", "乌龙茶", "红茶", "焙火茶", "陈香茶", "奶茶 / 果茶"}),
    "coffee": frozenset({"美式 / 黑咖啡", "拿铁", "冷萃", "浅烘手冲", "深烘咖啡"}),
    "milk": frozenset({"纯牛奶", "酸奶", "豆浆", "燕麦奶", "椰奶"}),
    "juice": frozenset({"柑橘类果汁", "苹果 / 梨汁", "桃子 / 荔枝饮品", "葡萄 / 莓果汁", "热带水果汁", "蔬菜汁", "椰子水"}),
}
_PREFERENCE_FLAVOR_OPTIONS = frozenset({
    "茉莉花", "兰花", "桂花", "玫瑰", "水蜜桃", "荔枝", "梨", "柑橘", "桂圆", "红枣", "青梅", "葡萄干",
    "嫩叶", "青草", "竹叶", "青豆", "板栗", "炒黄豆", "烤花生", "烤面包", "蜂蜜", "焦糖", "糯米", "陈皮",
})
_PREFERENCE_EVIDENCE_SOURCE = re.compile(
    r"^(?:(?:record|brew)-[a-z0-9-]{1,40}|[0-9a-f]{8}-[0-9a-f-]{27})$",
    re.IGNORECASE,
)


class PreferenceO1(ContractModel):
    tea: tuple[str, ...] = Field(default=(), max_length=8)
    coffee: tuple[str, ...] = Field(default=(), max_length=8)
    milk: tuple[str, ...] = Field(default=(), max_length=8)
    juice: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_supported_values(self) -> "PreferenceO1":
        for category, allowed in _PREFERENCE_O1_OPTIONS.items():
            values = getattr(self, category)
            if len(values) != len(set(values)) or any(value not in allowed for value in values):
                raise ValueError(f"unsupported {category} preference")
        return self


class PreferenceO2(ContractModel):
    sweetness: int = Field(default=50, ge=0, le=100)
    flavors: tuple[str, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def validate_supported_flavors(self) -> "PreferenceO2":
        if len(self.flavors) != len(set(self.flavors)) or any(value not in _PREFERENCE_FLAVOR_OPTIONS for value in self.flavors):
            raise ValueError("unsupported flavor preference")
        return self


class PreferenceProfile(ContractModel):
    """The only durable P9-4A preference payload; never generic app state."""

    o1: PreferenceO1 = Field(default_factory=PreferenceO1)
    o2: PreferenceO2 = Field(default_factory=PreferenceO2)


def canonical_empty_preference_profile() -> PreferenceProfile:
    return PreferenceProfile()


class UserPreferencesResponse(ContractModel):
    profile: PreferenceProfile
    revision: int = Field(ge=0)
    updated_at: datetime | None = None


class PutUserPreferencesRequest(ContractModel):
    profile: PreferenceProfile
    expected_revision: int = Field(ge=0)


class WarehouseTeaInput(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    tea_category: str | None = Field(default=None, max_length=80)
    tea_subtype: str | None = Field(default=None, max_length=120)
    origin: str | None = Field(default=None, max_length=200)
    roast_or_style: str | None = Field(default=None, max_length=120)
    aroma: str | None = Field(default=None, max_length=120)
    status: str = Field(pattern=r"^(drinking|paused|finished)$")
    source_type: str = Field(pattern=r"^(manual|selection)$")
    selection_session_id: UUID | None = None
    candidate_id: UUID | None = None
    extraction_version_id: UUID | None = None
    decision_version_id: UUID | None = None
    facts: tuple[str, ...] = Field(default=(), max_length=8)
    risks: tuple[str, ...] = Field(default=(), max_length=8)
    risk_flags: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_bounded_arrays(self) -> "WarehouseTeaInput":
        for field_name, limit in (("facts", 200), ("risks", 200), ("risk_flags", 80)):
            if any(len(value) > limit for value in getattr(self, field_name)):
                raise ValueError(f"{field_name} item is too long")
        return self


class WarehouseTea(WarehouseTeaInput):
    id: UUID
    joined_at: datetime
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class PutWarehouseTeaRequest(ContractModel):
    tea: WarehouseTeaInput
    expected_revision: int = Field(ge=0)


class BrewInfusion(ContractModel):
    number: int = Field(ge=1, le=20)
    suggested: float = Field(ge=0, le=600)
    actual: float = Field(ge=0, le=600)


class BrewPlan(ContractModel):
    ware: str | None = Field(default=None, max_length=40)
    water: str | None = Field(default=None, max_length=40)
    grams: str | None = Field(default=None, max_length=40)
    temp: str | None = Field(default=None, max_length=40)


class BrewFeedback(ContractModel):
    taste: str | None = Field(default=None, max_length=80)
    strength: str | None = Field(default=None, max_length=80)
    tags: tuple[str, ...] = Field(default=(), max_length=3)
    aroma: tuple[str, ...] = Field(default=(), max_length=3)
    impression: str | None = Field(default=None, max_length=500)
    score: int | None = Field(default=None, ge=1, le=5)
    repurchase: str | None = Field(default=None, max_length=80)
    advanced: dict[str, str] = Field(default_factory=dict)

    @field_validator("advanced")
    @classmethod
    def validate_advanced(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"回甘", "生津", "余韵"}
        if any(key not in allowed or len(item) > 80 for key, item in value.items()):
            raise ValueError("unsupported advanced feedback")
        return value

    @field_validator("tags", "aroma")
    @classmethod
    def validate_feedback_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(item) > 40 for item in value):
            raise ValueError("feedback item is too long")
        return value


class BrewJournalEntryInput(ContractModel):
    tea_id: UUID
    brewed_on: date
    infusions: tuple[BrewInfusion, ...] = Field(default=(), max_length=20)
    plan: BrewPlan = Field(default_factory=BrewPlan)
    feedback: BrewFeedback = Field(default_factory=BrewFeedback)
    suggestion: str | None = Field(default=None, max_length=500)


class BrewJournalEntry(BrewJournalEntryInput):
    id: UUID
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class PutBrewJournalEntryRequest(ContractModel):
    entry: BrewJournalEntryInput
    expected_revision: int = Field(ge=0)


class SelectionSessionSummary(ContractModel):
    id: UUID
    need: SelectionNeedInput
    created_at: datetime
    updated_at: datetime


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
    target_value: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    polarity: PreferencePolarity
    confidence: str = Field(pattern=r"^low$")
    issue_source: str = Field(pattern=r"^(tea|brewing|uncertain)$")
    source_brew_session_id: str = Field(min_length=1, max_length=120)
    created_at: datetime

    @field_validator("source_brew_session_id")
    @classmethod
    def validate_source_brew_session_id(cls, value: str) -> str:
        if not _PREFERENCE_EVIDENCE_SOURCE.fullmatch(value):
            raise ValueError("invalid preference evidence source")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class PutPreferenceEvidenceRequest(ContractModel):
    """Idempotent, source-scoped evidence upsert payload for the current user."""

    items: tuple[PreferenceEvidence, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_unique_sources(self) -> "PutPreferenceEvidenceRequest":
        sources = tuple(item.source_brew_session_id for item in self.items)
        if len(sources) != len(set(sources)):
            raise ValueError("duplicate preference evidence source")
        return self


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
