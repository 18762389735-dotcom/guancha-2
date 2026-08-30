import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from guancha_api.auth.cloudbase import CloudBaseTokenVerifier
from guancha_api.auth.fake import (
    ConfigurationErrorTokenVerifier,
    UnconfiguredTokenVerifier,
)
from guancha_api.auth.interfaces import TokenVerifier
from guancha_api.api.v1.routes import router as v1_router
from guancha_api.application.task_runners import InProcessTaskRunner
from guancha_api.application.task_runners import TaskEnqueueError
from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.infrastructure.storage.interfaces import TemporaryImageCleanupError
from guancha_api.providers.fake import FakeProvider
from guancha_api.providers.unavailable import UnconfiguredVisionProvider
from guancha_api.providers.execution import StructuredVisionProvider
from guancha_api.providers.openai import OpenAIResponsesProvider
from guancha_api.providers.mimo import DEFAULT_MIMO_BASE_URL, MiMoVisionProvider
from guancha_api.providers.reasoning import FakeReasoningProvider, ReasoningProvider
from guancha_api.providers.merchant_reply import FakeMerchantReplyReasoningProvider, MerchantReplyReasoningProvider
from guancha_api.providers.feedback import FakeFeedbackProvider, FeedbackReasoningProvider
from guancha_api.repositories.postgres import (
    CandidateLimitExceeded,
    CandidateImageLimitExceeded,
    CandidateExtractionInProgress,
    DecisionInputInvalid,
    CurrentDecisionNotAvailable,
    DecisionStale,
    QuestionsNotAvailable,
    QuestionGenerationFailed,
    MerchantReplyNotAvailable,
    IdempotencyConflict,
    OwnershipDenied,
    PostgresPhase2Repository,
    RepositoryError,
    ResourceNotFound,
)
from guancha_api.core.errors import ApiErrorDetail, ApiErrorResponse
from guancha_api.product_events import ProductEventSink


def _provider_from_environment(storage: InMemoryTemporaryPrivateStorage) -> StructuredVisionProvider:
    # Fake is deliberately opt-in.  The competition application must never
    # turn an absent live configuration into a plausible fixture result.
    mode = os.getenv("GUANCHA_PROVIDER", "").lower()
    if not mode:
        return UnconfiguredVisionProvider()
    if mode == "fake":
        return FakeProvider(
            extraction_response={
                "product_name": "安溪铁观音", "tea_category": "乌龙茶",
                "tea_subtype": "铁观音", "origin": "安溪",
                "roast_or_style": "清香型", "aroma_claims": ["兰花香"],
                "taste_claims": ["回甘"], "season": None, "year_or_batch": None,
                "grade": None, "weight": None, "price": None,
                "brew_claims": [], "risk_flags": ["年份未明确"],
                # Fake mode is a local demonstration provider, not a hidden
                # fixture transport.  Keep its output presentable while using
                # exactly the same product-claim/unverified evidence boundary
                # as a live screenshot provider.
                "evidence": [
                    {
                        "field_name": field_name, "raw_text": value,
                        "normalized_value": value, "model_confidence": 1.0,
                        "information_status": "explicit", "source_type": "product-claim",
                        "verification_status": "unverified", "source_location": "product page",
                        "evidence_strength": "high",
                    }
                    for field_name, value in (
                        ("product_name", "安溪铁观音"),
                        ("tea_category", "乌龙茶"),
                        ("tea_subtype", "铁观音"),
                        ("origin", "安溪"),
                        ("roast_or_style", "清香型"),
                    )
                ]
            }
        )
    if mode == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("GUANCHA_OPENAI_MODEL")
        if not api_key or not model:
            raise RuntimeError("GUANCHA_PROVIDER=openai requires OPENAI_API_KEY and GUANCHA_OPENAI_MODEL")
        return OpenAIResponsesProvider(api_key=api_key, model=model, storage=storage)
    if mode == "mimo":
        api_key = os.getenv("MIMO_API_KEY")
        model = os.getenv("GUANCHA_MIMO_MODEL")
        if not api_key or not model:
            raise RuntimeError("GUANCHA_PROVIDER=mimo requires MIMO_API_KEY and GUANCHA_MIMO_MODEL")
        return MiMoVisionProvider(
            api_key=api_key,
            model=model,
            storage=storage,
            base_url=os.getenv("MIMO_BASE_URL", DEFAULT_MIMO_BASE_URL),
        )
    raise RuntimeError("GUANCHA_PROVIDER must be fake, openai, or mimo")


