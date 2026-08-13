"""Image/job ASGI integration tests; generated images never enter source control."""
from __future__ import annotations

import os
import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from psycopg.rows import dict_row

from guancha_api.application.task_runners import ManualTaskRunner
from guancha_api.infrastructure.image_pipeline import sanitize_image_upload
from guancha_api.infrastructure.storage.memory import InMemoryTemporaryPrivateStorage
from guancha_api.main import create_app
from guancha_api.providers.fake import FakeProvider
from guancha_api.repositories.postgres import PostgresPhase2Repository
from guancha_api.repositories.postgres import IdempotencyConflict
from guancha_api.repositories.idempotency import request_hash
from guancha_api.schemas.contracts import ErrorCode

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.asyncio


class FailingTaskRunner(ManualTaskRunner):
    async def enqueue(self, *, job_id: object, task: object) -> None:
        del job_id, task
        raise RuntimeError("queue unavailable")


class FailingDeleteStorage(InMemoryTemporaryPrivateStorage):
    def __init__(self) -> None:
        super().__init__()
        self.fail_delete = False

    async def delete(self, *, object_key: str) -> None:
        if self.fail_delete:
            raise OSError("private storage unavailable")
        await super().delete(object_key=object_key)


class FailingPutStorage(InMemoryTemporaryPrivateStorage):
    async def put_private(self, *, object_key: str, content_type: str, data: bytes) -> None:
        del object_key, content_type, data
        raise OSError("private storage unavailable")


class FailingImageCreateRepository(PostgresPhase2Repository):
    async def create_image_and_initial_job(self, **kwargs: object) -> object:
        del kwargs
        raise psycopg.OperationalError("database write unavailable")


class FailingJobFailureRepository(PostgresPhase2Repository):
    async def fail_extraction_job(self, **kwargs: object) -> None:
        del kwargs
        raise psycopg.OperationalError("database failure persistence unavailable")


@pytest_asyncio.fixture
async def repository() -> PostgresPhase2Repository:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL ASGI integration tests")
    connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
    migration_directory = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    migration = "\n".join(path.read_text(encoding="utf-8") for path in sorted(migration_directory.glob("*.sql")))
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


def _jpeg() -> bytes:
    image = Image.new("RGB", (640, 480), "green")
    output = BytesIO(); image.save(output, "JPEG")
    return output.getvalue()


def _png() -> bytes:
    image = Image.new("RGB", (640, 480), "blue")
    metadata = PngInfo()
    metadata.add_text("Device", "private-camera")
    output = BytesIO(); image.save(output, "PNG", pnginfo=metadata)
    return output.getvalue()


def _oriented_jpeg() -> bytes:
    image = Image.new("RGB", (640, 480), "purple")
    exif = Image.Exif()
    exif[274] = 6
    exif[271] = "private-camera"
    output = BytesIO()
    image.save(output, "JPEG", exif=exif)
    return output.getvalue()


async def _candidate(client: httpx.AsyncClient, client_id: str) -> str:
    session = await client.post("/api/v1/selection-sessions", json={"need": {}}, headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())})
    candidate = await client.post(f"/api/v1/selection-sessions/{session.json()['id']}/candidates", json={"display_label": "A"}, headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())})
    return candidate.json()["id"]


async def _set_concurrency_timeouts(connection: psycopg.AsyncConnection[dict[str, object]]) -> None:
    """Bound lock diagnostics in the real-PostgreSQL concurrency test only."""
    async with connection.cursor() as cursor:
        await cursor.execute("set lock_timeout = '3s'")
        await cursor.execute("set statement_timeout = '10s'")


def _extraction_payload() -> dict[str, object]:
    return {
        "product_name": "安溪铁观音", "tea_category": "乌龙茶", "tea_subtype": "铁观音",
        "origin": "安溪", "roast_or_style": None, "aroma_claims": ["兰花香"],
        "taste_claims": ["回甘"], "season": None, "year_or_batch": None, "grade": None,
        "weight": "250g", "price": "99元", "brew_claims": [], "risk_flags": [],
        "evidence": [{
            "field_name": "product_name", "raw_text": "安溪铁观音", "normalized_value": "安溪铁观音",
            "model_confidence": 0.9, "information_status": "explicit",
            "source_type": "product-claim", "verification_status": "unverified",
            "source_location": "商品标题", "evidence_strength": "high",
        }],
    }


