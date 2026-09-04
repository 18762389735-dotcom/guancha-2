import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from guancha_api.auth.cloudbase import CloudBaseTokenVerifier
from guancha_api.auth.cookies import clear_refresh_cookie
from guancha_api.auth.gateway import CloudBaseAuthError, CloudBaseAuthGateway
from guancha_api.auth.fake import (
    ConfigurationErrorTokenVerifier,
    UnconfiguredTokenVerifier,
)
from guancha_api.auth.interfaces import TokenVerifier
from guancha_api.api.v1.routes import router as v1_router
from guancha_api.application.task_runners import InProcessTaskRunner, TaskEnqueueError, TaskRunner
from guancha_api.application.extraction_recovery import (
    extraction_stale_before_from_environment,
)
from guancha_api.infrastructure.storage.factory import temporary_private_storage_from_environment
from guancha_api.infrastructure.storage.interfaces import (
    TemporaryImageCleanupError,
    TemporaryPrivateStorage,
)
from guancha_api.providers.fake import FakeProvider
from guancha_api.providers.unavailable import UnconfiguredVisionProvider
from guancha_api.providers.execution import StructuredVisionProvider
from guancha_api.providers.openai import OpenAIResponsesProvider
from guancha_api.providers.mimo import DEFAULT_MIMO_BASE_URL, MiMoVisionProvider
from guancha_api.providers.reasoning import FakeReasoningProvider, ReasoningProvider
from guancha_api.providers.merchant_reply import FakeMerchantReplyReasoningProvider, MerchantReplyReasoningProvider
from guancha_api.providers.merchant_reply_mimo import MiMoMerchantReplyReasoningProvider
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
    PreferenceRevisionConflict,
    WarehouseRevisionConflict,
    BrewJournalRevisionConflict,
    PostgresPhase2Repository,
    RepositoryError,
    ResourceNotFound,
)
from guancha_api.core.errors import ApiErrorDetail, ApiErrorResponse
from guancha_api.product_events import ProductEventSink
from guancha_api.tasks.cloud_function import CloudFunctionExtractionDispatcher


# Keep the per-instance pool small: the current Run maximum of five instances
# yields at most fifteen pooled connections before the legacy admin connection.
# All values remain environment-configurable for a deployment-specific limit.
DEFAULT_DB_POOL_MIN_SIZE = 1
DEFAULT_DB_POOL_MAX_SIZE = 3
DEFAULT_DB_POOL_TIMEOUT_SECONDS = 5.0
DEFAULT_EXTRACTION_EXECUTION = "in-process"
_DATABASE_POOL_CHECK = AsyncConnectionPool.check_connection


def _pool_setting(name: str, default: int | float, *, minimum: int | float) -> int | float:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value) if isinstance(default, float) else int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a valid number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def _database_pool(database_url: str) -> AsyncConnectionPool:
    min_size = int(_pool_setting("GUANCHA_DB_POOL_MIN_SIZE", DEFAULT_DB_POOL_MIN_SIZE, minimum=0))
    max_size = int(_pool_setting("GUANCHA_DB_POOL_MAX_SIZE", DEFAULT_DB_POOL_MAX_SIZE, minimum=1))
    timeout = float(_pool_setting("GUANCHA_DB_POOL_TIMEOUT_SECONDS", DEFAULT_DB_POOL_TIMEOUT_SECONDS, minimum=0.1))
    if max_size < min_size:
        raise RuntimeError("GUANCHA_DB_POOL_MAX_SIZE must be greater than or equal to GUANCHA_DB_POOL_MIN_SIZE")
    return AsyncConnectionPool(
        conninfo=database_url,
        kwargs={"autocommit": True, "row_factory": dict_row},
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        check=_DATABASE_POOL_CHECK,
        open=False,
    )


def _extraction_task_runner_from_environment() -> TaskRunner:
    """Select only screenshot extraction dispatch; other background work stays local."""

    backend = os.getenv(
        "GUANCHA_EXTRACTION_EXECUTION", DEFAULT_EXTRACTION_EXECUTION
    ).strip().lower()
    if backend in {"", "in-process"}:
        return InProcessTaskRunner()
    if backend == "cloud-function":
        region = os.getenv("GUANCHA_EXTRACTION_FUNCTION_REGION", "").strip()
        if not region:
            raise RuntimeError("GUANCHA_EXTRACTION_FUNCTION_REGION must not be empty")
        return CloudFunctionExtractionDispatcher.from_environment(region=region)
    raise RuntimeError(
        "GUANCHA_EXTRACTION_EXECUTION must be in-process or cloud-function"
    )


