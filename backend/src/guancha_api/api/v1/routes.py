import os
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile, status

from guancha_api.auth.dependencies import CurrentUser
from guancha_api.application.phase2_service import Phase2ExtractionService
from guancha_api.application.decision_service import SessionDecisionService
from guancha_api.application.question_service import QuestionGenerationService
from guancha_api.application.merchant_reply_service import MerchantReplyService
from guancha_api.application.answer_contract import build_selection_answer
from guancha_api.repositories.idempotency import request_hash
from guancha_api.schemas.contracts import BrewFeedbackAnalysisRequest, BrewFeedbackAnalysisResponse
from guancha_api.product_events import CLIENT_EVENT_NAMES, ClientProductEvent, parse_analytics_session, safe_emit_client, safe_emit_server

from guancha_api.core.errors import ApiErrorResponse
from guancha_api.schemas.contracts import (
    AnalysisJobResponse,
    Candidate,
    CandidateDecision,
    DecisionVersion,
    DecisionVersionResponse,
    CandidateImageMetadata,
    CreateCandidateRequest,
    CreateSelectionSessionRequest,
    UpdateSelectionNeedRequest,
    ErrorCode,
    EvidenceItem,
    FollowupQuestion,
    CreateMerchantReplyRequest,
    CreateRejudgeRequest,
    DecisionDelta,
    MerchantReply,
    ExtractionVersionResponse,
    PublicConfig,
    SelectionSession,
    CurrentUserResponse,
    UploadCandidateImageResponse,
)

router = APIRouter(prefix="/api/v1", tags=["public"])


async def require_admin_token(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """Separate admin inspection from anonymous-client public endpoints."""
    expected = os.getenv("ADMIN_API_TOKEN")
    supplied = authorization.removeprefix("Bearer ") if authorization else None
    if not expected or supplied != expected:
        raise HTTPException(status_code=403, detail="admin_access_denied")
async def require_client_id(
    value: Annotated[str, Header(alias="X-Client-Id")],
) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=ErrorCode.INVALID_CLIENT_ID.value) from exc


async def require_idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key")],
) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=ErrorCode.INVALID_IDEMPOTENCY_KEY.value) from exc


ClientId = Annotated[UUID, Depends(require_client_id)]
IdempotencyKey = Annotated[UUID, Depends(require_idempotency_key)]
AnalyticsSession = Annotated[str | None, Header(alias="X-Analytics-Session-Id")]

def _emit(raw: Request, *, event_name: str, resource_id: UUID, analytics_session: str | None, **fields: object) -> None:
    safe_emit_server(raw.app.state.product_event_sink,
        event_name=event_name, resource_id=resource_id,
        anonymous_session_id=parse_analytics_session(analytics_session), **fields,
    )

@router.post("/events", status_code=202)
async def create_product_event(event: ClientProductEvent, raw: Request) -> dict[str, str]:
    if event.event_name not in CLIENT_EVENT_NAMES:
        raise HTTPException(status_code=422, detail="validation_error")
    safe_emit_client(raw.app.state.product_event_sink, event)
    return {"status": "accepted"}

def _repo(request: Request):
    if getattr(request.app.state, "repository", None) is None:
        raise HTTPException(503, "database_not_configured")
    return request.app.state.repository


def _service(request: Request) -> Phase2ExtractionService:
    return Phase2ExtractionService(
        _repo(request),
        worker_repository_factory=getattr(request.app.state, "worker_repository_factory", None),
    )

def _decision_service(request: Request) -> SessionDecisionService:
    return SessionDecisionService(_repo(request), request.app.state.product_event_sink)

def _question_service(request: Request) -> QuestionGenerationService:
    return QuestionGenerationService(_repo(request), request.app.state.reasoning_provider)

def _merchant_reply_service(request: Request) -> MerchantReplyService:
    return MerchantReplyService(_repo(request), request.app.state.merchant_reply_provider, request.app.state.product_event_sink)

def _job(value): return AnalysisJobResponse(id=value.id, candidate_id=value.candidate_id, candidate_image_id=value.candidate_image_id, status=value.status, stage=value.stage, attempt=value.attempt, error_code=value.error_code, extraction_version_id=value.extraction_version_id, decision_version_id=value.decision_version_id, decision_delta_id=value.decision_delta_id, processing_mode=value.processing_mode, created_at=value.created_at, updated_at=value.updated_at)
def _image(value, job_id=None): return CandidateImageMetadata(id=value.id, candidate_id=value.candidate_id, content_type=value.content_type, size_bytes=value.size_bytes, sha256=value.sanitized_sha256, width=value.width, height=value.height, display_order=value.display_order, status=value.status, current_job_id=job_id, created_at=value.created_at)

