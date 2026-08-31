"""Small server-to-server adapter for CloudBase HTTP Authentication."""

from __future__ import annotations

from typing import Any, Final

import httpx

from guancha_api.auth.cloudbase import DEFAULT_CLOUDBASE_REGION, cloudbase_gateway_origin


class CloudBaseAuthError(RuntimeError):
    """A safe, Guancha-facing error from the CloudBase Auth provider."""

    def __init__(self, code: str, *, status_code: int, retryable: bool = False, clear_cookie: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.clear_cookie = clear_cookie


_ERROR_ALIASES: Final = {
    "invalid_username_or_password": "invalid_credentials",
    "incorrect_username_or_password": "invalid_credentials",
    "invalid_credentials": "invalid_credentials",
    "invalid_verification_code": "verification_invalid",
    "verification_invalid": "verification_invalid",
    "verification_expired": "verification_expired",
    "verification_code_expired": "verification_expired",
    "expired_verification_code": "verification_expired",
    "rate_limit_exceeded": "verification_rate_limited",
    "verification_rate_limit_exceeded": "verification_rate_limited",
    "user_already_exists": "registration_conflict",
    "user_exists": "registration_conflict",
    "already_exists": "registration_conflict",
    "captcha_required": "captcha_required",
    "invalid_refresh_token": "session_expired",
    "refresh_token_invalid": "session_expired",
    "token_expired": "session_expired",
    "session_expired": "session_expired",
}


class CloudBaseAuthGateway:
    """Call only the configured CloudBase Auth HTTP endpoints.

    The adapter deliberately exposes no generic proxy method.  Each public
    operation supplies its own bounded payload and safe fallback error code,
    keeping upstream response details out of Guancha's API errors.
    """

    def __init__(
        self,
        *,
        env_id: str,
        region: str = DEFAULT_CLOUDBASE_REGION,
        timeout: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._origin = cloudbase_gateway_origin(env_id=env_id, region=region)
        self._timeout = httpx.Timeout(timeout)
        self._transport = transport

    @property
    def origin(self) -> str:
        return self._origin

    @staticmethod
    def _upstream_code(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        candidates: list[Any] = [payload.get("code"), payload.get("error")]
        if isinstance(payload.get("error"), dict):
            candidates.append(payload.get("error", {}).get("error_code"))
        candidates.append(payload.get("error_code"))
        error = payload.get("error")
        if isinstance(error, dict):
            candidates.extend([error.get("code"), error.get("error_code")])
        for candidate in candidates:
            if isinstance(candidate, str) and candidate in _ERROR_ALIASES:
                return candidate
        return None

    @classmethod
    def _provider_error(
        cls,
        response: httpx.Response,
        *,
        fallback_code: str,
    ) -> CloudBaseAuthError:
        if response.status_code >= 500 or response.status_code in {408, 429}:
            if response.status_code == 429 and fallback_code in {"verification_invalid", "verification_rate_limited"}:
                return CloudBaseAuthError("verification_rate_limited", status_code=429)
            return CloudBaseAuthError("auth_provider_unavailable", status_code=503, retryable=True)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        upstream_code = cls._upstream_code(payload)
        mapped = _ERROR_ALIASES.get(upstream_code or "", fallback_code)
        if mapped in {"invalid_credentials", "session_expired"}:
            status_code = 401
        elif mapped in {"verification_invalid", "verification_expired"}:
            status_code = 400
        elif mapped == "registration_conflict":
            status_code = 409
        elif mapped == "verification_rate_limited":
            status_code = 429
        else:
            status_code = response.status_code
        return CloudBaseAuthError(mapped, status_code=status_code)

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        fallback_code: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._origin}{path}",
                    json=payload,
                    headers=headers,
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise CloudBaseAuthError("auth_provider_unavailable", status_code=503, retryable=True) from None

        if response.status_code < 200 or response.status_code >= 300:
            raise self._provider_error(response, fallback_code=fallback_code)
        try:
            result = response.json()
        except ValueError:
            raise CloudBaseAuthError("auth_provider_unavailable", status_code=503, retryable=True) from None
        if not isinstance(result, dict):
            raise CloudBaseAuthError("auth_provider_unavailable", status_code=503, retryable=True)
        return result

    async def send_verification(self, email: str) -> dict[str, Any]:
        return await self._post(
            "/auth/v1/verification",
            {"email": email, "target": "ANY"},
            fallback_code="verification_rate_limited",
        )

    async def verify_verification(self, verification_id: str, verification_code: str) -> dict[str, Any]:
        return await self._post(
            "/auth/v1/verification/verify",
            {"verification_id": verification_id, "verification_code": verification_code},
            fallback_code="verification_invalid",
        )

    async def sign_up(self, *, email: str, verification_token: str, password: str) -> dict[str, Any]:
        return await self._post(
            "/auth/v1/signup",
            {"email": email, "verification_token": verification_token, "password": password},
            fallback_code="registration_conflict",
        )

    async def sign_in(self, *, username: str, password: str) -> dict[str, Any]:
        return await self._post(
            "/auth/v1/signin",
            {"username": username, "password": password},
            fallback_code="invalid_credentials",
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._post(
            "/auth/v1/token",
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            fallback_code="session_expired",
        )

    async def sign_out(self, access_token: str) -> None:
        try:
            await self._post(
                "/auth/v1/user/signout",
                {},
                fallback_code="session_expired",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except CloudBaseAuthError as exc:
            # Logout is locally complete even when the upstream access token
            # has already expired.  The caller still clears its cookie.
            if exc.code in {"session_expired", "invalid_credentials"}:
                return
            raise
