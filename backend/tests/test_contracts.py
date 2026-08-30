import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guancha_api.main import app, internal_error_handler
from guancha_api.schemas.contracts import (
    ActionBucket,
    EvidenceSourceType,
    EvidenceStrength,
    ImageInput,
    InformationStatus,
    JobState,
    PublicConfig,
    VerificationStatus,
)


client = TestClient(app, raise_server_exceptions=False)


def test_health_contract() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_public_config_is_the_approved_minimum_contract() -> None:
    response = client.get("/api/v1/config/public")
    assert response.status_code == 200
    assert response.json() == PublicConfig().model_dump(mode="json")


def test_openapi_contains_frozen_phase2_contract_paths() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert set(response.json()["paths"]) == {
        "/health",
        "/api/v1/config/public",
        "/api/v1/me",
        "/api/v1/events",
        "/api/v1/admin/jobs",
        "/api/v1/admin/ai-calls",
        "/api/v1/admin/rule-version",
        "/api/v1/selection-sessions",
        "/api/v1/selection-sessions/{session_id}",
        "/api/v1/selection-sessions/{session_id}/candidates",
        "/api/v1/selection-sessions/{session_id}/analyze",
            "/api/v1/selection-sessions/{session_id}/current-decision",
            "/api/v1/selection-sessions/{session_id}/answer",
            "/api/v1/selection-sessions/{session_id}/snapshot",
            "/api/v1/candidates/{candidate_id}",
        "/api/v1/candidates/{candidate_id}/images",
        "/api/v1/candidate-images/{candidate_image_id}",
        "/api/v1/candidates/{candidate_id}/extraction-jobs",
        "/api/v1/jobs/{job_id}",
        "/api/v1/extraction-versions/{extraction_version_id}",
        "/api/v1/candidates/{candidate_id}/current-extraction",
            "/api/v1/decision-versions/{version_id}",
            "/api/v1/decision-versions/{version_id}/questions",
            "/api/v1/selection-sessions/{session_id}/merchant-replies",
            "/api/v1/merchant-replies/{reply_id}",
            "/api/v1/selection-sessions/{session_id}/rejudge",
            "/api/v1/decision-deltas/{delta_id}",
            "/api/v1/brew-feedback/analyze",
        }
    assert "ApiErrorResponse" in response.json()["components"]["schemas"]
    assert "patch" in response.json()["paths"]["/api/v1/selection-sessions/{session_id}"]


def test_not_found_uses_unified_error_contract() -> None:
    response = client.get("/missing")
    assert response.status_code == 404
    assert response.json()["error"] | {"request_id": None} == {
        "code": "not_found",
        "message": "Resource not found.",
        "retryable": False,
        "resource_id": None,
        "request_id": None,
    }


def test_method_not_allowed_uses_unified_error_contract() -> None:
    response = client.post("/health")
    assert response.status_code == 405
    assert response.json()["error"] | {"request_id": None} == {
        "code": "method_not_allowed",
        "message": "Method not allowed.",
        "retryable": False,
        "resource_id": None,
        "request_id": None,
    }


def test_internal_error_handler_does_not_leak_exception_details() -> None:
    import asyncio
    import json

    from starlette.requests import Request

    response = asyncio.run(
        internal_error_handler(
            Request({"type": "http", "method": "GET", "headers": [], "path": "/health"}),
            RuntimeError("do not expose this"),
        )
    )
    assert response.status_code == 500
    assert json.loads(response.body)["error"] | {"request_id": None} == {
        "code": "internal_error",
        "message": "An unexpected error occurred.",
        "retryable": False,
        "resource_id": None,
        "request_id": None,
    }


def test_image_contract_rejects_third_party_image_type() -> None:
    with pytest.raises(ValidationError):
        ImageInput(content_type="image/webp", size_bytes=1, sha256="a" * 64)


def test_image_contract_enforces_five_megabyte_limit() -> None:
    with pytest.raises(ValidationError):
        ImageInput(content_type="image/jpeg", size_bytes=5_242_881, sha256="a" * 64)


def test_action_bucket_has_the_five_prd_values() -> None:
    assert {item.value for item in ActionBucket} == {
        "currently-selectable",
        "ask-before-buying",
        "sample-first",
        "not-recommended-now",
        "insufficient-information",
    }


def test_phase2_enums_are_closed_contracts() -> None:
    assert {item.value for item in JobState} == {"queued", "processing", "completed", "failed", "stale"}
    assert {item.value for item in InformationStatus} == {"explicit", "inferred", "unknown", "conflict"}
    assert {item.value for item in EvidenceSourceType} == {
        "product-claim", "merchant-claim", "user-input", "system-inference", "brew-feedback"
    }
    assert {item.value for item in VerificationStatus} == {
        "unverified", "user-confirmed", "system-consistent", "conflicting"
    }
    assert {item.value for item in EvidenceStrength} == {"low", "medium", "high"}


def test_sql_and_pydantic_evidence_enums_are_aligned() -> None:
    from pathlib import Path

    sql = Path("supabase/migrations/20260805090000_phase2_single_image_baseline.sql").read_text()
    assert "verification_status in ('unverified', 'user-confirmed', 'system-consistent', 'conflicting')" in sql
    assert "verification_status in ('unverified', 'verified'" not in sql


def test_phase2_api_requires_client_header_and_database_configuration() -> None:
    response = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 422
    response = client.get(
        "/api/v1/jobs/00000000-0000-0000-0000-000000000001",
        headers={"X-Client-Id": "00000000-0000-0000-0000-000000000002"},
    )
    assert response.status_code == 503
