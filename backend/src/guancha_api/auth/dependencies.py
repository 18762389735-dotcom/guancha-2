"""FastAPI authentication dependency for the protected auth kernel route."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request

from guancha_api.auth.errors import (
    AuthenticationNotConfigured,
    AuthenticationServiceUnavailable,
    InvalidAccessToken,
)
from guancha_api.auth.models import CurrentUserInfo, OwnerContext


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="invalid_access_token")
    return parts[1]


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> CurrentUserInfo:
    return await _resolve_authenticated_user(request, authorization)


async def _resolve_authenticated_user(
    request: Request,
    authorization: str | None,
) -> CurrentUserInfo:
    token = _bearer_token(authorization)
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise HTTPException(status_code=503, detail="auth_not_configured")

    try:
        identity = await verifier.verify(token)
    except InvalidAccessToken:
        raise HTTPException(status_code=401, detail="invalid_access_token") from None
    except AuthenticationNotConfigured:
        raise HTTPException(status_code=503, detail="auth_not_configured") from None
    except AuthenticationServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail="authentication_service_unavailable",
        ) from None

    repository = getattr(request.app.state, "repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="database_not_configured")
    app_user = await repository.resolve_or_create_app_user(identity.external_subject)
    return CurrentUserInfo(id=app_user.id, created_at=app_user.created_at)


CurrentUser = Annotated[CurrentUserInfo, Depends(get_current_user)]


async def get_owner_context(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_client_id: Annotated[str | None, Header(alias="X-Client-Id")] = None,
) -> OwnerContext:
    """Resolve authenticated ownership first; use anonymous only without auth."""

    if authorization is not None:
        current_user = await _resolve_authenticated_user(request, authorization)
        return OwnerContext.authenticated(current_user.id)

    if x_client_id is None:
        raise HTTPException(status_code=422, detail="missing_client_id")
    try:
        client_id = UUID(x_client_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_client_id") from exc
    return OwnerContext.anonymous(client_id)


Owner = Annotated[OwnerContext, Depends(get_owner_context)]
