"""CloudBase HTTP token introspection adapter."""

from __future__ import annotations

from typing import Final

import httpx

from guancha_api.auth.errors import AuthenticationServiceUnavailable, InvalidAccessToken
from guancha_api.auth.models import VerifiedIdentity

DEFAULT_CLOUDBASE_REGION: Final = "ap-shanghai"
_DOMESTIC_REGIONS: Final = frozenset({"ap-shanghai", "ap-guangzhou"})
_INTERNATIONAL_REGIONS: Final = frozenset({"ap-singapore"})


def cloudbase_gateway_origin(*, env_id: str, region: str = DEFAULT_CLOUDBASE_REGION) -> str:
    """Return the region-specific CloudBase gateway origin.

    Unknown regions are rejected instead of guessed.  The environment id is
    configuration, not a credential, and is never included in auth errors.
    """

    normalized_env_id = env_id.strip()
    normalized_region = region.strip().lower() or DEFAULT_CLOUDBASE_REGION
    if not normalized_env_id:
        raise ValueError("CloudBase environment id is required")
    if normalized_region in _DOMESTIC_REGIONS:
        suffix = ".api.tcloudbasegateway.com"
    elif normalized_region in _INTERNATIONAL_REGIONS:
        suffix = ".api.intl.tcloudbasegateway.com"
    else:
        raise ValueError("Unsupported CloudBase region")
    return f"https://{normalized_env_id}{suffix}"


class CloudBaseTokenVerifier:
    """Verify access tokens through CloudBase HTTP introspection.

    A short-lived client is intentionally used per verification.  This keeps
    the adapter independent from FastAPI lifespan management and guarantees
    that no long-lived client needs to be closed by the app factory.
    """

    def __init__(
        self,
        *,
        env_id: str,
        region: str = DEFAULT_CLOUDBASE_REGION,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._introspection_url = (
            f"{cloudbase_gateway_origin(env_id=env_id, region=region)}"
            "/auth/v1/token/introspect"
        )
        self._timeout = httpx.Timeout(timeout)
        self._transport = transport

    @property
    def introspection_url(self) -> str:
        return self._introspection_url

    async def verify(self, access_token: str) -> VerifiedIdentity:
        if not access_token.strip():
            raise InvalidAccessToken

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._introspection_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except (httpx.TimeoutException, httpx.TransportError):
            # Do not retain or re-emit the exception, which could expose
            # transport details in a future error/logging path.
            raise AuthenticationServiceUnavailable from None

        if response.status_code >= 500 or response.status_code in {408, 429}:
            raise AuthenticationServiceUnavailable
        if response.status_code < 200 or response.status_code >= 300:
            raise InvalidAccessToken

        try:
            payload = response.json()
        except ValueError:
            raise InvalidAccessToken from None
        if not isinstance(payload, dict):
            raise InvalidAccessToken

        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise InvalidAccessToken

        if "scope" in payload:
            scope = payload["scope"]
            if not isinstance(scope, str):
                raise InvalidAccessToken
            scope_tokens = scope.split()
            if not scope_tokens or "anonymous" in scope_tokens:
                raise InvalidAccessToken

        return VerifiedIdentity(external_subject=subject.strip())