async def test_fake_extraction_persists_version_and_exposes_current_result(
    repository: PostgresPhase2Repository,
) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    provider = FakeProvider(extraction_response=_extraction_payload())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner, provider=provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        uploaded = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        assert uploaded.status_code == 201 and uploaded.json()["extraction_job"]["status"] == "queued"
        assert await runner.drain() == 1
        job = await client.get(f"/api/v1/jobs/{uploaded.json()['extraction_job']['id']}", headers={"X-Client-Id": client_id})
        image = await client.get(f"/api/v1/candidate-images/{uploaded.json()['image']['id']}", headers={"X-Client-Id": client_id})
        version_id = job.json()["extraction_version_id"]
        version = await client.get(f"/api/v1/extraction-versions/{version_id}", headers={"X-Client-Id": client_id})
        current = await client.get(f"/api/v1/candidates/{candidate_id}/current-extraction", headers={"X-Client-Id": client_id})
        outsider = str(uuid4())
        forbidden = await client.get(f"/api/v1/extraction-versions/{version_id}", headers={"X-Client-Id": outsider})
    assert job.json()["status"] == "completed"
    assert image.json()["status"] == "completed"
    assert version.status_code == current.status_code == 200
    assert version.json()["id"] == current.json()["id"] == version_id
    evidence = version.json()["evidence_items"][0]
    assert evidence["source_type"] == "product-claim"
    assert evidence["verification_status"] == "unverified"
    assert {item["field_name"] for item in version.json()["evidence_items"]} >= {
        "product_name", "tea_category", "tea_subtype", "origin", "weight", "price"
    }
    assert provider.extraction_calls == 1 and uploaded.json()["image"]["id"] in "".join(storage.objects)
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "resource_not_owned"


async def test_screenshot_evidence_cannot_be_promoted_by_provider(
    repository: PostgresPhase2Repository,
) -> None:
    payload = _extraction_payload()
    payload["evidence"][0]["source_type"] = "merchant-claim"
    payload["evidence"][0]["verification_status"] = "system-consistent"
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner, provider=FakeProvider(extraction_response=payload))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        uploaded = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}, files={"file": ("tea.jpg", _jpeg(), "image/jpeg")})
        await runner.drain()
        job = await client.get(f"/api/v1/jobs/{uploaded.json()['extraction_job']['id']}", headers={"X-Client-Id": client_id})
        version = await client.get(f"/api/v1/extraction-versions/{job.json()['extraction_version_id']}", headers={"X-Client-Id": client_id})
    evidence = version.json()["evidence_items"][0]
    assert evidence["source_type"] == "product-claim"
    assert evidence["verification_status"] == "unverified"


