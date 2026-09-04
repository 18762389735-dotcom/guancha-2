from __future__ import annotations

from uuid import UUID, uuid4

from guancha_api.auth.models import OwnerContext, repository_owner, resolve_owner
from guancha_api.domain.tieguanyin.fixture_catalog import FixtureCatalog
from guancha_api.repositories.idempotency import request_hash
from guancha_api.application.task_runners import InProcessTaskRunner, ManualTaskRunner
from guancha_api.domain.tieguanyin.decision import evaluate_candidate, rank_within_buckets
from guancha_api.domain.tieguanyin.rules.rule_schema import load_approved_rules
from guancha_api.repositories.postgres import PostgresPhase2Repository, StoredJob
from guancha_api.schemas.contracts import CreateMerchantReplyRequest, ErrorCode, MerchantReply
from guancha_api.providers.merchant_reply import FakeMerchantReplyReasoningProvider, MerchantReplyParse, MerchantReplyReasoningProvider
from guancha_api.providers.merchant_reply_mimo import MiMoMerchantReplyReasoningProvider
from guancha_api.product_events import ProductEventSink, safe_emit_server


class MerchantReplyService:
    def __init__(self, repository: PostgresPhase2Repository, provider: MerchantReplyReasoningProvider | None = None, event_sink: ProductEventSink | None = None) -> None:
        self.repository = repository
        self.provider = provider or FakeMerchantReplyReasoningProvider()
        self.event_sink = event_sink

    async def submit(
        self, *, session_id: UUID, idempotency_key: UUID, request: CreateMerchantReplyRequest,
        analytics_session_id: UUID | None = None,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> MerchantReply:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        row, created = await self.repository.create_or_replay_merchant_reply(
            reply_id=uuid4(), session_id=session_id, client_id=repository_owner(request_owner),
            decision_version_id=request.decision_version_id, followup_question_id=request.followup_question_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash({"session_id": str(session_id), **request.model_dump(mode="json")}), raw_text=request.raw_text,
        )
        result = self._dto(row)
        if created and self.event_sink:
            safe_emit_server(self.event_sink, event_name="merchant_reply_submitted", resource_id=result.id, anonymous_session_id=analytics_session_id, candidate_id=result.candidate_id, decision_version_id=result.decision_version_id)
        return result

    async def get(self, *, reply_id: UUID, owner: OwnerContext | None = None, client_id: UUID | None = None) -> MerchantReply:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        return self._dto(await self.repository.get_merchant_reply_for_client(reply_id=reply_id, client_id=repository_owner(request_owner)))

    async def rejudge(
        self, *, session_id: UUID, idempotency_key: UUID,
        task_runner: InProcessTaskRunner | ManualTaskRunner,
        allow_demo_fallback: bool = False,
        analytics_session_id: UUID | None = None,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> StoredJob:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        repo_owner = repository_owner(request_owner)
        # The public action is session-scoped.  An anchor is retained only as
        # an internal audit/linkage field for the legacy non-null Job column.
        reply_id = await self.repository.aggregate_rejudge_anchor(session_id=session_id, client_id=repo_owner)
        fingerprint = request_hash({"session_id": str(session_id), "operation": "aggregate_merchant_rejudge"})
        job, created = await self.repository.create_merchant_rejudgement_job(
            job_id=uuid4(), session_id=session_id, client_id=repo_owner, reply_id=reply_id,
            idempotency_key=idempotency_key, request_hash=fingerprint,
        )
        if created:
            accepted = await task_runner.enqueue(
                job_id=job.id,
                task=lambda: self.run_rejudge(job_id=job.id, reply_id=reply_id, owner=request_owner, fingerprint=fingerprint, allow_demo_fallback=allow_demo_fallback, analytics_session_id=analytics_session_id),
            )
            if accepted and self.event_sink:
                safe_emit_server(self.event_sink, event_name="rejudge_started", resource_id=job.id, anonymous_session_id=analytics_session_id, stage="queued", metadata={"processing_mode": job.processing_mode.value if job.processing_mode else "test-fixture"})
            if accepted and isinstance(task_runner, InProcessTaskRunner):
                job = await self.repository.get_job_for_client(
                    job_id=job.id, client_id=repo_owner
                )
        return job

    async def run_rejudge(
        self, *, job_id: UUID, reply_id: UUID, fingerprint: str,
        allow_demo_fallback: bool = False,
        analytics_session_id: UUID | None = None,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> None:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        repo_owner = repository_owner(request_owner)
        if not await self.repository.claim_job(job_id=job_id):
            return
        try:
            # Parse every saved answer first.  Saving one answer must never stale
            # the parent decision and prevent a reply for another candidate.
            _anchor, _parent, _inputs, replies, _claims = await self.repository.merchant_rejudgement_batch(
                anchor_reply_id=reply_id, client_id=repo_owner
            )
            for saved in replies:
                if saved["processing_status"] == "queued":
                    await self.parse(reply_id=saved["id"], owner=request_owner, allow_demo_fallback=allow_demo_fallback, analytics_session_id=analytics_session_id)
            reply_context, parent, inputs, replies, all_claims = await self.repository.merchant_rejudgement_batch(
                anchor_reply_id=reply_id, client_id=repo_owner
            )
            parsed_claims = [claim for claim in all_claims if claim["normalized_value"] is not None]
            for item in inputs:
                item["evidence"] = list(item["evidence"])
                for claim in parsed_claims:
                    if item["candidate_id"] != claim["candidate_id"]:
                        continue
                    item["evidence"].extend(
                        [{
                            "field_name": claim["field_key"], "normalized_value": claim["normalized_value"],
                            "information_status": claim["information_status"], "source_type": "merchant-claim",
                            "verification_status": "unverified", "evidence_strength": claim["evidence_strength"],
                        }]
                    )
            drafts = [
                evaluate_candidate(
                    candidate_id=item["candidate_id"], extraction_version_id=item["extraction_version_id"],
                    need=parent["need_snapshot"], evidence=item["evidence"], rules=load_approved_rules(),
                    recent_preference_evidence=list(parent.get("recent_preference_evidence") or []),
                )
                for item in inputs
            ]
            ranked = rank_within_buckets(drafts)
            bucket_ranks: dict[object, int] = {}
            decisions: list[dict[str, object]] = []
            for overall_order, draft in enumerate(ranked, start=1):
                bucket_ranks[draft.action_bucket] = bucket_ranks.get(draft.action_bucket, 0) + 1
                decisions.append({
                    "id": uuid4(), "candidate_id": draft.candidate_id, "extraction_version_id": draft.extraction_version_id,
                    "action_bucket": draft.action_bucket.value, "rank_within_bucket": bucket_ranks[draft.action_bucket],
                    "overall_order": overall_order, "reasons": list(draft.reasons), "risk_flags": list(draft.risk_flags),
                    "missing_critical_fields": list(draft.missing_critical_fields), "score_components": draft.score_components,
                    "internal_score": draft.internal_score,
                })
            old_decisions = await self.repository.get_decision_version_for_client(version_id=reply_context["decision_version_id"], client_id=repo_owner)
            old_top = old_decisions[0]["top_candidate_id"]
            old_by_candidate = {row["candidate_id"]: row for row in old_decisions[1]}
            changed = [str(row["candidate_id"]) for row in decisions if old_by_candidate.get(row["candidate_id"], {}).get("action_bucket") != row["action_bucket"]]
            delta = {
                "id": uuid4(), "added_facts": [claim["field_key"] for claim in parsed_claims],
                "updated_fields": [claim["field_key"] for claim in parsed_claims],
                "unresolved_fields": [reply["field_key"] for reply in replies if reply["parse_status"] in {"evasive", "not-answered", "partially-answered"}],
                "resolved_risks": [], "added_risks": [claim["field_key"] for claim in parsed_claims if claim["information_status"] == "conflict"],
                "ranking_changed": old_top != decisions[0]["candidate_id"], "action_tier_changed": bool(changed),
                "old_top_candidate_id": old_top, "new_top_candidate_id": decisions[0]["candidate_id"],
                "explanation": "Saved merchant replies were aggregated and the current decision was recomputed.",
            }
            version_id = uuid4()
            await self.repository.complete_aggregate_merchant_rejudgement(
                job_id=job_id, client_id=repo_owner, anchor_reply_id=reply_id,
                reply_ids=tuple(reply["id"] for reply in replies), version_id=version_id, decisions=decisions,
                delta=delta, input_fingerprint=fingerprint,
            )
        except Exception:
            await self.repository.fail_aggregate_merchant_rejudgement(job_id=job_id, error_code=ErrorCode.AI_SCHEMA_INVALID)
            if self.event_sink:
                safe_emit_server(self.event_sink, event_name="rejudge_failed", resource_id=job_id, anonymous_session_id=analytics_session_id, stage="failed", error_category=ErrorCode.AI_SCHEMA_INVALID.value, metadata={"failure_category": "REJUDGE_INCONSISTENT"})
            raise
        if self.event_sink:
            safe_emit_server(self.event_sink, event_name="rejudge_completed", resource_id=job_id, anonymous_session_id=analytics_session_id, decision_version_id=version_id, stage="completed")

    async def parse(
        self, *, reply_id: UUID, analytics_session_id: UUID | None = None,
        allow_demo_fallback: bool = False,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> None:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        repo_owner = repository_owner(request_owner)
        claimed = await self.repository.claim_merchant_reply_for_parse(reply_id=reply_id, client_id=repo_owner)
        if claimed is None:
            return
        reply, product_evidence = claimed
        try:
            parsed = await self.provider.parse_merchant_reply(
                field_key=reply["field_key"], raw_text=reply["raw_text"], product_evidence=product_evidence
            )
        except Exception:
            parsed = self._demo_reply_fallback(
                field_key=str(reply["field_key"]),
                raw_text=str(reply["raw_text"]),
                allow_demo_fallback=allow_demo_fallback,
            )
            if parsed is None:
                await self.repository.fail_merchant_reply_parse(reply_id=reply_id, client_id=repo_owner)
                raise
        try:
            await self.repository.persist_merchant_reply_parse(
                reply_id=reply_id, client_id=repo_owner, parsed_status=parsed.reply_status, claims=parsed.claims
            )
        except Exception:
            await self.repository.fail_merchant_reply_parse(reply_id=reply_id, client_id=repo_owner)
            raise
        if self.event_sink and parsed.reply_status in {"evasive", "not-answered", "partially-answered"}:
            safe_emit_server(
                self.event_sink, event_name="merchant_reply_unusable", resource_id=reply_id,
                anonymous_session_id=analytics_session_id,
                candidate_id=reply.get("candidate_id"), decision_version_id=reply.get("decision_version_id"),
            )

    def _demo_reply_fallback(
        self, *, field_key: str, raw_text: str, allow_demo_fallback: bool
    ) -> MerchantReplyParse | None:
        """Use the existing merchant fixture only for an explicit sample flow."""
        if not allow_demo_fallback or not isinstance(self.provider, MiMoMerchantReplyReasoningProvider):
            return None
        fixture = FixtureCatalog().load_merchant_reply("merchant-answered")
        claim = next((item for item in fixture.expected_claims if item.get("field_name") == field_key), None)
        if claim is None:
            return None
        normalized = claim.get("normalized_value")
        if not isinstance(normalized, str) or not normalized:
            return None
        return MerchantReplyParse(
            reply_status="answered",
            answered_fields=(field_key,),
            claims=(
                {
                    "field_key": field_key,
                    "raw_text": str(claim.get("raw_text") or raw_text),
                    "normalized_value": normalized,
                },
            ),
            unresolved_fields=(), conflicts=(), coverage=1, ambiguity=0,
            should_rejudge=True,
        )

    @staticmethod
    def _dto(row: dict[str, object]) -> MerchantReply:
        return MerchantReply(id=row["id"], selection_session_id=row["selection_session_id"], decision_version_id=row["decision_version_id"], followup_question_id=row["followup_question_id"], candidate_id=row["candidate_id"], raw_text=row["raw_text"], status=row["status"], processing_status=row["processing_status"], parse_status=row.get("parse_status"), created_at=row["created_at"])
