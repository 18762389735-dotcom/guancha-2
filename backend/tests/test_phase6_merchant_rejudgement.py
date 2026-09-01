from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from PIL import Image
from psycopg.rows import dict_row

from guancha_api.application.task_runners import ManualTaskRunner
from guancha_api.application.merchant_reply_service import MerchantReplyService
from guancha_api.application import merchant_reply_service as merchant_reply_service_module
from guancha_api.domain.tieguanyin.decision import evaluate_candidate, rank_within_buckets
from guancha_api.domain.tieguanyin.rules.rule_schema import load_approved_rules
from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.main import create_app
from guancha_api.providers.fake import FakeProvider
from guancha_api.providers.merchant_reply import MerchantReplyParse
from guancha_api.repositories.postgres import PostgresPhase2Repository


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def repository() -> PostgresPhase2Repository:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required")
    connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
    migrations = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    async with connection.cursor() as cursor:
        await cursor.execute("drop schema public cascade")
        await cursor.execute("create schema public")
        await cursor.execute("\n".join(path.read_text(encoding="utf-8") for path in sorted(migrations.glob("*.sql"))))
    await connection.commit()
    try:
        yield PostgresPhase2Repository(connection)
    finally:
        await connection.close()


class AnsweringReplyProvider:
    async def parse_merchant_reply(self, *, field_key, raw_text, product_evidence):
        return MerchantReplyParse(
            reply_status="answered", answered_fields=(field_key,),
            claims=({"field_key": field_key, "raw_text": raw_text, "normalized_value": "light"},),
            unresolved_fields=(), conflicts=(), coverage=1, ambiguity=0, should_rejudge=True,
        )


def _image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (640, 480), "orange").save(output, "PNG")
    return output.getvalue()


def _vision() -> FakeProvider:
    return FakeProvider(extraction_response={
        "product_name": "tea", "tea_category": "oolong", "tea_subtype": "tieguanyin", "origin": None,
        "roast_or_style": None, "aroma_claims": [], "taste_claims": [], "season": None, "year_or_batch": None,
        "grade": None, "weight": None, "price": None, "brew_claims": [], "risk_flags": [],
        "evidence": [{"field_name": "tea_type", "raw_text": "tieguanyin", "normalized_value": "tieguanyin",
                      "model_confidence": 1, "information_status": "explicit", "source_type": "product-claim",
                      "verification_status": "unverified", "source_location": "title", "evidence_strength": "high"}],
    })


async def _current_decision(client: httpx.AsyncClient, headers: dict[str, str], runner: ManualTaskRunner) -> tuple[str, str]:
    session = await client.post("/api/v1/selection-sessions", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"need": {"taste_text": "light"}})
    candidate = await client.post(f"/api/v1/selection-sessions/{session.json()['id']}/candidates", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"display_label": "A"})
    upload = await client.post(f"/api/v1/candidates/{candidate.json()['id']}/images", headers={**headers, "Idempotency-Key": str(uuid4())}, files={"file": ("tea.png", _image(), "image/png")})
    assert upload.status_code == 201
    assert await runner.drain() == 1
    decision_job = await client.post(f"/api/v1/selection-sessions/{session.json()['id']}/analyze", headers={**headers, "Idempotency-Key": str(uuid4())})
    assert decision_job.status_code == 201
    assert await runner.drain() == 1
    current = await client.get(f"/api/v1/selection-sessions/{session.json()['id']}/current-decision", headers=headers)
    return session.json()["id"], current.json()["id"]


