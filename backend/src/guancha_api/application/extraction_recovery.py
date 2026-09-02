"""Shared stale-horizon policy for asynchronous extraction Jobs."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone


DEFAULT_EXTRACTION_STALE_AFTER_SECONDS = 300.0
SCF_EXECUTION_TIMEOUT_SECONDS = 180.0


def extraction_stale_after_seconds_from_environment() -> float:
    """Return a recovery horizon that is safely longer than SCF execution."""

    raw_value = os.getenv("GUANCHA_EXTRACTION_STALE_AFTER_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_EXTRACTION_STALE_AFTER_SECONDS
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "GUANCHA_EXTRACTION_STALE_AFTER_SECONDS must be numeric"
        ) from exc
    if value <= SCF_EXECUTION_TIMEOUT_SECONDS:
        raise RuntimeError(
            "GUANCHA_EXTRACTION_STALE_AFTER_SECONDS must exceed the SCF execution timeout"
        )
    return value


def extraction_stale_before_from_environment(
    *, now: datetime | None = None
) -> datetime:
    reference_time = now or datetime.now(timezone.utc)
    return reference_time - timedelta(
        seconds=extraction_stale_after_seconds_from_environment()
    )


__all__ = [
    "DEFAULT_EXTRACTION_STALE_AFTER_SECONDS",
    "SCF_EXECUTION_TIMEOUT_SECONDS",
    "extraction_stale_after_seconds_from_environment",
    "extraction_stale_before_from_environment",
]
