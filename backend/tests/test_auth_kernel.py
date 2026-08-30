from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

from guancha_api.auth.cloudbase import CloudBaseTokenVerifier, cloudbase_gateway_origin
from guancha_api.auth.errors import AuthenticationServiceUnavailable, InvalidAccessToken
from guancha_api.auth.fake import FakeTokenVerifier, UnconfiguredTokenVerifier
from guancha_api.auth.models import AppUser, VerifiedIdentity
from guancha_api.main import create_app
from guancha_api.repositories.postgres import PostgresPhase2Repository


DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class InMemoryAppUserRepository:
    def __init__(self) -> None:
        self.users: dict[str, AppUser] = {}

    async def resolve_or_create_app_user(self, cloudbase_user_id: str) -> AppUser:
        if cloudbase_user_id not in self.users:
            now = datetime.now(timezone.utc)
            self.users[cloudbase_user_id] = AppUser(
                id=uuid4(),
                cloudbase_user_id=cloudbase_user_id,
                created_at=now,
                updated_at=now,
            )
        return self.users[cloudbase_user_id]


async def _request_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def _mock_verifier(
    *,
    status_code: int = 200,
    payload: object | None = None,
    exception: Exception | None = None,
) -> tuple[CloudBaseTokenVerifier, dict[str, str]]:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization", "")
        if exception is not None:
            raise exception
        return httpx.Response(status_code, json=payload, request=request)

    verifier = CloudBaseTokenVerifier(
        env_id="test-env",
        region="ap-shanghai",
        transport=httpx.MockTransport(handler),
    )
    return verifier, seen


@pytest.mark.asyncio
async def test_cloudbase_verifier_returns_verified_subject_and_sends_bearer() -> None:
    verifier, seen = _mock_verifier(payload={"sub": "cloudbase-subject-a", "ignored": "claim"})

    identity = await verifier.verify("incoming-access-token")

    assert identity == VerifiedIdentity(external_subject="cloudbase-subject-a")
    assert seen == {
        "method": "GET",
        "url": "https://test-env.api.tcloudbasegateway.com/auth/v1/token/introspect",
        "authorization": "Bearer incoming-access-token",
    }


@pytest.mark.parametrize("scope", ["anonymous", "anonymous other"])
@pytest.mark.asyncio
async def test_cloudbase_verifier_rejects_explicit_anonymous_scope(scope: str) -> None:
    verifier, _ = _mock_verifier(payload={"sub": "cloudbase-subject-a", "scope": scope})

    with pytest.raises(InvalidAccessToken):
        await verifier.verify("incoming-access-token")


@pytest.mark.asyncio
async def test_cloudbase_verifier_allows_non_anonymous_scope() -> None:
    verifier, _ = _mock_verifier(payload={"sub": "cloudbase-subject-a", "scope": "user sso"})

    assert await verifier.verify("incoming-access-token") == VerifiedIdentity(
        external_subject="cloudbase-subject-a"
    )


@pytest.mark.parametrize("scope", [None, [], {}, ""])
@pytest.mark.asyncio
async def test_cloudbase_verifier_fails_closed_for_malformed_scope(scope: object) -> None:
    verifier, _ = _mock_verifier(payload={"sub": "cloudbase-subject-a", "scope": scope})

    with pytest.raises(InvalidAccessToken):
        await verifier.verify("incoming-access-token")


@pytest.mark.parametrize(
    "payload",
    [{}, {"sub": None}, {"sub": ""}, {"sub": "   "}, {"sub": 123}, []],
)
@pytest.mark.asyncio
async def test_cloudbase_verifier_fails_closed_for_missing_or_unusable_subject(payload: object) -> None:
    verifier, _ = _mock_verifier(payload=payload)

    with pytest.raises(InvalidAccessToken):
        await verifier.verify("incoming-access-token")


@pytest.mark.asyncio
async def test_cloudbase_verifier_treats_empty_object_as_invalid() -> None:
    verifier, _ = _mock_verifier(payload={})

    with pytest.raises(InvalidAccessToken):
        await verifier.verify("incoming-access-token")


