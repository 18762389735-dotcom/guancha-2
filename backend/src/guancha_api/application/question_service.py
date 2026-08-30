from __future__ import annotations

from uuid import UUID, uuid4

from guancha_api.auth.models import OwnerContext, repository_owner, resolve_owner
from guancha_api.domain.tieguanyin.questioning import ANSWER_BRANCHES, simulate_decision_branch
from guancha_api.domain.tieguanyin.merchant_fields import merchant_field_label
from guancha_api.domain.tieguanyin.question_value_config import load_question_value_config
from guancha_api.domain.tieguanyin.rules.rule_schema import load_approved_rules
from guancha_api.providers.reasoning import FakeReasoningProvider, ReasoningCandidate, ReasoningProvider
from guancha_api.repositories.postgres import PostgresPhase2Repository, QuestionGenerationFailed
from guancha_api.schemas.contracts import FollowupQuestion


def _question_text(field_key: str) -> str:
    label = merchant_field_label(field_key)
    return f"请问这款茶{label}？" if label.startswith("是否") else f"请问这款茶的{label}是什么？"


class QuestionGenerationService:
    def __init__(self, repository: PostgresPhase2Repository, provider: ReasoningProvider | None = None) -> None:
        self.repository = repository
        self.provider = provider or FakeReasoningProvider()

    async def generate(
        self, *, version_id: UUID, idempotency_key: UUID,
        owner: OwnerContext | None = None, client_id: UUID | None = None,
    ) -> tuple[FollowupQuestion, ...]:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        repo_owner = repository_owner(request_owner)
        version, decisions, inputs = await self.repository.question_context_for_current_decision(version_id=version_id, client_id=repo_owner)
        if not await self.repository.claim_question_generation(version_id=version_id, client_id=repo_owner, idempotency_key=idempotency_key):
            return await self.list_current(version_id=version_id, owner=request_owner)
        try:
            candidates = self._candidates(version=version, decisions=decisions, inputs=inputs)
            expressed = await self.provider.generate_questions(tuple(candidates[:3]))
            allowed = {(item.candidate_id, item.field_key): item for item in candidates}
            selected = []
            seen: set[tuple[object, str]] = set()
            for item in expressed:
                key = (item.candidate_id, item.field_key)
                if key not in allowed:
                    raise ValueError("Reasoning provider selected an unsupported question candidate")
                if key not in seen and len(selected) < 3:
                    seen.add(key); selected.append(allowed[key])
            if len(selected) > 3:
                raise ValueError("Reasoning provider selected an unsupported question candidate")
            records = [self._record(version, item, candidates) for item in selected]
            await self.repository.persist_followup_questions(version_id=version_id, client_id=repo_owner, status="completed", error_code=None, questions=records)
        except Exception as error:
            await self.repository.persist_followup_questions(version_id=version_id, client_id=repo_owner, status="failed", error_code="ai_schema_invalid", questions=[])
            raise QuestionGenerationFailed("Question generation failed") from error
        return await self.list_current(version_id=version_id, owner=request_owner)

    async def list_current(
        self, *, version_id: UUID, owner: OwnerContext | None = None, client_id: UUID | None = None
    ) -> tuple[FollowupQuestion, ...]:
        request_owner = resolve_owner(owner=owner, client_id=client_id)
        return tuple(self._dto(item) for item in await self.repository.get_followup_questions_for_current_decision(version_id=version_id, client_id=repository_owner(request_owner)))

    def _candidates(self, *, version: dict[str, object], decisions: list[dict[str, object]], inputs: list[dict[str, object]]) -> list[ReasoningCandidate]:
        rules = load_approved_rules()
        if not rules or any(rule.rule_version != version["rule_version"] for rule in rules):
            raise ValueError("Decision rule version does not match the stored snapshot")
        config = load_question_value_config()
        need = dict(version["need_snapshot"])
        drafts: list[tuple[ReasoningCandidate, int, int, int, int]] = []
        decisions_by_candidate = {item["candidate_id"]: item for item in decisions}
        for item in inputs:
            decision = decisions_by_candidate[item["candidate_id"]]
            known = {
                row["field_name"]
                for row in item["evidence"]
                if row.get("normalized_value") not in (None, "", "unknown")
                and row.get("information_status") in {"explicit", "inferred"}
            }
            missing = set(decision["missing_critical_fields"])
            for field_key, branches in ANSWER_BRANCHES.items():
                if field_key in known and field_key not in missing:
                    continue
                impacts = [simulate_decision_branch(need=need, inputs=inputs, original_decisions=decisions, target_candidate_id=item["candidate_id"], field_key=field_key, assumed_value=branch, rules=rules) for branch in branches]
                max_impact = max(impact.impact_level for impact in impacts)
                if max_impact == 0:
                    continue
                relevance = config.field_relevance[field_key]
                uncertainty = 2 if field_key in missing else 1
                answerability = config.field_answerability[field_key]
                interaction_cost = config.interaction_cost
                duplicate_penalty = 0
                value = max_impact * relevance * uncertainty * answerability - duplicate_penalty - interaction_cost
                if value < config.minimum_value_score:
                    continue
                affected = _affected(impacts)
                candidate = ReasoningCandidate(item["candidate_id"], field_key, _question_text(field_key), _reason(affected), affected, branches, max_impact, value, {"max_impact_level": max_impact, "user_relevance": relevance, "uncertainty": uncertainty, "answerability": answerability, "duplicate_penalty": duplicate_penalty, "interaction_cost": interaction_cost, "value_score": value})
                drafts.append((candidate, max_impact, relevance, answerability, interaction_cost))
        drafts.sort(key=lambda value: (-value[1], -value[2], -value[3], value[4], str(value[0].candidate_id), value[0].field_key))
        return [item[0] for item in drafts]

    def _record(self, version: dict[str, object], item: ReasoningCandidate, all_candidates: list[ReasoningCandidate]) -> dict[str, object]:
        return {"id": uuid4(), "selection_session_id": version["selection_session_id"], "candidate_id": item.candidate_id,
                "field_key": item.field_key, "question_text": item.question_text, "reason": item.reason,
                "affected_decision": list(item.affected_decision), "answer_branches": list(item.answer_branches), "priority": item.priority,
                "value_score": item.value_score, "value_components": item.value_components}

    @staticmethod
    def _dto(row: dict[str, object]) -> FollowupQuestion:
        return FollowupQuestion(id=row["id"], decision_version_id=row["decision_version_id"], selection_session_id=row["selection_session_id"], candidate_id=row["candidate_id"], field_key=row["field_key"], question_text=row["question_text"], reason=row["reason"], affected_decision=tuple(row["affected_decision"]), answer_branches=tuple(row["answer_branches"]), priority=row["priority"], status=row["status"], created_at=row["created_at"])


def _affected(impacts: list[object]) -> tuple[str, ...]:
    labels: list[str] = []
    if any(item.action_bucket_changed for item in impacts): labels.append("可能改变行动建议")
    if any(item.top_candidate_changed for item in impacts): labels.append("可能改变当前优先候选")
    if any(item.high_risk_changed for item in impacts): labels.append("可能改变高风险提示")
    if any(item.explanation_changed for item in impacts): labels.append("可能补足判断说明")
    return tuple(labels)


def _reason(affected: tuple[str, ...]) -> str:
    return "；".join(affected) if affected else "这项信息可补足当前判断依据。"