def _provider_from_environment(storage: TemporaryPrivateStorage) -> StructuredVisionProvider:
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


def _merchant_reply_provider_from_environment() -> MerchantReplyReasoningProvider:
    configured_mode = os.getenv("GUANCHA_MERCHANT_REPLY_PROVIDER", "").strip().lower()
    mode = configured_mode or ("mimo" if os.getenv("GUANCHA_PROVIDER", "").strip().lower() == "mimo" else "fake")
    if mode == "fake":
        return FakeMerchantReplyReasoningProvider()
    if mode == "mimo":
        api_key = os.getenv("MIMO_API_KEY", "").strip()
        model = os.getenv("GUANCHA_MIMO_MODEL", "").strip()
        if not api_key or not model:
            raise RuntimeError("GUANCHA_MERCHANT_REPLY_PROVIDER=mimo requires MIMO_API_KEY and GUANCHA_MIMO_MODEL")
        return MiMoMerchantReplyReasoningProvider(
            api_key=api_key,
            model=model,
            base_url=os.getenv("MIMO_BASE_URL", DEFAULT_MIMO_BASE_URL),
        )
    raise RuntimeError("GUANCHA_MERCHANT_REPLY_PROVIDER must be fake or mimo")


def _auth_gateway_from_environment() -> CloudBaseAuthGateway | None:
    env_id = os.getenv("CLOUDBASE_ENV_ID", "").strip()
    if not env_id:
        return None
    try:
        return CloudBaseAuthGateway(
            env_id=env_id,
            region=os.getenv("CLOUDBASE_REGION", "ap-shanghai"),
        )
    except ValueError:
        return None