@pytest.mark.asyncio
async def test_cloudbase_verifier_treats_http_5xx_as_unavailable() -> None:
    verifier, _ = _mock_verifier(status_code=503, payload={"error": "do not expose"})

    with pytest.raises(AuthenticationServiceUnavailable):
        await verifier.verify("incoming-access-token")


@pytest.mark.parametrize("exception_type", [httpx.ReadTimeout, httpx.ConnectError])
@pytest.mark.asyncio
async def test_cloudbase_verifier_treats_timeout_and_network_errors_as_unavailable(exception_type) -> None:
    verifier, _ = _mock_verifier(exception=exception_type("transport failure"))

    with pytest.raises(AuthenticationServiceUnavailable):
        await verifier.verify("incoming-access-token")


@pytest.mark.parametrize(
    ("region", "expected_origin"),
    [
        ("ap-shanghai", "https://test-env.api.tcloudbasegateway.com"),
        ("ap-guangzhou", "https://test-env.api.tcloudbasegateway.com"),
        ("ap-singapore", "https://test-env.api.intl.tcloudbasegateway.com"),
    ],
)
def test_cloudbase_gateway_origin_is_region_aware(region: str, expected_origin: str) -> None:
    assert cloudbase_gateway_origin(env_id="test-env", region=region) == expected_origin


def test_cloudbase_gateway_origin_rejects_unknown_region() -> None:
    with pytest.raises(ValueError):
        cloudbase_gateway_origin(env_id="test-env", region="ap-unknown")


@pytest.mark.asyncio
async def test_fake_verifier_is_closed_allowlist() -> None:
    verifier = FakeTokenVerifier()

    assert await verifier.verify("valid-token-a") == VerifiedIdentity(external_subject="cloudbase-user-a")
    with pytest.raises(InvalidAccessToken):
        await verifier.verify("arbitrary-token")


@pytest.mark.asyncio
async def test_me_requires_authorization() -> None:
    app = create_app(repository=InMemoryAppUserRepository(), token_verifier=FakeTokenVerifier())
    async with await _request_client(app) as client:
        response = await client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    "authorization",
    ["Basic credentials", "Bearer", "Bearer one two", "Token access-token"],
)
@pytest.mark.asyncio
async def test_me_rejects_malformed_authorization(authorization: str) -> None:
    app = create_app(repository=InMemoryAppUserRepository(), token_verifier=FakeTokenVerifier())
    async with await _request_client(app) as client:
        response = await client.get("/api/v1/me", headers={"Authorization": authorization})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_token"


@pytest.mark.asyncio
async def test_me_rejects_invalid_fake_token() -> None:
    app = create_app(repository=InMemoryAppUserRepository(), token_verifier=FakeTokenVerifier())
    async with await _request_client(app) as client:
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": "Bearer invalid-token"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_token"


@pytest.mark.asyncio
async def test_me_surfaces_auth_service_unavailable_without_fallback() -> None:
    verifier = FakeTokenVerifier(unavailable_tokens=frozenset({"unavailable-token"}))
    app = create_app(repository=InMemoryAppUserRepository(), token_verifier=verifier)
    async with await _request_client(app) as client:
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": "Bearer unavailable-token"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "authentication_service_unavailable"


@pytest.mark.asyncio
async def test_me_reports_auth_not_configured_without_breaking_app_creation() -> None:
    app = create_app(repository=InMemoryAppUserRepository(), token_verifier=UnconfiguredTokenVerifier())
    async with await _request_client(app) as client:
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": "Bearer any-token"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_not_configured"