async def test_invalid_fake_provider_result_fails_without_partial_extraction(
    repository: PostgresPhase2Repository,
) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    app = create_app(
        repository=repository, temporary_storage=storage, task_runner=runner,
        provider=FakeProvider(extraction_response={"bad": True}, repair_response={"still": "bad"}),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        uploaded = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        await runner.drain()
        job = await client.get(f"/api/v1/jobs/{uploaded.json()['extraction_job']['id']}", headers={"X-Client-Id": client_id})
        current = await client.get(f"/api/v1/candidates/{candidate_id}/current-extraction", headers={"X-Client-Id": client_id})
    assert job.json()["status"] == "failed"
    assert job.json()["error_code"] == "ai_schema_invalid"
    assert current.status_code == 404
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select count(*) as count from extraction_versions where candidate_id=%s", (candidate_id,))
        assert await cursor.fetchone() == {"count": 0}


async def test_fake_provider_network_retry_completes_once(repository: PostgresPhase2Repository) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    provider = FakeProvider(extraction_response=_extraction_payload(), network_failures_before_success=1)
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner, provider=provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        uploaded = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        await runner.drain()
        job = await client.get(f"/api/v1/jobs/{uploaded.json()['extraction_job']['id']}", headers={"X-Client-Id": client_id})
    assert job.json()["status"] == "completed"
    assert provider.extraction_calls == 2


async def test_upload_replay_get_delete_and_job_metadata(repository: PostgresPhase2Repository) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner, provider=FakeProvider(extraction_response={"evidence": [{"field_name": "x", "raw_text": "x", "normalized_value": "x", "model_confidence": 1, "information_status": "explicit", "source_type": "product-claim", "verification_status": "unverified", "source_location": "x", "evidence_strength": "high"}]}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        headers = {"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}
        files = {"file": ("tea.jpg", _jpeg(), "image/jpeg")}
        first = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers=headers, files=files)
        assert first.status_code == 201
        replay = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers=headers, files={"file": ("tea.jpg", _jpeg(), "image/jpeg")})
        assert replay.status_code == 201 and replay.json() == first.json() and len(storage.objects) == 1
        job = first.json()["extraction_job"]
        assert {"stage", "error_code", "extraction_version_id"} <= job.keys()
        fetched = await client.get(f"/api/v1/jobs/{job['id']}", headers={"X-Client-Id": client_id})
        assert fetched.status_code == 200 and fetched.json()["id"] == job["id"]
        deleted = await client.delete(f"/api/v1/candidate-images/{first.json()['image']['id']}", headers={"X-Client-Id": client_id})
        assert deleted.status_code == 204 and not storage.objects


async def test_successful_jpeg_png_are_normalized_without_exif(
    repository: PostgresPhase2Repository,
) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner)
    original_jpeg, original_png = _oriented_jpeg(), _png()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        jpeg_candidate = await _candidate(client, client_id)
        jpeg = await client.post(f"/api/v1/candidates/{jpeg_candidate}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}, files={"file": ("oriented.jpg", original_jpeg, "image/jpeg")})
        png_candidate = await _candidate(client, client_id)
        png = await client.post(f"/api/v1/candidates/{png_candidate}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}, files={"file": ("tea.png", original_png, "image/png")})
    assert jpeg.status_code == png.status_code == 201
    assert (jpeg.json()["image"]["width"], jpeg.json()["image"]["height"]) == (480, 640)
    assert png.json()["image"]["content_type"] == "image/png"
    stored_jpeg = next(data for kind, data in storage.objects.values() if kind == "image/jpeg")
    with Image.open(BytesIO(stored_jpeg)) as clean:
        assert clean.mode == "RGB" and clean.getexif() == {}
    stored_png = next(data for kind, data in storage.objects.values() if kind == "image/png")
    with Image.open(BytesIO(stored_png)) as clean:
        assert clean.mode == "RGB" and not clean.info
    async with repository._connection.cursor() as cursor:
        await cursor.execute(
            "select source_sha256, sanitized_sha256, content_type, size_bytes, width, height from candidate_images order by created_at"
        )
        rows = await cursor.fetchall()
    assert {row["content_type"] for row in rows} == {"image/jpeg", "image/png"}
    assert all(row["source_sha256"] and row["sanitized_sha256"] for row in rows)
    assert {row["source_sha256"] for row in rows} == {
        hashlib.sha256(original_jpeg).hexdigest(), hashlib.sha256(original_png).hexdigest(),
    }
    expected_jpeg = sanitize_image_upload(data=original_jpeg, declared_content_type="image/jpeg").sanitized_sha256
    expected_png = sanitize_image_upload(data=original_png, declared_content_type="image/png").sanitized_sha256
    assert any(row["sanitized_sha256"] == expected_jpeg for row in rows)
    assert any(row["sanitized_sha256"] == expected_png for row in rows)
    assert all(row["size_bytes"] > 0 and row["width"] > 0 and row["height"] > 0 for row in rows)
    assert jpeg.json()["image"]["sha256"] == expected_jpeg
    assert jpeg.json()["image"]["size_bytes"] == len(stored_jpeg)
    assert png.json()["image"]["sha256"] == expected_png
    assert png.json()["image"]["size_bytes"] == len(stored_png)


async def test_image_rejects_invalid_bytes(repository: PostgresPhase2Repository) -> None:
    app = create_app(repository=repository, temporary_storage=InMemoryTemporaryPrivateStorage(), task_runner=ManualTaskRunner())
    client_id = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        response = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}, files={"file": ("bad.jpg", b"not image", "image/jpeg")})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsafe_or_corrupt_image"


