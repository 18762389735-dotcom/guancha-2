from __future__ import annotations

from uuid import UUID, uuid4

from guancha_api.auth.models import OwnerContext, repository_owner, resolve_owner
from guancha_api.application.task_runners import InProcessTaskRunner, ManualTaskRunner
from guancha_api.domain.tieguanyin.decision import evaluate_candidate, rank_within_buckets
from guancha_api.domain.tieguanyin.rules.rule_schema import load_approved_rules
from guancha_api.repositories.idempotency import request_hash
from guancha_api.repositories.postgres import PostgresPhase2Repository, StoredJob
from guancha_api.product_events import ProductEventSink, safe_emit_server


class SessionDecisionService:
    """Coordinates a deterministic, one-session decision job outside HTTP routes."""

    def __init__(self, repository: PostgresPhase2Repository, event_sink: ProductEventSink | None = None) -> None:
        self.repository = repository
        self.event_sink = event_sink

    async def analyze(
        self, *, session_id: UUID, idempotency_key: UUID,
        task_runner: InProcessTaskRunner | ManualTaskRunner,
        analytics_session_id: UUID | None = None,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> StoredJob:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        session, inputs = await self.repository.decision_inputs_for_session(session_id=session_id, client_id=repository_owner(request_owner))
        expected_ids = tuple(item["extraction_version_id"] for item in inputs)
        need_snapshot = dict(session["need"])
        recent_preference_evidence = list(session.get("recent_preference_evidence") or [])
        fingerprint = request_hash({"need": need_snapshot, "recent_preference_evidence": recent_preference_evidence, "candidate_extraction_version_ids": [str(value) for value in expected_ids], "rule_version": "v1"})
        job, created = await self.repository.create_session_decision_job(
            job_id=uuid4(), session_id=session_id, client_id=repository_owner(request_owner), idempotency_key=idempotency_key,
            request_hash=fingerprint, need_snapshot=need_snapshot, expected_extraction_version_ids=expected_ids,
        )
        if created:
            accepted = await task_runner.enqueue(job_id=job.id, task=lambda: self.run(job_id=job.id, session_id=session_id, owner=request_owner, fingerprint=fingerprint, need_snapshot=need_snapshot, inputs_snapshot=inputs, recent_preference_evidence=recent_preference_evidence, analytics_session_id=analytics_session_id))
            if accepted and self.event_sink:
                safe_emit_server(self.event_sink, event_name="analysis_started", resource_id=job.id, anonymous_session_id=analytics_session_id, stage="queued", metadata={"processing_mode": job.processing_mode.value if job.processing_mode else "test-fixture"})
            if accepted and isinstance(task_runner, InProcessTaskRunner):
                job = await self.repository.get_job_for_client(
                    job_id=job.id, client_id=repository_owner(request_owner)
                )
        return job

    async def run(
        self, *, job_id: UUID, session_id: UUID, fingerprint: str,
        need_snapshot: dict[str, object], inputs_snapshot: list[dict[str, object]],
        recent_preference_evidence: list[dict[str, object]] | None = None,
        analytics_session_id: UUID | None = None,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> None:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        if not await self.repository.claim_job(job_id=job_id):
            return
        try:
            rules = load_approved_rules()
            drafts = [evaluate_candidate(candidate_id=item["candidate_id"], extraction_version_id=item["extraction_version_id"], need=need_snapshot, evidence=item["evidence"], rules=rules, recent_preference_evidence=recent_preference_evidence) for item in inputs_snapshot]
            ranked = rank_within_buckets(drafts)
            bucket_ranks: dict[object, int] = {}
            decisions = []
            for overall_order, draft in enumerate(ranked, start=1):
                bucket_ranks[draft.action_bucket] = bucket_ranks.get(draft.action_bucket, 0) + 1
                decisions.append({"id": uuid4(), "candidate_id": draft.candidate_id, "extraction_version_id": draft.extraction_version_id,
                    "action_bucket": draft.action_bucket.value, "rank_within_bucket": bucket_ranks[draft.action_bucket], "overall_order": overall_order,
                    "reasons": list(draft.reasons), "risk_flags": list(draft.risk_flags), "missing_critical_fields": list(draft.missing_critical_fields),
                    "score_components": draft.score_components, "internal_score": draft.internal_score})
            version_id = uuid4()
            await self.repository.complete_session_decision_job(job_id=job_id, session_id=session_id, client_id=repository_owner(request_owner), version_id=version_id, rule_version="v1", input_fingerprint=fingerprint, decisions=decisions)
        except Exception:
            from guancha_api.schemas.contracts import ErrorCode
            await self.repository.fail_session_decision_job(job_id=job_id, error_code=ErrorCode.AI_SCHEMA_INVALID)
            if self.event_sink:
                safe_emit_server(self.event_sink, event_name="analysis_failed", resource_id=job_id, anonymous_session_id=analytics_session_id, stage="failed", error_category=ErrorCode.AI_SCHEMA_INVALID.value, metadata={"failure_category": "PROVIDER_ERROR"})
            raise
        if self.event_sink:
            safe_emit_server(self.event_sink, event_name="analysis_completed", resource_id=job_id, anonymous_session_id=analytics_session_id, decision_version_id=version_id, stage="completed")