async def test_merchant_reply_rejudgement_creates_append_only_decision_v2(repository: PostgresPhase2Repository) -> None:
    runner = ManualTaskRunner()
    app = create_app(repository=repository, task_runner=runner, temporary_storage=InMemoryTemporaryPrivateStorage(), provider=_vision(), merchant_reply_provider=AnsweringReplyProvider())
    headers = {"X-Client-Id": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session_id, v1 = await _current_decision(client, headers, runner)
        questions = await client.post(f"/api/v1/decision-versions/{v1}/questions", headers={**headers, "Idempotency-Key": str(uuid4())})
        assert questions.status_code == 201
        question = questions.json()[0]
        reply_key = str(uuid4())
        reply = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": reply_key}, json={"decision_version_id": v1, "followup_question_id": question["id"], "raw_text": "light roast"})
        assert reply.status_code == 201, reply.text
        replay = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": reply_key}, json={"decision_version_id": v1, "followup_question_id": question["id"], "raw_text": "light roast"})
        assert replay.json()["id"] == reply.json()["id"]
        for extra_question in questions.json()[1:]:
            extra = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"decision_version_id": v1, "followup_question_id": extra_question["id"], "raw_text": "light roast"})
            assert extra.status_code == 201
        job = await client.post(f"/api/v1/selection-sessions/{session_id}/rejudge", headers={**headers, "Idempotency-Key": str(uuid4())}, json={})
        assert job.status_code == 201, job.text
        assert await runner.drain() == 1
        completed = await client.get(f"/api/v1/jobs/{job.json()['id']}", headers=headers)
        assert completed.json()["status"] == "completed"
        v2 = completed.json()["decision_version_id"]
        assert v2 and v2 != v1
        assert completed.json()["decision_delta_id"]
        current = await client.get(f"/api/v1/selection-sessions/{session_id}/current-decision", headers=headers)
        assert current.json()["id"] == v2
        async with repository._connection.cursor() as cursor:
            await cursor.execute("select source_type,verification_status from merchant_claims where merchant_reply_id=%s", (reply.json()["id"],))
            assert await cursor.fetchone() == {"source_type": "merchant-claim", "verification_status": "unverified"}
            await cursor.execute("select count(*) as count from decision_deltas where merchant_reply_id=%s", (reply.json()["id"],))
            assert (await cursor.fetchone())["count"] == 1
            await cursor.execute("select id from decision_deltas where merchant_reply_id=%s", (reply.json()["id"],))
            delta_id = (await cursor.fetchone())["id"]
        assert completed.json()["decision_delta_id"] == str(delta_id)
        delta = await client.get(f"/api/v1/decision-deltas/{delta_id}", headers=headers)
        assert delta.status_code == 200
        assert delta.json()["old_decision_version_id"] == v1
        assert delta.json()["new_decision_version_id"] == v2