@pytest.mark.parametrize(
    ("name", "payload_factory", "declared_type", "expected_code"),
    [
        ("empty", lambda: b"", "image/jpeg", "unsafe_or_corrupt_image"),
        ("truncated", lambda: b"\xff\xd8\xff\xe0", "image/jpeg", "unsafe_or_corrupt_image"),
        ("mime-mismatch", _png, "image/jpeg", "unsafe_or_corrupt_image"),
        ("reverse-mime-mismatch", _jpeg, "image/png", "unsafe_or_corrupt_image"),
        ("unsupported", lambda: b"GIF89a", "image/gif", "invalid_image_type"),
        ("too-large", lambda: b"\x89PNG\r\n\x1a\n" + b"x" * 5_242_881, "image/png", "image_too_large"),
    ],
)
async def test_upload_security_matrix_rejects_untrusted_input(
    repository: PostgresPhase2Repository,
    name: str,
    payload_factory: object,
    declared_type: str,
    expected_code: str,
) -> None:
    app = create_app(repository=repository, temporary_storage=InMemoryTemporaryPrivateStorage(), task_runner=ManualTaskRunner())
    client_id = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        response = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": (f"{name}.bin", payload_factory(), declared_type)},  # type: ignore[operator]
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code


async def test_image_conflict_limit_and_cross_client_access_are_isolated(repository: PostgresPhase2Repository) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        key = str(uuid4())
        first = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": key}, files={"file": ("tea.jpg", _jpeg(), "image/jpeg")})
        conflict = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": key}, files={"file": ("tea.png", _png(), "image/png")})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
        second = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}, files={"file": ("tea.png", _png(), "image/png")})
        assert second.status_code == 201
        limited = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}, files={"file": ("tea2.png", _png(), "image/png")})
        assert limited.status_code == 409 and limited.json()["error"]["code"] == "candidate_image_limit_exceeded"
        outsider = str(uuid4())
        for url in (f"/api/v1/candidate-images/{first.json()['image']['id']}", f"/api/v1/jobs/{first.json()['extraction_job']['id']}"):
            response = await client.get(url, headers={"X-Client-Id": outsider})
            assert response.status_code == 403 and response.json()["error"]["code"] == "resource_not_owned", (url, response.json())
        deleted = await client.delete(
            f"/api/v1/candidate-images/{first.json()['image']['id']}",
            headers={"X-Client-Id": outsider},
        )
        assert deleted.status_code == 403 and deleted.json()["error"]["code"] == "resource_not_owned"
        retried = await client.post(
            f"/api/v1/candidates/{candidate_id}/extraction-jobs",
            headers={"X-Client-Id": outsider, "Idempotency-Key": str(uuid4())},
        )
        assert retried.status_code == 403 and retried.json()["error"]["code"] == "resource_not_owned"


async def test_missing_resources_and_repeat_delete_use_error_envelope(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id = InMemoryTemporaryPrivateStorage(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=ManualTaskRunner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        missing_image = await client.get(f"/api/v1/candidate-images/{uuid4()}", headers={"X-Client-Id": client_id})
        missing_job = await client.get(f"/api/v1/jobs/{uuid4()}", headers={"X-Client-Id": client_id})
        uploaded = await client.post(f"/api/v1/candidates/{candidate_id}/images", headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}, files={"file": ("tea.jpg", _jpeg(), "image/jpeg")})
        image_id = uploaded.json()["image"]["id"]
        first_delete = await client.delete(f"/api/v1/candidate-images/{image_id}", headers={"X-Client-Id": client_id})
        second_delete = await client.delete(f"/api/v1/candidate-images/{image_id}", headers={"X-Client-Id": client_id})
    assert missing_image.status_code == 404 and missing_image.json()["error"]["code"] == "candidate_image_not_found"
    assert missing_job.status_code == 404 and "error" in missing_job.json()
    assert first_delete.status_code == second_delete.status_code == 204


async def test_enqueue_failure_keeps_private_object_and_is_retryable(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id = InMemoryTemporaryPrivateStorage(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=FailingTaskRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        upload_key = str(uuid4())
        response = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": upload_key},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert len(storage.objects) == 1
    async with repository._connection.cursor() as cursor:
        await cursor.execute(
            "select status, error_code from analysis_jobs where candidate_id=%s", (candidate_id,)
        )
        job = await cursor.fetchone()
        await cursor.execute(
            "select status, error_code from candidate_images where candidate_id=%s", (candidate_id,)
        )
        image = await cursor.fetchone()
        await cursor.execute("select count(*) as count from extraction_versions")
        versions = await cursor.fetchone()
    assert job == {"status": "failed", "error_code": "worker_interrupted"}
    assert image == {"status": "failed", "error_code": "worker_interrupted"}
    assert versions == {"count": 0}
    retry_runner = ManualTaskRunner()
    retry_app = create_app(repository=repository, temporary_storage=storage, task_runner=retry_runner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=retry_app), base_url="http://test") as client:
        original_replay = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": upload_key},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        recovered = await client.post(
            f"/api/v1/candidates/{candidate_id}/extraction-jobs",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
        )
    assert original_replay.status_code == 201
    assert original_replay.json()["extraction_job"]["status"] == "failed"
    assert recovered.status_code == 201 and recovered.json()["status"] == "queued"
    assert retry_runner.pending_count == 1
    assert len(storage.objects) == 1
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select count(*) as count from candidate_images where candidate_id=%s", (candidate_id,))
        assert await cursor.fetchone() == {"count": 1}


async def test_enqueue_failure_does_not_delete_object_before_failure_state_is_persisted(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id = InMemoryTemporaryPrivateStorage(), str(uuid4())
    failing_repository = FailingJobFailureRepository(repository._connection)
    app = create_app(repository=failing_repository, temporary_storage=storage, task_runner=FailingTaskRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        response = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == 503
    assert len(storage.objects) == 1
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select status from analysis_jobs where candidate_id=%s", (candidate_id,))
        assert await cursor.fetchone() == {"status": "queued"}
        await cursor.execute("select status from candidate_images where candidate_id=%s", (candidate_id,))
        assert await cursor.fetchone() == {"status": "received"}


async def test_startup_recovery_makes_interrupted_enqueue_failure_retryable(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id = InMemoryTemporaryPrivateStorage(), str(uuid4())
    failing_repository = FailingJobFailureRepository(repository._connection)
    failing_app = create_app(repository=failing_repository, temporary_storage=storage, task_runner=FailingTaskRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=failing_app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        failed = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    assert failed.status_code == 503 and len(storage.objects) == 1
    async with repository._connection.cursor() as cursor:
        await cursor.execute(
            "update analysis_jobs set created_at=now() - interval '91 seconds' where candidate_id=%s",
            (candidate_id,),
        )
    retry_runner = ManualTaskRunner()
    retry_app = create_app(repository=repository, temporary_storage=storage, task_runner=retry_runner)
    # Exercise create_app's actual startup hook, rather than calling the
    # repository recovery routine directly.
    async with retry_app.router.lifespan_context(retry_app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=retry_app), base_url="http://test") as client:
            recovered = await client.post(
                f"/api/v1/candidates/{candidate_id}/extraction-jobs",
                headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            )
        assert retry_runner.pending_count == 1
    assert recovered.status_code == 201
    assert recovered.json()["status"] == "queued"
    assert retry_runner.pending_count == 0


async def test_interrupted_job_retry_uses_new_idempotency_key_and_replays(
    repository: PostgresPhase2Repository,
) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        upload_key = str(uuid4())
        uploaded = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": upload_key},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        job_id = uploaded.json()["extraction_job"]["id"]
        await repository.fail_extraction_job(
            job_id=job_id, error_code=ErrorCode.WORKER_INTERRUPTED
        )
        retry_key = str(uuid4())
        headers = {"X-Client-Id": client_id, "Idempotency-Key": retry_key}
        first = await client.post(f"/api/v1/candidates/{candidate_id}/extraction-jobs", headers=headers)
        replay = await client.post(f"/api/v1/candidates/{candidate_id}/extraction-jobs", headers=headers)
        polled = await client.get(f"/api/v1/jobs/{job_id}", headers={"X-Client-Id": client_id})
        original_upload_replay = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": upload_key},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    assert first.status_code == replay.status_code == 201
    assert polled.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["status"] == "queued"
    assert {"stage", "error_code", "extraction_version_id"} <= polled.json().keys()
    assert original_upload_replay.status_code == 201
    assert original_upload_replay.json()["image"]["id"] == uploaded.json()["image"]["id"]
    assert runner.pending_count == 2


async def test_retry_different_key_while_job_queued_returns_in_progress(
    repository: PostgresPhase2Repository,
) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, client_id)
        uploaded = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        # The initial queued upload job is active; a distinct retry key must
        # report the frozen in-progress code rather than "not retryable".
        response = await client.post(
            f"/api/v1/candidates/{candidate_id}/extraction-jobs",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
        )
    assert uploaded.status_code == 201
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "candidate_extraction_in_progress"


async def test_retry_repository_rejects_same_key_with_different_request_hash(
    repository: PostgresPhase2Repository,
) -> None:
    storage, runner, client_id = InMemoryTemporaryPrivateStorage(), ManualTaskRunner(), uuid4()
    app = create_app(repository=repository, temporary_storage=storage, task_runner=runner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        candidate_id = await _candidate(client, str(client_id))
        uploaded = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": str(client_id), "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    original_job = uploaded.json()["extraction_job"]["id"]
    await repository.fail_extraction_job(job_id=original_job, error_code=ErrorCode.WORKER_INTERRUPTED)
    retry_key = uuid4()
    digest = request_hash({"candidate_id": candidate_id, "operation": "retry-extraction"})
    restored = await repository.requeue_interrupted_job(
        failed_job_id=original_job, new_job_id=uuid4(), idempotency_key=retry_key, request_hash=digest
    )
    assert restored is not None and restored[1]
    with pytest.raises(IdempotencyConflict):
        await repository.requeue_interrupted_job(
            failed_job_id=original_job, new_job_id=uuid4(), idempotency_key=retry_key,
            request_hash=request_hash({"candidate_id": candidate_id, "operation": "different"}),
        )


async def test_retry_same_key_recovers_after_retry_enqueue_failure(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id = InMemoryTemporaryPrivateStorage(), str(uuid4())
    setup_runner = ManualTaskRunner()
    app = create_app(repository=repository, temporary_storage=storage, task_runner=setup_runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        uploaded = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        await repository.fail_extraction_job(
            job_id=uploaded.json()["extraction_job"]["id"], error_code=ErrorCode.WORKER_INTERRUPTED
        )
        retry_key = str(uuid4())
        failing_app = create_app(repository=repository, temporary_storage=storage, task_runner=FailingTaskRunner())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=failing_app, raise_app_exceptions=False), base_url="http://test"
        ) as failing_client:
            failed = await failing_client.post(
                f"/api/v1/candidates/{candidate_id}/extraction-jobs",
                headers={"X-Client-Id": client_id, "Idempotency-Key": retry_key},
            )
        recovery_runner = ManualTaskRunner()
        recovery_app = create_app(repository=repository, temporary_storage=storage, task_runner=recovery_runner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=recovery_app), base_url="http://test") as recovery_client:
            recovered = await recovery_client.post(
                f"/api/v1/candidates/{candidate_id}/extraction-jobs",
                headers={"X-Client-Id": client_id, "Idempotency-Key": retry_key},
            )
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "service_unavailable"
    assert recovered.status_code == 201
    assert recovered.json()["status"] == "queued"
    assert recovery_runner.pending_count == 1
    assert len(storage.objects) == 1


async def test_delete_cleanup_failure_is_recoverable_and_uses_typed_error(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id = FailingDeleteStorage(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=ManualTaskRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        uploaded = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        image_id = uploaded.json()["image"]["id"]
        storage.fail_delete = True
        failed = await client.delete(f"/api/v1/candidate-images/{image_id}", headers={"X-Client-Id": client_id})
        storage.fail_delete = False
        recovered = await client.delete(f"/api/v1/candidate-images/{image_id}", headers={"X-Client-Id": client_id})
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "temporary_image_cleanup_failed"
    assert recovered.status_code == 204
    assert not storage.objects


async def test_database_write_failure_compensates_private_object(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id = InMemoryTemporaryPrivateStorage(), str(uuid4())
    failing_repository = FailingImageCreateRepository(repository._connection)
    app = create_app(repository=failing_repository, temporary_storage=storage, task_runner=ManualTaskRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        response = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert not storage.objects


async def test_database_cleanup_failure_uses_deterministic_key_for_idempotent_recovery(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id, key = FailingDeleteStorage(), str(uuid4()), str(uuid4())
    failing_repository = FailingImageCreateRepository(repository._connection)
    failing_app = create_app(
        repository=failing_repository, temporary_storage=storage, task_runner=ManualTaskRunner()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=failing_app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        storage.fail_delete = True
        failed = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": key},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "temporary_image_cleanup_failed"
    assert len(storage.objects) == 1
    storage.fail_delete = False
    recovery_app = create_app(repository=repository, temporary_storage=storage, task_runner=ManualTaskRunner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=recovery_app), base_url="http://test") as client:
        recovered = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": key},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    assert recovered.status_code == 201
    assert len(storage.objects) == 1
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select count(*) as count from candidate_images where candidate_id=%s", (candidate_id,))
        assert await cursor.fetchone() == {"count": 1}


async def test_storage_write_failure_creates_no_database_resources(
    repository: PostgresPhase2Repository,
) -> None:
    storage, client_id = FailingPutStorage(), str(uuid4())
    app = create_app(repository=repository, temporary_storage=storage, task_runner=ManualTaskRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        candidate_id = await _candidate(client, client_id)
        response = await client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select count(*) as count from candidate_images where candidate_id=%s", (candidate_id,))
        assert await cursor.fetchone() == {"count": 0}


async def test_concurrent_same_key_upload_creates_one_image_and_job(
    repository: PostgresPhase2Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, client_id, key = InMemoryTemporaryPrivateStorage(), str(uuid4()), str(uuid4())
    first_runner, second_runner = ManualTaskRunner(), ManualTaskRunner()
    barrier = asyncio.Barrier(2)
    original_preflight = PostgresPhase2Repository.find_image_job_replay

    async def synchronized_preflight(
        instance: PostgresPhase2Repository, **kwargs: object
    ) -> object:
        result = await original_preflight(instance, **kwargs)  # type: ignore[arg-type]
        await barrier.wait()
        return result

    monkeypatch.setattr(PostgresPhase2Repository, "find_image_job_replay", synchronized_preflight)
    first_app = create_app(repository=repository, temporary_storage=storage, task_runner=first_runner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first_app), base_url="http://test") as first_client:
        candidate_id = await _candidate(first_client, client_id)
        await _set_concurrency_timeouts(repository._connection)
        second_connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
        await second_connection.set_autocommit(True)
        await _set_concurrency_timeouts(second_connection)
        try:
            second_app = create_app(repository=PostgresPhase2Repository(second_connection), temporary_storage=storage, task_runner=second_runner)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second_app), base_url="http://test") as second_client:
                headers = {"X-Client-Id": client_id, "Idempotency-Key": key}
                one, two = await asyncio.wait_for(
                    asyncio.gather(
                        first_client.post(f"/api/v1/candidates/{candidate_id}/images", headers=headers, files={"file": ("a.jpg", _jpeg(), "image/jpeg")}),
                        second_client.post(f"/api/v1/candidates/{candidate_id}/images", headers=headers, files={"file": ("a.jpg", _jpeg(), "image/jpeg")}),
                    ),
                    timeout=15,
                )
        finally:
            await second_connection.close()
    assert one.status_code == two.status_code == 201
    assert one.json()["image"]["id"] == two.json()["image"]["id"]
    assert first_runner.pending_count + second_runner.pending_count == 1
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select count(*) as count from candidate_images where candidate_id=%s", (candidate_id,))
        images = await cursor.fetchone()
        await cursor.execute("select count(*) as count from analysis_jobs where candidate_id=%s", (candidate_id,))
        jobs = await cursor.fetchone()
    assert images == {"count": 1}
    assert jobs == {"count": 1}
    assert len(storage.objects) == 1


async def test_concurrent_same_key_retry_creates_one_recovery_job(
    repository: PostgresPhase2Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, client_id = InMemoryTemporaryPrivateStorage(), str(uuid4())
    initial_runner = ManualTaskRunner()
    first_app = create_app(repository=repository, temporary_storage=storage, task_runner=initial_runner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first_app), base_url="http://test") as setup_client:
        candidate_id = await _candidate(setup_client, client_id)
        uploaded = await setup_client.post(
            f"/api/v1/candidates/{candidate_id}/images",
            headers={"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())},
            files={"file": ("tea.jpg", _jpeg(), "image/jpeg")},
        )
        await repository.fail_extraction_job(
            job_id=uploaded.json()["extraction_job"]["id"], error_code=ErrorCode.WORKER_INTERRUPTED
        )
        await _set_concurrency_timeouts(repository._connection)
        second_connection = await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)
        await second_connection.set_autocommit(True)
        await _set_concurrency_timeouts(second_connection)
        barrier = asyncio.Barrier(2)
        original_requeue = PostgresPhase2Repository.requeue_interrupted_job

        async def synchronized_requeue(instance: PostgresPhase2Repository, **kwargs: object) -> object:
            await barrier.wait()
            return await original_requeue(instance, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(PostgresPhase2Repository, "requeue_interrupted_job", synchronized_requeue)
        first_runner, second_runner = ManualTaskRunner(), ManualTaskRunner()
        try:
            second_app = create_app(
                repository=PostgresPhase2Repository(second_connection), temporary_storage=storage,
                task_runner=second_runner,
            )
            first_retry_app = create_app(repository=repository, temporary_storage=storage, task_runner=first_runner)
            async with (
                httpx.AsyncClient(transport=httpx.ASGITransport(app=first_retry_app), base_url="http://test") as first_client,
                httpx.AsyncClient(transport=httpx.ASGITransport(app=second_app), base_url="http://test") as second_client,
            ):
                headers = {"X-Client-Id": client_id, "Idempotency-Key": str(uuid4())}
                one, two = await asyncio.wait_for(
                    asyncio.gather(
                        first_client.post(f"/api/v1/candidates/{candidate_id}/extraction-jobs", headers=headers),
                        second_client.post(f"/api/v1/candidates/{candidate_id}/extraction-jobs", headers=headers),
                    ),
                    timeout=15,
                )
        finally:
            await second_connection.close()
    assert one.status_code == two.status_code == 201
    assert one.json()["id"] == two.json()["id"]
    assert first_runner.pending_count + second_runner.pending_count == 1
    async with repository._connection.cursor() as cursor:
        await cursor.execute("select count(*) as count from analysis_jobs where candidate_id=%s", (candidate_id,))
        assert await cursor.fetchone() == {"count": 2}
