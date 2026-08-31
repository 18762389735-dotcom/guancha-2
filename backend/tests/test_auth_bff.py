"""Offline contract tests for the same-origin CloudBase Auth BFF."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from guancha_api.auth.gateway import CloudBaseAuthError, CloudBaseAuthGateway
from guancha_api.main import create_app


def token_payload(access_token: str = "access-token-a", refresh_token: str = "refresh-token-a") -> dict[str, Any]:
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": 3600,
        "sub": "cloudbase-user-a",
    }


class FakeAuthGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.refresh_calls = 0
        self.failure: CloudBaseAuthError | None = None

    async def send_verification(self, email: str) -> dict[str, Any]:
        self.calls.append(("verification", {"email": email}))
        if self.failure:
            raise self.failure
        return {"verification_id": "verification-id-a", "expires_in": 600}

    async def verify_verification(self, verification_id: str, verification_code: str) -> dict[str, Any]:
        self.calls.append(("verify", {"verification_id": verification_id, "verification_code": verification_code}))
        if self.failure:
            raise self.failure
        return {"verification_token": "verification-token-a", "expires_in": 600}

    async def sign_up(self, *, email: str, verification_token: str, password: str) -> dict[str, Any]:
        self.calls.append(("signup", {"email": email, "verification_token": verification_token, "password": password}))
        if self.failure:
            raise self.failure
        return token_payload("registered-access-token", "registered-refresh-token")

    async def sign_in(self, *, username: str, password: str) -> dict[str, Any]:
        self.calls.append(("signin", {"username": username, "password": password}))
        if self.failure:
            raise self.failure
        return token_payload()

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        self.calls.append(("refresh", {"refresh_token": refresh_token}))
        self.refresh_calls += 1
        if self.failure:
            raise self.failure
        return token_payload("refreshed-access-token", "rotated-refresh-token")

    async def sign_out(self, access_token: str) -> None:
        self.calls.append(("signout", {"access_token": access_token}))
        if self.failure:
            if self.failure.code == "session_expired":
                return
            raise self.failure


def make_app(gateway: FakeAuthGateway):
    return create_app(auth_gateway=gateway)


async def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_register_start_and_complete_forward_verification_then_signup_without_exposing_refresh_token() -> None:
    gateway = FakeAuthGateway()
    app = make_app(gateway)
    async with await client_for(app) as client:
        start = await client.post("/api/v1/auth/register/start", json={"email": "new@example.com"})
        complete = await client.post(
            "/api/v1/auth/register/complete",
            json={
                "email": "new@example.com",
                "verification_id": "verification-id-a",
                "verification_code": "123456",
                "password": "Password1",
            },
        )
    assert start.status_code == 200
    assert start.json() == {"verification_id": "verification-id-a", "expires_in": 600}
    assert complete.status_code == 200
    assert complete.json()["access_token"] == "registered-access-token"
    assert "refresh_token" not in complete.json()
    assert [name for name, _ in gateway.calls] == ["verification", "verify", "signup"]
    assert gateway.calls[1][1]["verification_code"] == "123456"
    assert gateway.calls[2][1]["verification_token"] == "verification-token-a"


@pytest.mark.asyncio
async def test_sign_in_sets_httponly_refresh_cookie_and_returns_only_access_session_metadata() -> None:
    gateway = FakeAuthGateway()
    async with await client_for(make_app(gateway)) as client:
        response = await client.post(
            "/api/v1/auth/sign-in",
            json={"username": "tea@example.com", "password": "Password1"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "access_token": "access-token-a",
        "expires_in": 3600,
        "sub": "cloudbase-user-a",
        "token_type": "Bearer",
    }
    cookie = response.headers["set-cookie"]
    assert "guancha.refresh-token.v1=refresh-token-a" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "refresh_token" not in response.text


@pytest.mark.asyncio
async def test_refresh_reads_cookie_rotates_it_and_never_returns_refresh_token() -> None:
    gateway = FakeAuthGateway()
    async with await client_for(make_app(gateway)) as client:
        signed_in = await client.post(
            "/api/v1/auth/sign-in",
            json={"username": "tea@example.com", "password": "Password1"},
        )
        refreshed = await client.post("/api/v1/auth/refresh")
    assert signed_in.status_code == 200
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] == "refreshed-access-token"
    assert "refresh_token" not in refreshed.text
    assert "rotated-refresh-token" in refreshed.headers["set-cookie"]
    assert gateway.calls[-1] == ("refresh", {"refresh_token": "refresh-token-a"})


@pytest.mark.asyncio
async def test_invalid_refresh_clears_cookie_and_returns_session_expired() -> None:
    gateway = FakeAuthGateway()
    gateway.failure = CloudBaseAuthError("session_expired", status_code=401)
    async with await client_for(make_app(gateway)) as client:
        response = await client.post(
            "/api/v1/auth/refresh",
            cookies={"guancha.refresh-token.v1": "expired-refresh-token"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "expired-refresh-token" not in response.text


@pytest.mark.asyncio
async def test_logout_calls_upstream_with_bearer_and_clears_cookie_even_if_upstream_session_is_expired() -> None:
    gateway = FakeAuthGateway()
    async with await client_for(make_app(gateway)) as client:
        signed_in = await client.post(
            "/api/v1/auth/sign-in",
            json={"username": "tea@example.com", "password": "Password1"},
        )
        response = await client.post(
            "/api/v1/auth/sign-out",
            headers={"Authorization": f"Bearer {signed_in.json()['access_token']}"},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "signed_out"}
    assert gateway.calls[-1] == ("signout", {"access_token": "access-token-a"})
    assert "Max-Age=0" in response.headers["set-cookie"]

    gateway.failure = CloudBaseAuthError("session_expired", status_code=401)
    async with await client_for(make_app(gateway)) as client:
        response = await client.post(
            "/api/v1/auth/sign-out",
            headers={"Authorization": "Bearer access-token-a"},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_auth_validation_and_provider_errors_are_safe() -> None:
    gateway = FakeAuthGateway()
    gateway.failure = CloudBaseAuthError("invalid_credentials", status_code=401)
    async with await client_for(make_app(gateway)) as client:
        response = await client.post(
            "/api/v1/auth/sign-in",
            json={"username": "tea@example.com", "password": "Password1"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert "Password1" not in response.text
    assert "access-token" not in response.text
    assert "refresh-token" not in response.text

    unavailable = FakeAuthGateway()
    unavailable.failure = CloudBaseAuthError("auth_provider_unavailable", status_code=503, retryable=True)
    async with await client_for(make_app(unavailable)) as client:
        response = await client.post("/api/v1/auth/register/start", json={"email": "new@example.com"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_provider_unavailable"


@pytest.mark.asyncio
async def test_cloudbase_gateway_uses_documented_paths_and_safe_error_mapping() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/verification"):
            return httpx.Response(200, json={"verification_id": "verification-id-a", "expires_in": 600})
        if request.url.path.endswith("/verification/verify"):
            return httpx.Response(200, json={"verification_token": "verification-token-a", "expires_in": 600})
        if request.url.path.endswith("/signup"):
            return httpx.Response(200, json=token_payload())
        if request.url.path.endswith("/signin"):
            return httpx.Response(200, json=token_payload())
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json=token_payload("refreshed-access-token", "rotated-refresh-token"))
        return httpx.Response(200, json={})

    gateway = CloudBaseAuthGateway(
        env_id="env-test",
        region="ap-shanghai",
        transport=httpx.MockTransport(handler),
    )
    await gateway.send_verification("new@example.com")
    await gateway.verify_verification("verification-id-a", "123456")
    await gateway.sign_up(email="new@example.com", verification_token="verification-token-a", password="Password1")
    await gateway.sign_in(username="new@example.com", password="Password1")
    await gateway.refresh("refresh-token-a")
    await gateway.sign_out("access-token-a")

    assert [request.url.path for request in requests] == [
        "/auth/v1/verification",
        "/auth/v1/verification/verify",
        "/auth/v1/signup",
        "/auth/v1/signin",
        "/auth/v1/token",
        "/auth/v1/user/signout",
    ]
    assert requests[-1].headers["Authorization"] == "Bearer access-token-a"