def create_app(
    *,
    repository: PostgresPhase2Repository | None = None,
    database_pool: AsyncConnectionPool | None = None,
    worker_repository_factory: Callable[[], Awaitable[PostgresPhase2Repository]] | None = None,
    task_runner: TaskRunner | None = None,
    extraction_task_runner: TaskRunner | None = None,
    temporary_storage: TemporaryPrivateStorage | None = None,
    provider: StructuredVisionProvider | None = None,
    reasoning_provider: ReasoningProvider | None = None,
    merchant_reply_provider: MerchantReplyReasoningProvider | None = None,
    feedback_provider: FeedbackReasoningProvider | None = None,
    product_event_sink: ProductEventSink | None = None,
    token_verifier: TokenVerifier | None = None,
    auth_gateway: CloudBaseAuthGateway | None = None,
) -> FastAPI:
    """Build an injectable API application; tests never need external services."""
    resolved_task_runner = task_runner or InProcessTaskRunner()
    # Tests and explicitly injected callers retain their existing single-runner
    # behavior. Normal startup selects an extraction-only runner by config.
    resolved_extraction_task_runner = (
        extraction_task_runner
        or task_runner
        or _extraction_task_runner_from_environment()
    )
    resolved_temporary_storage = temporary_storage or temporary_private_storage_from_environment()
    resolved_provider = provider or _provider_from_environment(resolved_temporary_storage)
    resolved_reasoning_provider = reasoning_provider or FakeReasoningProvider()
    resolved_merchant_reply_provider = merchant_reply_provider or _merchant_reply_provider_from_environment()
    resolved_feedback_provider = feedback_provider or FakeFeedbackProvider()
    resolved_product_event_sink = product_event_sink or ProductEventSink.from_environment()
    resolved_token_verifier = token_verifier or _token_verifier_from_environment()
    resolved_auth_gateway = auth_gateway or _auth_gateway_from_environment()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        owns_repository = repository is None
        owns_database_pool = database_pool is None
        database_url = os.getenv("GUANCHA_DATABASE_URL", "").strip()
        application.state.repository = repository
        application.state.database_pool = database_pool
        application.state.worker_repository_factory = worker_repository_factory
        application.state.temporary_storage = resolved_temporary_storage
        application.state.task_runner = resolved_task_runner
        application.state.extraction_task_runner = resolved_extraction_task_runner
        application.state.provider = resolved_provider
        application.state.reasoning_provider = resolved_reasoning_provider
        application.state.merchant_reply_provider = resolved_merchant_reply_provider
        application.state.feedback_provider = resolved_feedback_provider
        application.state.product_event_sink = resolved_product_event_sink
        application.state.token_verifier = resolved_token_verifier
        application.state.auth_gateway = resolved_auth_gateway
        try:
            if application.state.database_pool is None and database_url and repository is None:
                application.state.database_pool = _database_pool(database_url)
            if application.state.worker_repository_factory is None and database_url:
                application.state.worker_repository_factory = lambda: PostgresPhase2Repository.connect(database_url)
            if application.state.database_pool is not None:
                await application.state.database_pool.open()
            if application.state.repository is None and database_url:
                application.state.repository = await PostgresPhase2Repository.connect(database_url)
            if application.state.repository is not None:
                await application.state.repository.recover_interrupted_jobs(
                    stale_before=extraction_stale_before_from_environment()
                )
            yield
        finally:
            await application.state.task_runner.shutdown()
            if application.state.extraction_task_runner is not application.state.task_runner:
                await application.state.extraction_task_runner.shutdown()
            if owns_repository and application.state.repository is not None:
                await application.state.repository.close()
            if owns_database_pool and application.state.database_pool is not None:
                await application.state.database_pool.close()

    application = FastAPI(title="Guancha P0 API", version="0.1.0", lifespan=lifespan)
    # ASGI tests intentionally do not depend on a server process or lifespan
    # manager to exercise injected persistence boundaries.
    application.state.repository = repository
    application.state.database_pool = database_pool
    application.state.worker_repository_factory = worker_repository_factory
    application.state.task_runner = resolved_task_runner
    application.state.extraction_task_runner = resolved_extraction_task_runner
    application.state.temporary_storage = resolved_temporary_storage
    application.state.provider = resolved_provider
    application.state.reasoning_provider = resolved_reasoning_provider
    application.state.merchant_reply_provider = resolved_merchant_reply_provider
    application.state.feedback_provider = resolved_feedback_provider
    application.state.product_event_sink = resolved_product_event_sink
    application.state.token_verifier = resolved_token_verifier
    application.state.auth_gateway = resolved_auth_gateway
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
        if detail == "session_expired":
            response = error_response(status_code=401, code=detail, message="Login session has expired; sign in again.")
            clear_refresh_cookie(request, response)
            return response
        if exc.status_code == 404:
            return error_response(status_code=404, code="not_found", message="Resource not found.")
        if exc.status_code == 405:
            return error_response(status_code=405, code="method_not_allowed", message="Method not allowed.")
        if exc.status_code == 501:
            return error_response(status_code=501, code="contract_not_implemented", message="This Phase 2 contract is not implemented yet.")
        if exc.status_code == 503 or detail == "database_not_configured":
            return error_response(status_code=503, code="service_unavailable", message="Database service is not configured.")
        return error_response(status_code=exc.status_code, code="internal_error", message="An unexpected error occurred.")

    @application.exception_handler(CloudBaseAuthError)
    async def cloudbase_auth_error_handler(request: Request, exc: CloudBaseAuthError) -> JSONResponse:
        messages = {
            "invalid_credentials": "邮箱或密码不正确。",
            "verification_invalid": "验证码无效。",
            "verification_expired": "验证码已过期。",
            "verification_rate_limited": "验证码请求过于频繁，请稍后重试。",
            "registration_conflict": "该邮箱已注册。",
            "captcha_required": "认证服务需要额外验证。",
            "session_expired": "登录状态已过期，请重新登录。",
            "auth_provider_unavailable": "认证服务暂时不可用，请稍后重试。",
        }
        response = error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=messages.get(exc.code, "认证服务暂时不可用，请稍后重试。"),
            retryable=exc.retryable,
        )
        if exc.clear_cookie:
            clear_refresh_cookie(request, response)
        return response

    @application.exception_handler(RepositoryError)
    async def repository_error_handler(request: Request, exc: RepositoryError) -> JSONResponse:
        if isinstance(exc, OwnershipDenied):
            return error_response(status_code=403, code="resource_not_owned", message="Resource belongs to another owner.")
        if isinstance(exc, ResourceNotFound):
            code = exc.error_code.value if exc.error_code is not None else _not_found_code(exc)
            return error_response(status_code=404, code=code, message="Requested resource was not found.")
        if isinstance(exc, IdempotencyConflict):
            return error_response(status_code=409, code="idempotency_conflict", message="Idempotency key belongs to a different request.")
        if isinstance(exc, PreferenceRevisionConflict):
            return error_response(
                status_code=409,
                code="preferences_revision_conflict",
                message="Preferences changed on another device. Refresh and edit again.",
            )
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
        if isinstance(exc, WarehouseRevisionConflict):
            return error_response(
                status_code=409,
                code="warehouse_revision_conflict",
                message="Warehouse tea changed on another device. Refresh and edit again.",
            )
        if isinstance(exc, BrewJournalRevisionConflict):
            return error_response(
                status_code=409,
                code="brew_journal_revision_conflict",
                message="Brew Journal entry changed on another device. Refresh and edit again.",
            )
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