@router.get("/admin/jobs", tags=["admin"])
async def admin_jobs(raw: Request, _: Annotated[None, Depends(require_admin_token)]) -> tuple[dict[str, object], ...]:
    return await _repo(raw).list_jobs_for_admin()

@router.get("/admin/ai-calls", tags=["admin"])
async def admin_ai_calls(raw: Request, _: Annotated[None, Depends(require_admin_token)]) -> tuple[dict[str, object], ...]:
    return await _repo(raw).list_ai_calls_for_admin()

@router.get("/admin/rule-version", tags=["admin"])
async def admin_rule_version(_: Annotated[None, Depends(require_admin_token)]) -> dict[str, str]:
    return {"rule_version": "tieguanyin-rules-v1"}

@router.get("/config/public", response_model=PublicConfig)
async def get_public_config() -> PublicConfig: return PublicConfig()


@router.get("/me", response_model=CurrentUserResponse, tags=["auth"])
async def get_current_user_profile(current_user: CurrentUser) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=current_user.id,
        authenticated=True,
        created_at=current_user.created_at,
    )

@router.post("/brew-feedback/analyze", response_model=BrewFeedbackAnalysisResponse)
async def analyze_brew_feedback(
    request: BrewFeedbackAnalysisRequest,
    client_id: ClientId,
    idempotency_key: IdempotencyKey,
    raw: Request,
) -> BrewFeedbackAnalysisResponse:
    replay_key = (client_id, idempotency_key)
    payload_hash = request_hash(request.model_dump(mode="json"))
    repository = getattr(raw.app.state, "repository", None)

    if repository is not None:
        replay = await repository.get_brew_feedback_replay(
            client_id=client_id,
            client_feedback_id=request.client_feedback_id,
            idempotency_key=idempotency_key,
        )
        if replay is not None:
            if replay["request_hash"] != payload_hash:
                raise HTTPException(status_code=409, detail="feedback_duplicate")
            return BrewFeedbackAnalysisResponse.model_validate(replay["response"])
    else:
        # A database-free FakeProvider app remains useful for isolated tests.
        replay = raw.app.state.feedback_replays.get(replay_key)
        if replay is not None:
            if replay[0] != payload_hash:
                raise HTTPException(status_code=409, detail="feedback_duplicate")
            return replay[2]
        feedback_key = (client_id, request.client_feedback_id)
        prior_key = raw.app.state.feedback_client_ids.get(feedback_key)
        if prior_key is not None and prior_key != replay_key:
            raise HTTPException(status_code=409, detail="feedback_duplicate")
    try:
        response = await raw.app.state.feedback_provider.explain_brew_feedback(request)
        if not isinstance(response, BrewFeedbackAnalysisResponse):
            raise ValueError("invalid feedback provider response")
        if repository is not None:
            persisted = await repository.save_brew_feedback_replay(
                client_id=client_id,
                client_feedback_id=request.client_feedback_id,
                idempotency_key=idempotency_key,
                request_hash=payload_hash,
                response=response.model_dump(mode="json"),
            )
            if persisted is None or persisted["request_hash"] != payload_hash:
                raise HTTPException(status_code=409, detail="feedback_duplicate")
            return BrewFeedbackAnalysisResponse.model_validate(persisted["response"])
        raw.app.state.feedback_replays[replay_key] = (payload_hash, request.client_feedback_id, response)
        raw.app.state.feedback_client_ids[(client_id, request.client_feedback_id)] = replay_key
        return response
    except HTTPException:
        raise
    except Exception as exc:
        # The feedback bridge is deliberately stateless: it never persists a
        # partial preference result, and it must not disclose provider details.
        raise HTTPException(status_code=503, detail="feedback_analysis_failed") from exc