@pytest.mark.parametrize(("product_status", "product_value", "expected_status"), [
    ("unknown", "heavy", "explicit"),
    ("inferred", "heavy", "explicit"),
    ("explicit", "", "explicit"),
    ("explicit", "light", "explicit"),
    ("explicit", "heavy", "conflict"),
])
async def test_persisted_merchant_conflict_requires_an_explicit_known_opposite_product_claim(
    repository: PostgresPhase2Repository, product_status: str, product_value: str, expected_status: str,
) -> None:
    runner = ManualTaskRunner()
    app = create_app(repository=repository, task_runner=runner, temporary_storage=InMemoryTemporaryPrivateStorage(), provider=_vision(), merchant_reply_provider=AnsweringReplyProvider())
    headers = {"X-Client-Id": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session_id, v1 = await _current_decision(client, headers, runner)
        questions = await client.post(
            f"/api/v1/decision-versions/{v1}/questions",
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert questions.status_code == 201
        question_rows = questions.json()
        roast_question = next(item for item in question_rows if item["field_key"] == "roast_level")
        async with repository._connection.cursor() as cursor:
            await cursor.execute(
                """select cd.extraction_version_id,v.source_image_id,c.id as candidate_id
                   from candidate_decisions cd join extraction_versions v on v.id=cd.extraction_version_id
                   join candidates c on c.id=cd.candidate_id where cd.decision_version_id=%s limit 1""",
                (v1,),
            )
            evidence = await cursor.fetchone()
            await cursor.execute(
                """insert into evidence_items (id,extraction_version_id,field_name,raw_text,normalized_value,model_confidence,
                   information_status,source_type,verification_status,source_image_id,source_location,evidence_strength)
                   values (%s,%s,'roast_level',%s,%s,0.8,%s,'product-claim','unverified',%s,'test','medium')""",
                    (uuid4(), evidence["extraction_version_id"], product_value, product_value, product_status, evidence["source_image_id"]),
                )
        reply = await client.post(
            f"/api/v1/selection-sessions/{session_id}/merchant-replies",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"decision_version_id": v1, "followup_question_id": roast_question["id"], "raw_text": "light roast"},
        )
        assert reply.status_code == 201
        for question in question_rows:
            if question["id"] == roast_question["id"]:
                continue
            reply_text = {
                "price": "280元",
                "aroma_style": "清香",
            }.get(question["field_key"], "不清楚")
            extra = await client.post(
                f"/api/v1/selection-sessions/{session_id}/merchant-replies",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={"decision_version_id": v1, "followup_question_id": question["id"], "raw_text": reply_text},
            )
            assert extra.status_code == 201
        rejudge = await client.post(
            f"/api/v1/selection-sessions/{session_id}/rejudge",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={},
        )
        assert rejudge.status_code == 201, rejudge.text
        assert await runner.drain() == 1
        async with repository._connection.cursor() as cursor:
            await cursor.execute("select information_status,conflicts_with_evidence_id from merchant_claims where merchant_reply_id=%s", (reply.json()["id"],))
            persisted = await cursor.fetchone()
        assert persisted["information_status"] == expected_status
        assert (persisted["conflicts_with_evidence_id"] is not None) is (expected_status == "conflict")


async def test_rejudge_aggregates_all_saved_replies_into_one_delta(repository: PostgresPhase2Repository) -> None:
    class MultiFieldProvider:
        async def parse_merchant_reply(self, *, field_key, raw_text, **_kwargs):
            return MerchantReplyParse(
                reply_status="answered", answered_fields=(field_key,),
                claims=({"field_key": field_key, "raw_text": raw_text, "normalized_value": raw_text},),
                unresolved_fields=(), conflicts=(), coverage=1, ambiguity=0, should_rejudge=True,
            )

    runner = ManualTaskRunner()
    app = create_app(repository=repository, task_runner=runner, temporary_storage=InMemoryTemporaryPrivateStorage(), provider=_vision(), merchant_reply_provider=MultiFieldProvider())
    headers = {"X-Client-Id": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session_id, v1 = await _current_decision(client, headers, runner)
        questions = (await client.post(f"/api/v1/decision-versions/{v1}/questions", headers={**headers, "Idempotency-Key": str(uuid4())})).json()
        assert len(questions) >= 2
        reply_ids = []
        for question in questions:
            response = await client.post(
                f"/api/v1/selection-sessions/{session_id}/merchant-replies",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={"decision_version_id": v1, "followup_question_id": question["id"], "raw_text": f"answer {question['field_key']}"},
            )
            assert response.status_code == 201
            reply_ids.append(response.json()["id"])
        job = await client.post(
            f"/api/v1/selection-sessions/{session_id}/rejudge",
            headers={**headers, "Idempotency-Key": str(uuid4())}, json={},
        )
        assert job.status_code == 201
        assert await runner.drain() == 1
        completed = (await client.get(f"/api/v1/jobs/{job.json()['id']}", headers=headers)).json()
        assert completed["status"] == "completed"
        delta = (await client.get(f"/api/v1/decision-deltas/{completed['decision_delta_id']}", headers=headers)).json()
        assert set(delta["merchant_reply_ids"]) == set(reply_ids)
        assert delta["merchant_reply_id"] in reply_ids


async def test_three_field_merchant_claims_survive_real_submit_and_aggregate_rejudge(
    repository: PostgresPhase2Repository, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the full submit -> parse -> persist -> aggregate path lossless."""

    values = {
        "roast_level": "light",
        "sample_available": "true",
        "season": "spring",
    }

    class ThreeFieldProvider:
        async def parse_merchant_reply(self, *, field_key, raw_text, **_kwargs):
            return MerchantReplyParse(
                reply_status="answered", answered_fields=(field_key,),
                claims=({"field_key": field_key, "raw_text": raw_text, "normalized_value": values[field_key]},),
                unresolved_fields=(), conflicts=(), coverage=1, ambiguity=0, should_rejudge=True,
            )

    captured_evidence: dict[object, list[dict[str, object]]] = {}
    real_evaluate_candidate = merchant_reply_service_module.evaluate_candidate

    def capture_evidence(**kwargs):
        captured_evidence[kwargs["candidate_id"]] = list(kwargs["evidence"])
        return real_evaluate_candidate(**kwargs)

    monkeypatch.setattr(merchant_reply_service_module, "evaluate_candidate", capture_evidence)
    runner = ManualTaskRunner()
    app = create_app(
        repository=repository, task_runner=runner, temporary_storage=InMemoryTemporaryPrivateStorage(),
        provider=_vision(), merchant_reply_provider=ThreeFieldProvider(),
    )
    headers = {"X-Client-Id": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session_id, v1 = await _current_decision(client, headers, runner)
        async with repository._connection.cursor() as cursor:
            await cursor.execute(
                """select candidate_id from candidate_decisions
                   where decision_version_id=%s order by overall_order limit 1""",
                (v1,),
            )
            candidate = await cursor.fetchone()
        assert candidate is not None

        question_ids: dict[str, UUID] = {}
        async with repository._connection.transaction():
            async with repository._connection.cursor() as cursor:
                for index, field_key in enumerate(values):
                    question_id = uuid4()
                    question_ids[field_key] = question_id
                    await cursor.execute(
                        """insert into followup_questions
                           (id,decision_version_id,selection_session_id,candidate_id,field_key,question_text,reason,
                            affected_decision,answer_branches,priority,value_score,value_components,status)
                           values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,'completed')""",
                        (
                            question_id, v1, session_id, candidate["candidate_id"], field_key,
                            f"请确认{field_key}", "deterministic integration fixture", "[]", "[]", 3 - index, 3 - index, "{}",
                        ),
                    )

        raw_texts = {
            "roast_level": "这个火候不会太重，整体就是偏轻一点的。",
            "sample_available": "如果想先试，可以给你寄一小袋尝尝。",
            "season": "这是今年春天采的这一批。",
        }
        reply_ids: list[str] = []
        for field_key, question_id in question_ids.items():
            response = await client.post(
                f"/api/v1/selection-sessions/{session_id}/merchant-replies",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={"decision_version_id": v1, "followup_question_id": str(question_id), "raw_text": raw_texts[field_key]},
            )
            assert response.status_code == 201, response.text
            reply_ids.append(response.json()["id"])

        async with repository._connection.cursor() as cursor:
            await cursor.execute(
                """select count(*) as count from merchant_replies
                   where decision_version_id=%s and id = any(%s::uuid[])""",
                (v1, reply_ids),
            )
            assert (await cursor.fetchone())["count"] == 3

        captured_evidence.clear()
        job = await client.post(
            f"/api/v1/selection-sessions/{session_id}/rejudge",
            headers={**headers, "Idempotency-Key": str(uuid4())}, json={},
        )
        assert job.status_code == 201, job.text
        assert await runner.drain() == 1

        completed = (await client.get(f"/api/v1/jobs/{job.json()['id']}", headers=headers)).json()
        assert completed["status"] == "completed"
        delta = (await client.get(f"/api/v1/decision-deltas/{completed['decision_delta_id']}", headers=headers)).json()
        assert set(delta["added_facts"]) == set(values)

        async with repository._connection.cursor() as cursor:
            await cursor.execute(
                """select id,followup_question_id,candidate_id,processing_status,parse_status
                   from merchant_replies where id = any(%s::uuid[]) order by created_at""",
                (reply_ids,),
            )
            saved_replies = await cursor.fetchall()
            await cursor.execute(
                """select merchant_reply_id,candidate_id,field_key,normalized_value,information_status,evidence_strength
                   from merchant_claims where merchant_reply_id = any(%s::uuid[]) order by created_at""",
                (reply_ids,),
            )
            claims = await cursor.fetchall()

        assert len(saved_replies) == 3
        assert all(row["processing_status"] == "completed" and row["parse_status"] in {"answered", "conflicting"} for row in saved_replies)
        assert len(claims) == 3
        assert {(row["field_key"], row["normalized_value"]) for row in claims} == set(values.items())
        assert all(row["information_status"] == "explicit" and row["evidence_strength"] == "medium" for row in claims)

        assert len(captured_evidence) == 1
        merchant_evidence = [row for row in next(iter(captured_evidence.values())) if row.get("source_type") == "merchant-claim"]
        assert {(row["field_name"], row["normalized_value"]) for row in merchant_evidence} == set(values.items())


async def test_foreign_client_cannot_read_reply_or_rejudge(repository: PostgresPhase2Repository) -> None:
    runner = ManualTaskRunner()
    app = create_app(repository=repository, task_runner=runner, temporary_storage=InMemoryTemporaryPrivateStorage(), provider=_vision(), merchant_reply_provider=AnsweringReplyProvider())
    headers = {"X-Client-Id": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session_id, v1 = await _current_decision(client, headers, runner)
        questions = await client.post(f"/api/v1/decision-versions/{v1}/questions", headers={**headers, "Idempotency-Key": str(uuid4())})
        reply = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"decision_version_id": v1, "followup_question_id": questions.json()[0]["id"], "raw_text": "light roast"})
        for extra_question in questions.json()[1:]:
            extra = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"decision_version_id": v1, "followup_question_id": extra_question["id"], "raw_text": "light roast"})
            assert extra.status_code == 201
        foreign = {"X-Client-Id": str(uuid4()), "Idempotency-Key": str(uuid4())}
        assert (await client.get(f"/api/v1/merchant-replies/{reply.json()['id']}", headers=foreign)).status_code == 403
        assert (await client.post(f"/api/v1/selection-sessions/{session_id}/rejudge", headers=foreign, json={})).status_code == 403


async def test_failed_parser_preserves_the_current_decision_without_partial_rows(repository: PostgresPhase2Repository) -> None:
    class FailingReplyProvider:
        async def parse_merchant_reply(self, **_kwargs):
            raise RuntimeError("synthetic parser failure")

    runner = ManualTaskRunner()
    app = create_app(repository=repository, task_runner=runner, temporary_storage=InMemoryTemporaryPrivateStorage(), provider=_vision(), merchant_reply_provider=FailingReplyProvider())
    headers = {"X-Client-Id": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session_id, v1 = await _current_decision(client, headers, runner)
        questions = await client.post(f"/api/v1/decision-versions/{v1}/questions", headers={**headers, "Idempotency-Key": str(uuid4())})
        reply = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"decision_version_id": v1, "followup_question_id": questions.json()[0]["id"], "raw_text": "light roast"})
        for extra_question in questions.json()[1:]:
            extra = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"decision_version_id": v1, "followup_question_id": extra_question["id"], "raw_text": "light roast"})
            assert extra.status_code == 201
        job = await client.post(f"/api/v1/selection-sessions/{session_id}/rejudge", headers={**headers, "Idempotency-Key": str(uuid4())}, json={})
        with pytest.raises(RuntimeError):
            await runner.drain()
        terminal = await client.get(f"/api/v1/jobs/{job.json()['id']}", headers=headers)
        assert terminal.json()["status"] == "failed"
        current = await client.get(f"/api/v1/selection-sessions/{session_id}/current-decision", headers=headers)
        assert current.json()["id"] == v1
        async with repository._connection.cursor() as cursor:
            await cursor.execute("select count(*) as count from merchant_claims where merchant_reply_id=%s", (reply.json()["id"],))
            assert (await cursor.fetchone())["count"] == 0
            await cursor.execute("select count(*) as count from decision_deltas where merchant_reply_id=%s", (reply.json()["id"],))
            assert (await cursor.fetchone())["count"] == 0


async def test_evasive_reply_still_produces_a_comparative_decision(repository: PostgresPhase2Repository) -> None:
    class EvasiveReplyProvider:
        async def parse_merchant_reply(self, *, field_key, **_kwargs):
            return MerchantReplyParse("evasive", (), (), (field_key,), (), 0, 1, False)

    runner = ManualTaskRunner()
    app = create_app(repository=repository, task_runner=runner, temporary_storage=InMemoryTemporaryPrivateStorage(), provider=_vision(), merchant_reply_provider=EvasiveReplyProvider())
    headers = {"X-Client-Id": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session_id, v1 = await _current_decision(client, headers, runner)
        questions = await client.post(f"/api/v1/decision-versions/{v1}/questions", headers={**headers, "Idempotency-Key": str(uuid4())})
        reply = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"decision_version_id": v1, "followup_question_id": questions.json()[0]["id"], "raw_text": "not sure"})
        for extra_question in questions.json()[1:]:
            extra = await client.post(f"/api/v1/selection-sessions/{session_id}/merchant-replies", headers={**headers, "Idempotency-Key": str(uuid4())}, json={"decision_version_id": v1, "followup_question_id": extra_question["id"], "raw_text": "not sure"})
            assert extra.status_code == 201
        job = await client.post(f"/api/v1/selection-sessions/{session_id}/rejudge", headers={**headers, "Idempotency-Key": str(uuid4())}, json={})
        assert await runner.drain() == 1
        completed = await client.get(f"/api/v1/jobs/{job.json()['id']}", headers=headers)
        assert completed.json()["status"] == "completed"
        assert completed.json()["decision_version_id"] != v1


async def test_unrelated_reply_preserves_v1_bounded_preference_component_and_ranking() -> None:
    preferred_id = uuid4()
    other_id = uuid4()
    version_id = uuid4()
    reply_id = uuid4()
    extraction_ids = {preferred_id: uuid4(), other_id: uuid4()}
    explicit = lambda **values: [
        {"field_name": key, "normalized_value": value, "information_status": "explicit"}
        for key, value in values.items()
    ]
    inputs = [
        {"candidate_id": preferred_id, "extraction_version_id": extraction_ids[preferred_id], "evidence": explicit(tea_type="tieguanyin", aroma_style="nongxiang", roast_level="light", season="spring")},
        {"candidate_id": other_id, "extraction_version_id": extraction_ids[other_id], "evidence": explicit(tea_type="tieguanyin", aroma_style="qingxiang", roast_level="light", season="spring")},
    ]
    parent = {
        "need_snapshot": {},
        "recent_preference_evidence": [{
            "confidence": "low", "issue_source": "tea", "target_type": "aroma",
            "target_value": "nongxiang", "polarity": "positive",
        }],
    }
    replies = [{
        "id": reply_id, "candidate_id": preferred_id, "field_key": "return_policy",
        "processing_status": "completed", "parse_status": "evasive",
    }]
    v1_ranked = rank_within_buckets([
        evaluate_candidate(
            candidate_id=item["candidate_id"], extraction_version_id=item["extraction_version_id"],
            need=parent["need_snapshot"], evidence=item["evidence"], rules=load_approved_rules(),
            recent_preference_evidence=parent["recent_preference_evidence"],
        )
        for item in inputs
    ])
    old_rows = [
        {"candidate_id": draft.candidate_id, "action_bucket": draft.action_bucket.value}
        for draft in v1_ranked
    ]

    class RepositoryStub:
        completed = None

        async def claim_job(self, *, job_id):
            return True

        async def merchant_rejudgement_batch(self, *, anchor_reply_id, client_id):
            return ({"decision_version_id": version_id}, parent, inputs, replies, [])

        async def get_decision_version_for_client(self, *, version_id, client_id):
            return ({"top_candidate_id": v1_ranked[0].candidate_id}, old_rows)

        async def complete_aggregate_merchant_rejudgement(self, **kwargs):
            self.completed = kwargs

        async def fail_aggregate_merchant_rejudgement(self, **kwargs):
            raise AssertionError(f"rejudge unexpectedly failed: {kwargs}")

    class ThrowingSink:
        def emit_server(self, **kwargs):
            raise RuntimeError("telemetry unavailable")

    repository = RepositoryStub()
    await MerchantReplyService(repository=repository, event_sink=ThrowingSink()).run_rejudge(
        job_id=uuid4(), reply_id=reply_id, client_id=uuid4(), fingerprint="bounded-preference",
        analytics_session_id=uuid4(),
    )

    decisions = repository.completed["decisions"]
    assert [row["candidate_id"] for row in decisions] == [draft.candidate_id for draft in v1_ranked]
    assert {
        row["candidate_id"]: row["score_components"]["personal_low_confidence"] for row in decisions
    } == {
        draft.candidate_id: draft.score_components["personal_low_confidence"] for draft in v1_ranked
    }
    assert decisions[0]["candidate_id"] == preferred_id
    assert repository.completed["delta"]["ranking_changed"] is False