@pytest.mark.asyncio
async def test_me_resolves_stable_app_user_and_ignores_client_user_id() -> None:
    repository = InMemoryAppUserRepository()
    app = create_app(repository=repository, token_verifier=FakeTokenVerifier())
    async with await _request_client(app) as client:
        first = await client.get("/api/v1/me", headers={"Authorization": "Bearer valid-token-a"})
        second = await client.get(
            "/api/v1/me",
            headers={
                "Authorization": "Bearer valid-token-a",
                "X-User-Id": str(uuid4()),
            },
        )
        other = await client.get("/api/v1/me", headers={"Authorization": "Bearer valid-token-b"})

    first_body = first.json()
    second_body = second.json()
    other_body = other.json()
    assert first.status_code == second.status_code == other.status_code == 200
    assert first_body["authenticated"] is True
    assert set(first_body) == {"id", "authenticated", "created_at"}
    assert UUID(first_body["id"])
    assert first_body["id"] == second_body["id"]
    assert first_body["id"] != other_body["id"]
    assert "cloudbase_user_id" not in first_body
    assert "access_token" not in first_body
    assert "refresh_token" not in first_body


@pytest_asyncio.fixture
async def postgres_repository() -> PostgresPhase2Repository:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for auth PostgreSQL integration tests")
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
    repository = PostgresPhase2Repository(connection)
    try:
        yield repository
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_app_user_repository_mapping_is_stable(postgres_repository: PostgresPhase2Repository) -> None:
    first = await postgres_repository.resolve_or_create_app_user("cloudbase-subject-a")
    second = await postgres_repository.resolve_or_create_app_user("cloudbase-subject-a")
    other = await postgres_repository.resolve_or_create_app_user("cloudbase-subject-b")

    assert first.id == second.id
    assert first.cloudbase_user_id == second.cloudbase_user_id == "cloudbase-subject-a"
    assert first.id != other.id


@pytest.mark.asyncio
async def test_app_user_resolution_does_not_update_existing_row(
    postgres_repository: PostgresPhase2Repository,
) -> None:
    first = await postgres_repository.resolve_or_create_app_user("cloudbase-subject-a")
    expected_updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    async with postgres_repository._connection.cursor() as cursor:
        await cursor.execute(
            "update app_users set updated_at = %s where id = %s",
            (expected_updated_at, first.id),
        )
        await cursor.execute(
            "select updated_at from app_users where id = %s",
            (first.id,),
        )
        before_resolve = await cursor.fetchone()

    resolved = await postgres_repository.resolve_or_create_app_user("cloudbase-subject-a")

    async with postgres_repository._connection.cursor() as cursor:
        await cursor.execute(
            "select updated_at from app_users where id = %s",
            (first.id,),
        )
        after_resolve = await cursor.fetchone()

    assert before_resolve["updated_at"] == expected_updated_at
    assert after_resolve["updated_at"] == expected_updated_at
    assert resolved.id == first.id


@pytest.mark.asyncio
async def test_concurrent_app_user_first_use_keeps_one_identity(
    postgres_repository: PostgresPhase2Repository,
) -> None:
    assert DATABASE_URL is not None
    first_repository = await PostgresPhase2Repository.connect(DATABASE_URL)
    second_repository = await PostgresPhase2Repository.connect(DATABASE_URL)
    try:
        first, second = await asyncio.gather(
            first_repository.resolve_or_create_app_user("concurrent-subject"),
            second_repository.resolve_or_create_app_user("concurrent-subject"),
        )
    finally:
        await first_repository.close()
        await second_repository.close()

    async with postgres_repository._connection.cursor() as cursor:
        await cursor.execute(
            "select count(*) as count from app_users where cloudbase_user_id = %s",
            ("concurrent-subject",),
        )
        count_row = await cursor.fetchone()

    assert first.id == second.id
    assert count_row["count"] == 1


@pytest.mark.asyncio
async def test_existing_anonymous_selection_route_needs_no_bearer(
    postgres_repository: PostgresPhase2Repository,
) -> None:
    app = create_app(repository=postgres_repository, token_verifier=FakeTokenVerifier())
    client_id = uuid4()
    async with await _request_client(app) as client:
        response = await client.post(
            "/api/v1/selection-sessions",
            json={"need": {"taste_text": "floral"}},
            headers={
                "X-Client-Id": str(client_id),
                "Idempotency-Key": str(uuid4()),
            },
        )

    assert response.status_code == 201
    assert response.json()["anonymous_client_id"] == str(client_id)