@router.post("/selection-sessions", response_model=SelectionSession, status_code=201)
async def create_selection_session(request: CreateSelectionSessionRequest, client_id: ClientId, idempotency_key: IdempotencyKey, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> SelectionSession:
    result, created = await _service(raw).create_session(
        client_id=client_id, idempotency_key=idempotency_key, need=request.need,
        recent_preference_evidence=request.recent_preference_evidence,
    )
    if created:
        _emit(raw, event_name="need_submitted", resource_id=result.id, analytics_session=x_analytics_session_id,
              metadata={"has_budget": bool(request.need.budget_text), "has_sensory_need": bool(request.need.taste_text)})
    return result

@router.get("/selection-sessions/{session_id}", response_model=SelectionSession)
async def get_selection_session(session_id: UUID, client_id: ClientId, raw: Request) -> SelectionSession:
    return await _service(raw).get_session(client_id=client_id, session_id=session_id)

@router.patch("/selection-sessions/{session_id}", response_model=SelectionSession)
async def update_selection_session(session_id: UUID, request: UpdateSelectionNeedRequest, client_id: ClientId, raw: Request) -> SelectionSession:
    return await _service(raw).update_session_need(client_id=client_id, session_id=session_id, need=request.need, recent_preference_evidence=request.recent_preference_evidence)

@router.post("/selection-sessions/{session_id}/candidates", response_model=Candidate, status_code=201)
async def create_candidate(session_id: UUID, request: CreateCandidateRequest, client_id: ClientId, idempotency_key: IdempotencyKey, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> Candidate:
    result, created = await _service(raw).create_candidate(
        client_id=client_id, session_id=session_id, idempotency_key=idempotency_key, request=request
    )
    if created:
        _emit(raw, event_name="candidate_created", resource_id=result.id, analytics_session=x_analytics_session_id, candidate_id=result.id)
    return result

@router.get("/selection-sessions/{session_id}/candidates", response_model=tuple[Candidate, ...])
async def list_candidates(session_id: UUID, client_id: ClientId, raw: Request) -> tuple[Candidate, ...]:
    return await _service(raw).list_candidates(client_id=client_id, session_id=session_id)

@router.get("/selection-sessions/{session_id}/snapshot")
async def get_selection_snapshot(session_id: UUID, client_id: ClientId, raw: Request) -> dict[str, object]:
    return await _repo(raw).selection_snapshot_for_client(session_id=session_id, client_id=client_id)

@router.delete("/candidates/{candidate_id}", status_code=204)
async def delete_candidate(candidate_id: UUID, client_id: ClientId, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> None:
    await _service(raw).delete_candidate(client_id=client_id, candidate_id=candidate_id, storage=raw.app.state.temporary_storage)
    _emit(raw, event_name="candidate_deleted", resource_id=candidate_id, analytics_session=x_analytics_session_id, candidate_id=candidate_id)

@router.post("/candidates/{candidate_id}/images", response_model=UploadCandidateImageResponse, status_code=201)
async def upload_candidate_image(candidate_id: UUID, client_id: ClientId, idempotency_key: IdempotencyKey, raw: Request, file: Annotated[UploadFile, File()], x_analytics_session_id: AnalyticsSession = None) -> UploadCandidateImageResponse:
    data = await file.read(5_242_881)
    try:
        result, created = await _service(raw).upload_image(client_id=client_id, candidate_id=candidate_id, idempotency_key=idempotency_key, data=data, declared_content_type=file.content_type or '', storage=raw.app.state.temporary_storage, task_runner=raw.app.state.task_runner, provider=raw.app.state.provider)
        if created:
            _emit(raw, event_name="candidate_image_added", resource_id=result.image.id, analytics_session=x_analytics_session_id, candidate_id=candidate_id)
        return result
    except ValueError as exc:
        code = getattr(exc, "error_code", ErrorCode.UNSAFE_OR_CORRUPT_IMAGE)
        raise HTTPException(422, code.value) from exc

@router.get("/candidate-images/{candidate_image_id}", response_model=CandidateImageMetadata)
async def get_candidate_image(candidate_image_id: UUID, client_id: ClientId, raw: Request) -> CandidateImageMetadata:
    return await _service(raw).get_image_metadata(
        client_id=client_id, image_id=candidate_image_id
    )

@router.delete("/candidate-images/{candidate_image_id}", status_code=204)
async def delete_candidate_image(candidate_image_id: UUID, client_id: ClientId, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> None:
    await _service(raw).delete_image(
        client_id=client_id, image_id=candidate_image_id, storage=raw.app.state.temporary_storage
    )
    _emit(raw, event_name="candidate_image_removed", resource_id=candidate_image_id, analytics_session=x_analytics_session_id)

@router.get("/jobs/{job_id}", response_model=AnalysisJobResponse)
async def get_job(job_id: UUID, client_id: ClientId, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> AnalysisJobResponse:
    result = await _service(raw).get_job(client_id=client_id, job_id=job_id)
    return result

@router.post("/selection-sessions/{session_id}/analyze", response_model=AnalysisJobResponse, status_code=201)
async def analyze_selection_session(session_id: UUID, client_id: ClientId, idempotency_key: IdempotencyKey, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> AnalysisJobResponse:
    staged = await _service(raw).start_staged_extractions(
        session_id=session_id,
        client_id=client_id,
        storage=raw.app.state.temporary_storage,
        task_runner=raw.app.state.task_runner,
        provider=raw.app.state.provider,
    )
    if staged:
        # The established public response is an AnalysisJobResponse.  The
        # browser ignores this kickoff value and continues polling each
        # candidate job; a later call creates the session-decision job once
        # all candidate extractions are complete.
        result = staged[0]
        _emit(raw, event_name="analysis_started", resource_id=result.id, analytics_session=x_analytics_session_id, candidate_id=result.candidate_id, stage=result.stage.value, metadata={"processing_mode": result.processing_mode.value})
        return result
    queued = await _service(raw).list_staged_extractions(session_id=session_id, client_id=client_id)
    if queued:
        # Replay returns the existing business anchor without redispatching or
        # emitting a second server-authoritative transition.
        return queued[0]
    job = await _decision_service(raw).analyze(session_id=session_id, client_id=client_id, idempotency_key=idempotency_key, task_runner=raw.app.state.task_runner, analytics_session_id=parse_analytics_session(x_analytics_session_id))
    result = _job(job)
    return result

def _decision(version, decisions):
    return DecisionVersionResponse(
        id=version["id"], selection_session_id=version["selection_session_id"], anonymous_client_id=version["anonymous_client_id"],
        version=version["version"], status=version["status"], rule_version=version["rule_version"], top_candidate_id=version["top_candidate_id"], created_at=version["created_at"],
        candidate_decisions=tuple(CandidateDecision(id=row["id"], decision_version_id=row["decision_version_id"], candidate_id=row["candidate_id"], extraction_version_id=row["extraction_version_id"], action_bucket=row["action_bucket"], rank_within_bucket=row["rank_within_bucket"], overall_order=row["overall_order"], reasons=tuple(row["reasons"]), risk_flags=tuple(row["risk_flags"]), missing_critical_fields=tuple(row["missing_critical_fields"]), score_components={key: value for key, value in row["score_components"].items() if key != "personal_low_confidence"}, created_at=row["created_at"]) for row in decisions),
    )

@router.get("/decision-versions/{version_id}", response_model=DecisionVersionResponse)
async def get_decision_version(version_id: UUID, client_id: ClientId, raw: Request) -> DecisionVersionResponse:
    version, decisions = await _repo(raw).get_decision_version_for_client(version_id=version_id, client_id=client_id)
    return _decision(version, decisions)

@router.post("/decision-versions/{version_id}/questions", response_model=tuple[FollowupQuestion, ...], status_code=201)
async def generate_followup_questions(version_id: UUID, client_id: ClientId, idempotency_key: IdempotencyKey, raw: Request) -> tuple[FollowupQuestion, ...]:
    return await _question_service(raw).generate(version_id=version_id, client_id=client_id, idempotency_key=idempotency_key)

@router.get("/decision-versions/{version_id}/questions", response_model=tuple[FollowupQuestion, ...])
async def get_followup_questions(version_id: UUID, client_id: ClientId, raw: Request) -> tuple[FollowupQuestion, ...]:
    return await _question_service(raw).list_current(version_id=version_id, client_id=client_id)

@router.post("/selection-sessions/{session_id}/merchant-replies", response_model=MerchantReply, status_code=201)
async def create_merchant_reply(session_id: UUID, request: CreateMerchantReplyRequest, client_id: ClientId, idempotency_key: IdempotencyKey, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> MerchantReply:
    result = await _merchant_reply_service(raw).submit(session_id=session_id, client_id=client_id, idempotency_key=idempotency_key, request=request, analytics_session_id=parse_analytics_session(x_analytics_session_id))
    return result

@router.get("/merchant-replies/{reply_id}", response_model=MerchantReply)
async def get_merchant_reply(reply_id: UUID, client_id: ClientId, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> MerchantReply:
    result = await _merchant_reply_service(raw).get(reply_id=reply_id, client_id=client_id)
    return result

@router.post("/selection-sessions/{session_id}/rejudge", response_model=AnalysisJobResponse, status_code=201)
async def rejudge_merchant_reply(session_id: UUID, request: CreateRejudgeRequest, client_id: ClientId, idempotency_key: IdempotencyKey, raw: Request, x_analytics_session_id: AnalyticsSession = None) -> AnalysisJobResponse:
    job = await _merchant_reply_service(raw).rejudge(
        session_id=session_id, client_id=client_id,
        idempotency_key=idempotency_key, task_runner=raw.app.state.task_runner,
        analytics_session_id=parse_analytics_session(x_analytics_session_id),
    )
    result = _job(job)
    return result

@router.get("/decision-deltas/{delta_id}", response_model=DecisionDelta)
async def get_decision_delta(delta_id: UUID, client_id: ClientId, raw: Request) -> DecisionDelta:
    return DecisionDelta.model_validate(
        await _repo(raw).get_decision_delta_for_client(delta_id=delta_id, client_id=client_id)
    )

@router.get("/selection-sessions/{session_id}/current-decision", response_model=DecisionVersionResponse)
async def get_current_decision(session_id: UUID, client_id: ClientId, raw: Request) -> DecisionVersionResponse:
    result = await _repo(raw).get_current_decision_for_session(session_id=session_id, client_id=client_id)
    if result is None: raise HTTPException(404, "not_found")
    return _decision(*result)


@router.get("/selection-sessions/{session_id}/answer")
async def get_selection_answer(session_id: UUID, client_id: ClientId, raw: Request) -> dict[str, object]:
    """User-facing presentation contract; raw Evidence stays behind this boundary."""
    inputs = await _repo(raw).answer_contract_inputs_for_session(session_id=session_id, client_id=client_id)
    if inputs is None:
        raise HTTPException(404, "not_found")
    return build_selection_answer(version=inputs[0], decisions=inputs[1], candidates=inputs[2], questions=inputs[3])

@router.get("/extraction-versions/{extraction_version_id}", response_model=ExtractionVersionResponse)
async def get_extraction_version(extraction_version_id: UUID, client_id: ClientId, raw: Request) -> ExtractionVersionResponse:
    row,evidence=await _repo(raw).get_extraction_version_for_client(version_id=extraction_version_id, client_id=client_id)
    return ExtractionVersionResponse(id=row['id'], candidate_id=row['candidate_id'], source_image_id=row['source_image_id'], source_image_ids=tuple(row['source_image_ids']), status=row['status'], schema_version=row['schema_version'], evidence_items=tuple(EvidenceItem.model_validate(x) for x in evidence), created_at=row['created_at'])

@router.get("/candidates/{candidate_id}/current-extraction", response_model=ExtractionVersionResponse)
async def get_current_extraction(candidate_id: UUID, client_id: ClientId, raw: Request) -> ExtractionVersionResponse:
    result=await _repo(raw).get_current_extraction_for_candidate(candidate_id=candidate_id, client_id=client_id)
    if result is None: raise HTTPException(404, 'not_found')
    row,evidence=result; return ExtractionVersionResponse(id=row['id'], candidate_id=row['candidate_id'], source_image_id=row['source_image_id'], source_image_ids=tuple(row['source_image_ids']), status=row['status'], schema_version=row['schema_version'], evidence_items=tuple(EvidenceItem.model_validate(x) for x in evidence), created_at=row['created_at'])

@router.post("/candidates/{candidate_id}/extraction-jobs", response_model=AnalysisJobResponse, status_code=201)
async def retry_extraction_job(candidate_id: UUID, client_id: ClientId, idempotency_key: IdempotencyKey, raw: Request) -> AnalysisJobResponse:
    try:
        return await _service(raw).retry_job(client_id=client_id, candidate_id=candidate_id, idempotency_key=idempotency_key, storage=raw.app.state.temporary_storage, task_runner=raw.app.state.task_runner, provider=raw.app.state.provider)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
