"""Phase 9-2 owner precedence and authenticated Selection isolation tests."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image
from psycopg.rows import dict_row

from guancha_api.auth.dependencies import Owner
from guancha_api.auth.fake import FakeTokenVerifier
from guancha_api.auth.models import AppUser, OwnerContext
from guancha_api.application.task_runners import ManualTaskRunner
from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.main import create_app
from guancha_api.providers.fake import FakeProvider
from guancha_api.repositories.postgres import PostgresPhase2Repository


DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class InMemoryAppUserRepository:
    def __init__(self) -> None:
        self.users: dict[str, AppUser] = {}

    async def resolve_or_create_app_user(self, cloudbase_user_id: str) -> AppUser:
        if cloudbase_user_id not in self.users:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            self.users[cloudbase_user_id] = AppUser(
                id=uuid4(),
                cloudbase_user_id=cloudbase_user_id,
                created_at=now,
                updated_at=now,
            )
        return self.users[cloudbase_user_id]


def _owner_probe_app(verifier: FakeTokenVerifier) -> FastAPI:
    app = FastAPI()
    app.state.token_verifier = verifier
    app.state.repository = InMemoryAppUserRepository()

    @app.get("/owner")
    async def owner_probe(owner: Owner) -> dict[str, object]:
        return {
            "authenticated": owner.is_authenticated,
            "user_id": str(owner.user_id) if owner.user_id else None,
            "anonymous_client_id": str(owner.anonymous_client_id) if owner.anonymous_client_id else None,
        }

    return app


@pytest.mark.asyncio
async def test_owner_context_precedence_and_no_anonymous_fallback() -> None:
    client_id = str(uuid4())
    app = _owner_probe_app(FakeTokenVerifier())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        anonymous = await client.get("/owner", headers={"X-Client-Id": client_id})
        authenticated = await client.get("/owner", headers={"Authorization": "Bearer valid-token-a"})
        auth_wins = await client.get(
            "/owner",
            headers={"Authorization": "Bearer valid-token-a", "X-Client-Id": str(uuid4())},
        )
        malformed = await client.get(
            "/owner",
            headers={"Authorization": "Basic credentials", "X-Client-Id": client_id},
        )
        invalid = await client.get(
            "/owner",
            headers={"Authorization": "Bearer invalid-token", "X-Client-Id": client_id},
        )
        missing = await client.get("/owner")

    assert anonymous.status_code == 200
    assert anonymous.json() == {
        "authenticated": False,
        "user_id": None,
        "anonymous_client_id": client_id,
    }
    assert authenticated.status_code == auth_wins.status_code == 200
    assert authenticated.json()["authenticated"] is True
    assert authenticated.json()["user_id"] == auth_wins.json()["user_id"]
    assert auth_wins.json()["anonymous_client_id"] is None
    assert malformed.status_code == invalid.status_code == 401
    assert malformed.json()["detail"] == invalid.json()["detail"] == "invalid_access_token"
    assert missing.status_code == 422
    assert missing.json()["detail"] == "missing_client_id"


@pytest.mark.asyncio
async def test_auth_unavailable_does_not_fallback_to_anonymous() -> None:
    app = _owner_probe_app(FakeTokenVerifier(unavailable_tokens=frozenset({"unavailable-token"})))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/owner",
            headers={
                "Authorization": "Bearer unavailable-token",
                "X-Client-Id": str(uuid4()),
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "authentication_service_unavailable"


def test_owner_context_has_exactly_one_server_derived_branch() -> None:
    with pytest.raises(ValueError):
        OwnerContext()
    with pytest.raises(ValueError):
        OwnerContext(user_id=uuid4(), anonymous_client_id=uuid4())


@pytest_asyncio.fixture
async def repository() -> PostgresPhase2Repository:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for Phase 9-2 PostgreSQL ownership tests")
    connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
    migration_directory = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    migration = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(migration_directory.glob("*.sql"))
    )
    async with connection.cursor() as cursor:
        await cursor.execute("drop schema public cascade")
        await cursor.execute("create schema public")
        await cursor.execute(migration)
    await connection.commit()
    await connection.set_autocommit(True)
    try:
        yield PostgresPhase2Repository(connection)
    finally:
        await connection.close()


def _image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (640, 480), "orange").save(output, "PNG")
    return output.getvalue()


def _provider() -> FakeProvider:
    return FakeProvider(
        extraction_response={
            "product_name": "tea",
            "tea_category": "oolong",
            "tea_subtype": "tieguanyin",
            "origin": None,
            "roast_or_style": None,
            "aroma_claims": [],
            "taste_claims": [],
            "season": None,
            "year_or_batch": None,
            "grade": None,
            "weight": None,
            "price": None,
            "brew_claims": [],
            "risk_flags": [],
            "evidence": [
                {
                    "field_name": "tea_type",
                    "raw_text": "铁观音",
                    "normalized_value": "tieguanyin",
                    "model_confidence": 1,
                    "information_status": "explicit",
                    "source_type": "product-claim",
                    "verification_status": "unverified",
                    "source_location": "title",
                    "evidence_strength": "high",
                }
            ],
        }
    )


def _assert_error(response: httpx.Response, code: str) -> None:
    body = response.json()["error"]
    assert body["code"] == code
    assert body["retryable"] is False
    assert UUID(body["request_id"])


async def _complete_authenticated_selection(
    client: httpx.AsyncClient, headers: dict[str, str], runner: ManualTaskRunner
) -> dict[str, str]:
    session = await client.post(
        "/api/v1/selection-sessions",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"need": {"taste_text": "清香"}},
    )
    assert session.status_code == 201, session.text
    assert session.json()["anonymous_client_id"] is None
    session_id = session.json()["id"]

    candidate = await client.post(
        f"/api/v1/selection-sessions/{session_id}/candidates",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"display_label": "A"},
    )
    assert candidate.status_code == 201, candidate.text
    candidate_id = candidate.json()["id"]
    upload = await client.post(
        f"/api/v1/candidates/{candidate_id}/images",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("tea.png", _image(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    assert await runner.drain() == 1
    image_id = upload.json()["image"]["id"]
    extraction_job_id = upload.json()["extraction_job"]["id"]
    extraction_job = await client.get(f"/api/v1/jobs/{extraction_job_id}", headers=headers)
    assert extraction_job.status_code == 200, extraction_job.text
    extraction_id = extraction_job.json()["extraction_version_id"]

    analysis = await client.post(
        f"/api/v1/selection-sessions/{session_id}/analyze",
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert analysis.status_code == 201, analysis.text
    assert await runner.drain() == 1
    decision = await client.get(
        f"/api/v1/selection-sessions/{session_id}/current-decision", headers=headers
    )
    assert decision.status_code == 200, decision.text
    decision_id = decision.json()["id"]

    questions = await client.post(
        f"/api/v1/decision-versions/{decision_id}/questions",
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert questions.status_code == 201, questions.text
    assert questions.json()
    question_rows = questions.json()
    question = question_rows[0]
    reply = None
    for question_row in question_rows:
        saved_reply = await client.post(
            f"/api/v1/selection-sessions/{session_id}/merchant-replies",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "decision_version_id": decision_id,
                "followup_question_id": question_row["id"],
                "raw_text": "轻火",
            },
        )
        assert saved_reply.status_code == 201, saved_reply.text
        if reply is None:
            reply = saved_reply
    assert reply is not None
    rejudge = await client.post(
        f"/api/v1/selection-sessions/{session_id}/rejudge",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert rejudge.status_code == 201, rejudge.text
    assert await runner.drain() == 1
    completed_job = await client.get(f"/api/v1/jobs/{rejudge.json()['id']}", headers=headers)
    assert completed_job.status_code == 200, completed_job.text
    assert completed_job.json()["decision_delta_id"]

    return {
        "session_id": session_id,
        "candidate_id": candidate_id,
        "image_id": image_id,
        "extraction_job_id": extraction_job_id,
        "extraction_id": extraction_id,
        "decision_id": decision_id,
        "question_id": question["id"],
        "reply_id": reply.json()["id"],
        "delta_id": completed_job.json()["decision_delta_id"],
        "analysis_job_id": analysis.json()["id"],
    }


@pytest.mark.asyncio
async def test_authenticated_selection_and_all_derived_reads_are_session_owned(
    repository: PostgresPhase2Repository,
) -> None:
    runner = ManualTaskRunner()
    app = create_app(
        repository=repository,
        token_verifier=FakeTokenVerifier(),
        task_runner=runner,
        temporary_storage=InMemoryTemporaryPrivateStorage(),
        provider=_provider(),
    )
    user_a = {"Authorization": "Bearer valid-token-a"}
    user_b = {"Authorization": "Bearer valid-token-b"}
    anonymous_x = {"X-Client-Id": str(uuid4())}
    anonymous_y = {"X-Client-Id": str(uuid4())}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ids = await _complete_authenticated_selection(client, user_a, runner)

        # The same account is cross-device: an arbitrary client header cannot
        # change the server-derived authenticated owner.
        same_user = await client.get(
            f"/api/v1/selection-sessions/{ids['session_id']}",
            headers={**user_a, "X-Client-Id": str(uuid4())},
        )
        assert same_user.status_code == 200

        anonymous_session = await client.post(
            "/api/v1/selection-sessions",
            headers={**anonymous_x, "Idempotency-Key": str(uuid4())},
            json={"need": {}},
        )
        assert anonymous_session.status_code == 201
        anonymous_session_id = anonymous_session.json()["id"]
        assert (await client.get(
            f"/api/v1/selection-sessions/{anonymous_session_id}", headers=anonymous_x
        )).status_code == 200
        assert (await client.get(
            f"/api/v1/selection-sessions/{anonymous_session_id}", headers=anonymous_y
        )).status_code == 403

        root_reads = [
            f"/api/v1/selection-sessions/{ids['session_id']}",
            f"/api/v1/selection-sessions/{ids['session_id']}/candidates",
            f"/api/v1/selection-sessions/{ids['session_id']}/snapshot",
            f"/api/v1/selection-sessions/{ids['session_id']}/answer",
        ]
        derived_reads = [
            f"/api/v1/candidate-images/{ids['image_id']}",
            f"/api/v1/jobs/{ids['extraction_job_id']}",
            f"/api/v1/jobs/{ids['analysis_job_id']}",
            f"/api/v1/extraction-versions/{ids['extraction_id']}",
            f"/api/v1/candidates/{ids['candidate_id']}/current-extraction",
            f"/api/v1/decision-versions/{ids['decision_id']}",
            f"/api/v1/decision-versions/{ids['decision_id']}/questions",
            f"/api/v1/merchant-replies/{ids['reply_id']}",
            f"/api/v1/decision-deltas/{ids['delta_id']}",
        ]
        for path in [*root_reads, *derived_reads]:
            foreign = await client.get(path, headers=user_b)
            assert foreign.status_code == 403, (path, foreign.text)
            assert foreign.json()["error"]["code"] == "resource_not_owned"

        # A missing bearer never falls back to the historical or arbitrary
        # X-Client-Id, even when it happens to be the browser's old value.
        for path in [*root_reads, *derived_reads]:
            anonymous_attempt = await client.get(path, headers=anonymous_x)
            assert anonymous_attempt.status_code == 403, (path, anonymous_attempt.text)
            assert anonymous_attempt.json()["error"]["code"] == "resource_not_owned"

        denied_patch = await client.patch(
            f"/api/v1/selection-sessions/{ids['session_id']}",
            headers=user_b,
            json={"need": {"taste_text": "浓香"}},
        )
        denied_candidate = await client.post(
            f"/api/v1/selection-sessions/{ids['session_id']}/candidates",
            headers={**user_b, "Idempotency-Key": str(uuid4())},
            json={"display_label": "B"},
        )
        denied_delete = await client.delete(
            f"/api/v1/candidates/{ids['candidate_id']}", headers=user_b
        )
        denied_image_delete = await client.delete(
            f"/api/v1/candidate-images/{ids['image_id']}", headers=user_b
        )
        denied_questions = await client.post(
            f"/api/v1/decision-versions/{ids['decision_id']}/questions",
            headers={**user_b, "Idempotency-Key": str(uuid4())},
        )
        denied_reply = await client.post(
            f"/api/v1/selection-sessions/{ids['session_id']}/merchant-replies",
            headers={**user_b, "Idempotency-Key": str(uuid4())},
            json={
                "decision_version_id": ids["decision_id"],
                "followup_question_id": ids["question_id"],
                "raw_text": "轻火",
            },
        )
        denied_rejudge = await client.post(
            f"/api/v1/selection-sessions/{ids['session_id']}/rejudge",
            headers={**user_b, "Idempotency-Key": str(uuid4())},
            json={},
        )
        for response in (
            denied_patch,
            denied_candidate,
            denied_delete,
            denied_image_delete,
            denied_questions,
            denied_reply,
            denied_rejudge,
        ):
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "resource_not_owned"

        claimed = await client.get(
            f"/api/v1/selection-sessions/{anonymous_session_id}",
            headers={**user_a, "X-Client-Id": anonymous_x["X-Client-Id"]},
        )
        assert claimed.status_code == 403
        assert claimed.json()["error"]["code"] == "resource_not_owned"


@pytest.mark.asyncio
async def test_authenticated_owner_cannot_upload_another_users_candidate_image(
    repository: PostgresPhase2Repository,
) -> None:
    storage = InMemoryTemporaryPrivateStorage()
    app = create_app(
        repository=repository,
        token_verifier=FakeTokenVerifier(),
        task_runner=ManualTaskRunner(),
        temporary_storage=storage,
        provider=_provider(),
    )
    user_a = {"Authorization": "Bearer valid-token-a"}
    user_b = {"Authorization": "Bearer valid-token-b"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session = await client.post(
            "/api/v1/selection-sessions",
            headers={**user_a, "Idempotency-Key": str(uuid4())},
            json={"need": {}},
        )
        assert session.status_code == 201, session.text
        candidate = await client.post(
            f"/api/v1/selection-sessions/{session.json()['id']}/candidates",
            headers={**user_a, "Idempotency-Key": str(uuid4())},
            json={"display_label": "A"},
        )
        assert candidate.status_code == 201, candidate.text
        candidate_id = candidate.json()["id"]

        denied = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={**user_b, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.png", _image(), "image/png")},
        )

    assert denied.status_code == 403, denied.text
    _assert_error(denied, "resource_not_owned")
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select count(*) as count from candidate_images where candidate_id=%s", (candidate_id,))
        images = await cursor.fetchone()
        await cursor.execute("select count(*) as count from analysis_jobs where candidate_id=%s", (candidate_id,))
        jobs = await cursor.fetchone()
    assert images == {"count": 0}
    assert jobs == {"count": 0}
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_authenticated_owner_cannot_analyze_another_users_selection(
    repository: PostgresPhase2Repository,
) -> None:
    runner = ManualTaskRunner()
    app = create_app(
        repository=repository,
        token_verifier=FakeTokenVerifier(),
        task_runner=runner,
        temporary_storage=InMemoryTemporaryPrivateStorage(),
        provider=_provider(),
    )
    user_a = {"Authorization": "Bearer valid-token-a"}
    user_b = {"Authorization": "Bearer valid-token-b"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ids = await _complete_authenticated_selection(client, user_a, runner)
        async with repository._connection.cursor() as cursor:
            await cursor.execute(
                """select count(*) as count from analysis_jobs j
                   join candidates c on c.id = j.candidate_id
                   where c.selection_session_id=%s""",
                (ids["session_id"],),
            )
            jobs_before = await cursor.fetchone()
            await cursor.execute(
                "select count(*) as count from decision_versions where selection_session_id=%s",
                (ids["session_id"],),
            )
            decisions_before = await cursor.fetchone()

        denied = await client.post(
            f"/api/v1/selection-sessions/{ids['session_id']}/analyze",
            headers={**user_b, "Idempotency-Key": str(uuid4())},
        )

    assert denied.status_code == 403, denied.text
    _assert_error(denied, "resource_not_owned")
    async with repository._connection.cursor() as cursor:
        await cursor.execute(
            """select count(*) as count from analysis_jobs j
               join candidates c on c.id = j.candidate_id
               where c.selection_session_id=%s""",
            (ids["session_id"],),
        )
        jobs_after = await cursor.fetchone()
        await cursor.execute(
            "select count(*) as count from decision_versions where selection_session_id=%s",
            (ids["session_id"],),
        )
        decisions_after = await cursor.fetchone()
    assert jobs_after == jobs_before
    assert decisions_after == decisions_before


@pytest.mark.asyncio
async def test_authenticated_owner_cannot_retry_another_users_extraction(
    repository: PostgresPhase2Repository,
) -> None:
    storage = InMemoryTemporaryPrivateStorage()
    runner = ManualTaskRunner()
    app = create_app(
        repository=repository,
        token_verifier=FakeTokenVerifier(),
        task_runner=runner,
        temporary_storage=storage,
        provider=_provider(),
    )
    user_a = {"Authorization": "Bearer valid-token-a"}
    user_b = {"Authorization": "Bearer valid-token-b"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session = await client.post(
            "/api/v1/selection-sessions",
            headers={**user_a, "Idempotency-Key": str(uuid4())},
            json={"need": {}},
        )
        assert session.status_code == 201, session.text
        candidate = await client.post(
            f"/api/v1/selection-sessions/{session.json()['id']}/candidates",
            headers={**user_a, "Idempotency-Key": str(uuid4())},
            json={"display_label": "A"},
        )
        assert candidate.status_code == 201, candidate.text
        candidate_id = candidate.json()["id"]
        upload = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={**user_a, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.png", _image(), "image/png")},
        )
        assert upload.status_code == 201, upload.text
        original_job_id = upload.json()["extraction_job"]["id"]
        assert runner.pending_count == 1

        denied = await client.post(
            f"/api/v1/candidates/{candidate_id}/extraction-jobs",
            headers={**user_b, "Idempotency-Key": str(uuid4())},
        )

    assert denied.status_code == 403, denied.text
    _assert_error(denied, "resource_not_owned")
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select id, status from analysis_jobs where candidate_id=%s order by created_at", (candidate_id,))
        jobs = await cursor.fetchall()
    assert jobs == [{"id": UUID(original_job_id), "status": "queued"}]
    assert runner.pending_count == 1
    assert len(storage.objects) == 1


@pytest.mark.asyncio
async def test_invalid_bearer_does_not_fallback_to_the_real_anonymous_selection_owner(
    repository: PostgresPhase2Repository,
) -> None:
    app = create_app(repository=repository, token_verifier=FakeTokenVerifier())
    anonymous_client_id = str(uuid4())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session = await client.post(
            "/api/v1/selection-sessions",
            headers={"X-Client-Id": anonymous_client_id, "Idempotency-Key": str(uuid4())},
            json={"need": {}},
        )
        assert session.status_code == 201, session.text

        denied = await client.get(
            f"/api/v1/selection-sessions/{session.json()['id']}",
            headers={
                "Authorization": "Bearer invalid-token",
                "X-Client-Id": anonymous_client_id,
            },
        )

    assert denied.status_code == 401, denied.text
    _assert_error(denied, "invalid_access_token")


@pytest.mark.asyncio
async def test_authenticated_and_anonymous_session_idempotency_scopes_are_independent(
    repository: PostgresPhase2Repository,
) -> None:
    app = create_app(repository=repository, token_verifier=FakeTokenVerifier())
    user_a = {"Authorization": "Bearer valid-token-a"}
    user_b = {"Authorization": "Bearer valid-token-b"}
    anonymous_x = {"X-Client-Id": str(uuid4())}
    key = str(uuid4())
    payload = {"need": {"taste_text": "清香"}}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/selection-sessions", headers={**user_a, "Idempotency-Key": key}, json=payload)
        replay = await client.post("/api/v1/selection-sessions", headers={**user_a, "Idempotency-Key": key}, json=payload)
        conflict = await client.post("/api/v1/selection-sessions", headers={**user_a, "Idempotency-Key": key}, json={"need": {"taste_text": "浓香"}})
        other_user = await client.post("/api/v1/selection-sessions", headers={**user_b, "Idempotency-Key": key}, json=payload)
        anonymous = await client.post("/api/v1/selection-sessions", headers={**anonymous_x, "Idempotency-Key": key}, json=payload)

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert other_user.status_code == anonymous.status_code == 201
    assert other_user.json()["id"] not in {first.json()["id"], anonymous.json()["id"]}