def _token_verifier_from_environment() -> TokenVerifier:
    env_id = os.getenv("CLOUDBASE_ENV_ID", "").strip()
    if not env_id:
        return UnconfiguredTokenVerifier()
    try:
        return CloudBaseTokenVerifier(
            env_id=env_id,
            region=os.getenv("CLOUDBASE_REGION", "ap-shanghai"),
        )
    except ValueError:
        # Keep anonymous startup available while making /me fail closed.
        return ConfigurationErrorTokenVerifier()

def create_app(
    *,
    repository: PostgresPhase2Repository | None = None,
    worker_repository_factory: Callable[[], Awaitable[PostgresPhase2Repository]] | None = None,
    task_runner: InProcessTaskRunner | None = None,
    temporary_storage: InMemoryTemporaryPrivateStorage | None = None,
    provider: StructuredVisionProvider | None = None,
    reasoning_provider: ReasoningProvider | None = None,
    merchant_reply_provider: MerchantReplyReasoningProvider | None = None,
    feedback_provider: FeedbackReasoningProvider | None = None,
    product_event_sink: ProductEventSink | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastAPI:
    """Build an injectable API application; tests never need external services."""
    resolved_task_runner = task_runner or InProcessTaskRunner()
    resolved_temporary_storage = temporary_storage or InMemoryTemporaryPrivateStorage()
    resolved_provider = provider or _provider_from_environment(resolved_temporary_storage)
    resolved_reasoning_provider = reasoning_provider or FakeReasoningProvider()
    resolved_merchant_reply_provider = merchant_reply_provider or FakeMerchantReplyReasoningProvider()
    resolved_feedback_provider = feedback_provider or FakeFeedbackProvider()
    resolved_product_event_sink = product_event_sink or ProductEventSink.from_environment()
    resolved_token_verifier = token_verifier or _token_verifier_from_environment()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        owns_repository = repository is None
        database_url = os.getenv("GUANCHA_DATABASE_URL")
        application.state.repository = repository or (
            await PostgresPhase2Repository.connect(database_url) if database_url else None
        )
        application.state.worker_repository_factory = worker_repository_factory or (
            (lambda: PostgresPhase2Repository.connect(database_url)) if database_url else None
        )
        application.state.temporary_storage = resolved_temporary_storage
        application.state.task_runner = resolved_task_runner
        application.state.provider = resolved_provider
        application.state.reasoning_provider = resolved_reasoning_provider
        application.state.merchant_reply_provider = resolved_merchant_reply_provider
        application.state.feedback_provider = resolved_feedback_provider
        application.state.product_event_sink = resolved_product_event_sink
        application.state.token_verifier = resolved_token_verifier
        if application.state.repository is not None:
            await application.state.repository.recover_interrupted_jobs()
        try:
            yield
        finally:
            await application.state.task_runner.shutdown()
            if owns_repository and application.state.repository is not None:
                await application.state.repository.close()

    application = FastAPI(title="Guancha P0 API", version="0.1.0", lifespan=lifespan)
    # ASGI tests intentionally do not depend on a server process or lifespan
    # manager to exercise injected persistence boundaries.
    application.state.repository = repository
    application.state.worker_repository_factory = worker_repository_factory
    application.state.task_runner = resolved_task_runner
    application.state.temporary_storage = resolved_temporary_storage
    application.state.provider = resolved_provider
    application.state.reasoning_provider = resolved_reasoning_provider
    application.state.merchant_reply_provider = resolved_merchant_reply_provider
    application.state.feedback_provider = resolved_feedback_provider
    application.state.product_event_sink = resolved_product_event_sink
    application.state.token_verifier = resolved_token_verifier
    application.state.feedback_replays = {}
    application.state.feedback_client_ids = {}
    application.include_router(v1_router)
    _register_exception_handlers(application)
    # The competition demo intentionally uses the existing static prototype
    # as the same-origin UI, keeping local startup to one small process.
    frontend_root = Path(__file__).resolve().parents[3]
    if (frontend_root / "index.html").is_file():
        application.mount("/", StaticFiles(directory=frontend_root, html=True), name="prototype")
    return application


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    resource_id: UUID | None = None,
) -> JSONResponse:
    payload = ApiErrorResponse(
        error=ApiErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            resource_id=resource_id,
            request_id=uuid4(),
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json", by_alias=True))


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Public fallback retained for contract tests and registered per app factory."""
    return error_response(
        status_code=500,
        code="internal_error",
        message="An unexpected error occurred.",
    )


def _not_found_code(exc: ResourceNotFound) -> str:
    message = str(exc).lower()
    if "selection session" in message:
        return "selection_session_not_found"
    if "candidate image" in message:
        return "candidate_image_not_found"
    if "candidate" in message:
        return "candidate_not_found"
    if "decision version" in message:
        return "current_decision_not_available"
    if "merchant reply" in message:
        return "merchant_reply_not_found"
    if "decision delta" in message:
        return "decision_delta_not_found"
    return "not_found"


def _register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first_error = exc.errors()[0] if exc.errors() else {}
        location = first_error.get("loc", [])
        if location[:1] == ("header",) or location[:1] == ["header"]:
            header_name = str(location[1]).lower() if len(location) > 1 else ""
            if header_name == "x-client-id":
                return error_response(
                    status_code=422,
                    code="missing_client_id",
                    message="X-Client-Id header is required.",
                )
            if header_name == "idempotency-key":
                return error_response(
                    status_code=422,
                    code="missing_idempotency_key",
                    message="Idempotency-Key header is required.",
                )
        if request.url.path.endswith("/brew-feedback/analyze"):
            return error_response(
                status_code=422,
                code="brew_feedback_invalid",
                message="Brew feedback input is invalid.",
            )
        return error_response(
            status_code=422,
            code="validation_error",
            message="Request validation failed.",
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else ""
        if detail in {"missing_client_id", "invalid_client_id", "missing_idempotency_key", "invalid_idempotency_key"}:
            return error_response(status_code=exc.status_code, code=detail, message="Required request header is invalid.")
        if detail == "admin_access_denied":
            return error_response(status_code=403, code="resource_not_owned", message="Admin access is required.")
        if detail in {"invalid_image_type", "image_too_large", "unsafe_or_corrupt_image", "image_too_low_resolution", "image_pixel_limit_exceeded"}:
            return error_response(status_code=exc.status_code, code=detail, message="Image upload failed safety validation.")
        if detail == "candidate_extraction_not_retryable":
            return error_response(status_code=exc.status_code, code=detail, message="The current extraction job cannot be retried.")
        if detail == "feedback_analysis_failed":
            return error_response(status_code=503, code=detail, message="Brew feedback analysis is temporarily unavailable.", retryable=True)
        if detail == "feedback_duplicate":
            return error_response(status_code=409, code=detail, message="Feedback idempotency key belongs to a different request.")
        if detail == "authentication_required":
            return error_response(status_code=401, code=detail, message="Authentication is required.")
        if detail == "invalid_access_token":
            return error_response(status_code=401, code=detail, message="Access token is invalid.")
        if detail == "auth_not_configured":
            return error_response(status_code=503, code=detail, message="Authentication is not configured.")
        if detail == "authentication_service_unavailable":
            return error_response(status_code=503, code=detail, message="Authentication service is unavailable.", retryable=True)
        if exc.status_code == 404:
            return error_response(status_code=404, code="not_found", message="Resource not found.")
        if exc.status_code == 405:
            return error_response(status_code=405, code="method_not_allowed", message="Method not allowed.")
        if exc.status_code == 501:
            return error_response(status_code=501, code="contract_not_implemented", message="This Phase 2 contract is not implemented yet.")
        if exc.status_code == 503 or detail == "database_not_configured":
            return error_response(status_code=503, code="service_unavailable", message="Database service is not configured.")
        return error_response(status_code=exc.status_code, code="internal_error", message="An unexpected error occurred.")

    @application.exception_handler(RepositoryError)
    async def repository_error_handler(request: Request, exc: RepositoryError) -> JSONResponse:
        if isinstance(exc, OwnershipDenied):
            return error_response(status_code=403, code="resource_not_owned", message="Resource belongs to another owner.")
        if isinstance(exc, ResourceNotFound):
            code = exc.error_code.value if exc.error_code is not None else _not_found_code(exc)
            return error_response(status_code=404, code=code, message="Requested resource was not found.")
        if isinstance(exc, IdempotencyConflict):
            return error_response(status_code=409, code="idempotency_conflict", message="Idempotency key belongs to a different request.")
        if isinstance(exc, CandidateLimitExceeded):
            return error_response(status_code=409, code="candidate_limit_exceeded", message="A selection session permits at most five candidates.")
        if isinstance(exc, CandidateImageLimitExceeded):
            return error_response(status_code=409, code="candidate_image_limit_exceeded", message="A candidate permits at most two images.")
        if isinstance(exc, CandidateExtractionInProgress):
            return error_response(status_code=409, code="candidate_extraction_in_progress", message="Candidate already has an active extraction job.")
        if isinstance(exc, DecisionInputInvalid):
            return error_response(status_code=409, code="decision_inputs_incomplete", message="Every candidate needs a current extraction before analysis.")
        if isinstance(exc, CurrentDecisionNotAvailable):
            return error_response(status_code=404, code="current_decision_not_available", message="No current decision is available.")
        if isinstance(exc, DecisionStale):
            return error_response(status_code=409, code="decision_stale", message="This decision is no longer current; analyze again first.")
        if isinstance(exc, QuestionsNotAvailable):
            return error_response(status_code=409, code="questions_not_available", message="Questions are not available for this decision yet.")
        if isinstance(exc, QuestionGenerationFailed):
            return error_response(status_code=503, code="ai_provider_error", message="Question generation failed; retry is safe.", retryable=True)
        if isinstance(exc, MerchantReplyNotAvailable):
            return error_response(status_code=409, code="question_not_available", message="The referenced follow-up question is not current.")
        return error_response(status_code=500, code="internal_error", message="Persistence operation failed.")

    @application.exception_handler(psycopg.Error)
    async def database_error_handler(request: Request, exc: psycopg.Error) -> JSONResponse:
        return error_response(
            status_code=503,
            code="service_unavailable",
            message="Database service is unavailable.",
            retryable=True,
        )

    @application.exception_handler(TemporaryImageCleanupError)
    async def storage_cleanup_error_handler(
        request: Request, exc: TemporaryImageCleanupError
    ) -> JSONResponse:
        return error_response(
            status_code=503,
            code="temporary_image_cleanup_failed",
            message="Temporary private image cleanup failed.",
            retryable=True,
        )

    @application.exception_handler(TaskEnqueueError)
    async def task_enqueue_error_handler(request: Request, exc: TaskEnqueueError) -> JSONResponse:
        return error_response(
            status_code=503,
            code="service_unavailable",
            message="Background task service is unavailable.",
            retryable=True,
        )

    application.add_exception_handler(Exception, internal_error_handler)

    @application.get(
        "/health",
        tags=["system"],
        responses={422: {"model": ApiErrorResponse, "description": "Unified API error"}},
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}


app = create_app()
