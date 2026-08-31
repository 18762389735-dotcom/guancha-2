"""Refresh-token cookie policy shared by Auth routes and error handlers."""

from __future__ import annotations

import os

from fastapi import Request, Response


AUTH_REFRESH_COOKIE = "guancha.refresh-token.v1"
AUTH_COOKIE_PATH = "/api/v1/auth"


def cookie_secure(request: Request) -> bool:
    configured = os.getenv("GUANCHA_AUTH_COOKIE_SECURE")
    if configured is not None and configured.strip():
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    forwarded = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def set_refresh_cookie(request: Request, response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=AUTH_REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def clear_refresh_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        key=AUTH_REFRESH_COOKIE,
        secure=cookie_secure(request),
        httponly=True,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )
