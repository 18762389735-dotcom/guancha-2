"""Offline safety checks for the fixed A/B/C competition demonstration.

Never calls a provider and never prints values of environment variables.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg

from guancha_api.application.job_runner import FakeExtractionPayload
from guancha_api.domain.tieguanyin.demo_fallback import DemoFallbackCatalog, image_set_fingerprint
from guancha_api.domain.tieguanyin.fixture_catalog import FixtureCatalog
from guancha_api.domain.tieguanyin.question_value_config import load_question_value_config
from guancha_api.domain.tieguanyin.rules.rule_schema import load_approved_rules
from guancha_api.infrastructure.image_pipeline import sanitize_image_upload
from guancha_api.main import create_app


def report(level: str, text: str) -> None:
    print(f"{level} {text}")


def check_database(dsn: str | None) -> bool:
    if not dsn:
        report("FAIL", "database=not-configured")
        return False
    try:
        # This is intentionally read-only: preflight never applies migrations
        # or writes into the presentation database.
        with psycopg.connect(dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("select current_database(), current_user")
                cursor.fetchone()
                cursor.execute("""select count(*) from information_schema.tables
                                  where table_schema='public' and table_name in
                                  ('analysis_jobs','ai_call_logs','extraction_versions','evidence_items')""")
                if cursor.fetchone()[0] != 4:
                    raise RuntimeError("required migration tables are missing")
                cursor.execute("""select pg_get_constraintdef(oid) from pg_constraint
                                  where conrelid='analysis_jobs'::regclass
                                  and conname='analysis_jobs_processing_mode_check'""")
                row = cursor.fetchone()
                if row is None or "cache-fallback" not in row[0] or "live-ai" not in row[0]:
                    raise RuntimeError("phase8 processing-mode migration is not current")
        report("PASS", "database connection and phase8 migration")
        return True
    except Exception as exc:
        report("FAIL", f"database/migration: {type(exc).__name__}")
        return False


def check_offline_fixture_paths(catalog: FixtureCatalog) -> bool:
    try:
        fallback = DemoFallbackCatalog(catalog)
        images = catalog.demo_image_fixtures()
        if len(images) != 6 or len(catalog.demo_image_set_fixtures()) != 3:
            raise RuntimeError("expected exactly A/B/C two-image demo sets")
        # Validate all six real committed image files, then each real two-image
        # candidate set through the fixed fallback contract.
        for image in images:
            sanitized = sanitize_image_upload(
                data=(catalog.root.parent / image.path).read_bytes(), declared_content_type=image.mime_type
            )
            if sanitized.sanitized_sha256 != image.sha256:
                raise RuntimeError("sanitized hash mismatch")
        image_index = {item.fixture_id: item for item in images}
        for image_set in catalog.demo_image_set_fixtures():
            pairs = tuple((image_index[item_id].display_order, image_index[item_id].sha256) for item_id in image_set.image_fixture_ids)
            fixture = fallback.match(candidate_id=uuid4(), images=pairs)
            if fixture is None:
                raise RuntimeError("approved two-image fallback did not match")
            FakeExtractionPayload.model_validate({
                    "product_name": fixture.fields.get("tea_type"), "tea_category": "乌龙茶",
                    "tea_subtype": fixture.fields.get("tea_type"), "origin": fixture.fields.get("origin_text"),
                    "roast_or_style": fixture.fields.get("roast_level"), "aroma_claims": [], "taste_claims": [],
                    "year_or_batch": fixture.fields.get("year_or_batch"), "grade": None,
                    "weight": None, "price": None, "brew_claims": [], "risk_flags": [],
                    "evidence": [{"field_name": item["field_name"], "raw_text": item.get("raw_text") or item["field_name"],
                        "normalized_value": item.get("normalized_value") or item["field_name"], "model_confidence": 0.9,
                        "information_status": item["information_status"], "source_type": item["source_type"],
                        "verification_status": item["verification_status"], "source_location": item["source_location"],
                        "evidence_strength": item["evidence_strength"]} for item in fixture.evidence],
                })
        report("PASS", "A/B/C hashes, schemas, Fake-mode 3 candidates/6 real images, cache fallback simulation")
        return True
    except Exception as exc:
        report("FAIL", f"fixture/Fake simulation: {type(exc).__name__}")
        return False


def check_real_postgres_runner_exercise() -> bool:
    """Run the isolated six-image integration proof only against ``guancha_test``.

    The normal database check is deliberately read-only.  This optional test
    exercise recreates the public schema, so it can never target a deployment
    database and only runs when the explicit test DSN is present.
    """
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        report("WARN", "real PostgreSQL runner exercise requires TEST_DATABASE_URL=.../guancha_test")
        return True
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("select current_database()")
                database = cursor.fetchone()[0]
        if database != "guancha_test":
            report("WARN", "real PostgreSQL runner exercise refused outside guancha_test")
            return True
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "backend/tests/test_phase8_demo_postgres.py",
                "-k",
                "three_candidate_six_image_fallback_continues_to_decision_and_questions",
                "-q",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("six-image runner exercise failed")
        report("PASS", "real PostgreSQL ManualRunner: A/B/C six images -> extraction -> decision -> questions")
        return True
    except Exception as exc:
        report("FAIL", f"real PostgreSQL runner exercise: {type(exc).__name__}")
        return False


def main() -> int:
    failed = False
    try:
        app = create_app()
        assert app.openapi()["paths"]
        report("PASS", "application import and OpenAPI")
    except Exception as exc:
        report("FAIL", f"application import/OpenAPI: {type(exc).__name__}")
        failed = True
    try:
        catalog = FixtureCatalog()
        if not check_offline_fixture_paths(catalog):
            failed = True
    except Exception as exc:
        report("FAIL", f"fixture manifest: {type(exc).__name__}")
        failed = True
    try:
        assert load_approved_rules()
        assert load_question_value_config().field_relevance
        report("PASS", "decision rules and question configuration")
    except Exception as exc:
        report("FAIL", f"decision/question configuration: {type(exc).__name__}")
        failed = True
    if not check_database(os.getenv("GUANCHA_DATABASE_URL")):
        failed = True
    if not check_real_postgres_runner_exercise():
        failed = True
    report("PASS" if os.getenv("ADMIN_API_TOKEN") else "WARN", "admin token configured" if os.getenv("ADMIN_API_TOKEN") else "admin token not configured")
    report("PASS" if os.getenv("OPENAI_API_KEY") else "WARN", "real-provider configured" if os.getenv("OPENAI_API_KEY") else "real-provider=not-configured")
    try:
        status = subprocess.run(["git", "status", "--short"], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=False)
        report("PASS", "git worktree checked" if not status.stdout.strip() else "git worktree has local changes")
    except OSError:
        report("WARN", "git status unavailable")
    try:
        tracked = subprocess.run(["git", "ls-files", ".env", "*.pem", "*.key"], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=False)
        if tracked.stdout.strip():
            raise RuntimeError("secret-like files are tracked")
        report("PASS", "no obvious secret files tracked")
    except Exception as exc:
        report("FAIL", f"tracked-secret scan: {type(exc).__name__}")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
