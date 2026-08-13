# GUANCHA FINAL RUNTIME VALIDATION

## Executive Summary

Phase 17A closed the previously missing isolated PostgreSQL gate. The current backend suite, all 30 AI-eval cases, and the FakeProvider database chain passed against `guancha_test`; the independent database red team found no P0/P1 database-integrity defect.

Browser runtime is now available and one real local end-to-end path also passed. It covered screenshot upload, extraction, Decision V1, generated questions, three independently bound merchant replies, aggregate rejudge, Decision V2/Delta, selection and local tea-stock entry. The broader browser scenario matrix has not yet been completed, so this is not a deployment approval.

## Product Baseline

- Current product-code baseline: `cabc959` (`fix: close final client risk boundaries`).
- Starting documentation/SSOT baseline: `3b5b49a`.
- Validation branch: `codex/final-db-validation`.
- Phase 17A test-contract changes: two failing database assertions were corrected after their first real execution; no production code, schema, dependency or Provider behavior was changed.

## Database Environment

- Database: `guancha_test`.
- Connection: `postgresql://postgres:***@127.0.0.1:5432/guancha_test`.
- Credentials: `NOT RECORDED`.
- `POSTGRES_PASSWORD=PRESENT` was used only in child-process environment variables and cleared afterwards.
- Current user: `postgres`; server: PostgreSQL 16.14.

## Safety Gate

Before every destructive fixture/test command, the operator queried `SELECT current_database()` and proceeded only after the exact result `guancha_test`. No other database was mutated, dropped, truncated or reset.

## Schema Initialization

The existing pytest repository fixture was used. It rebuilds the isolated `public` schema from the project migrations rather than inventing a parallel schema. A post-connection metadata check found the expected 16 project tables; no application data was dumped into this report.

## Previously Skipped DB Tests

The prior DB gate was an absent `TEST_DATABASE_URL`. With the isolated runtime variable configured, no backend test was skipped for that reason.

Two initial failures were stable test-contract defects, recorded in [DB_FAILURES.md](DB_FAILURES.md):

1. A startup-recovery assertion ran after runner shutdown, even though the released runner contract clears pending identities at shutdown.
2. A MerchantReply conflict test expected immediate parsing, while the current contract parses saved replies during aggregate rejudge.

Both were repaired only in their tests, then re-run under PostgreSQL. No product direction changed.

## Backend Full Result

`304 passed / 0 failed / 0 skipped` with `TEST_DATABASE_URL` set to the isolated database.

## AI Eval

The deterministic harness was run with the database enabled and FakeProvider configuration:

- Total: 30
- PASS: 30
- FAIL: 0
- BLOCKED: 0

This is a fixed test/fixture evaluation, not a claim about live visual-model accuracy. Real Provider calls: `0`.

## Fake-provider Full Database Chain

Three real PostgreSQL acceptance tests passed for the required lineage:

`Need → 3 candidates (including 2/2/1 image layout) → extraction → Evidence → Decision V1 → Questions → all MerchantReplies → aggregate rejudge → Decision V2 → DecisionDelta`.

The runtime browser smoke independently exercised a one-candidate version of the same path, including actual local upload and selection into the local tea stock.

## Decision Version Integrity

V1 remains an append-only historical version; V2 is separately created by aggregate rejudge. The Delta references the correct old/new version pair. PostgreSQL integration and red-team suites passed this check.

## MerchantReply Integrity

The browser path submitted three replies one question at a time and did not fan out one reply to multiple questions. The persisted/rejudge tests enforce question, decision-version and session ownership.

## Evidence Integrity

Product claims, merchant claims and inferred evidence retain separate source/status semantics. A known-opposite explicit product claim produces a conflict record; merchant evidence does not overwrite the product claim. PostgreSQL conflict tests passed.

## Candidate Identity

Three-candidate database lineage and candidate replay tests passed using stable `candidate_id` references. The browser runtime also completed the selection using its current candidate identity.

## Replay / Idempotency

Same-key session/candidate/image/retry replay and concurrent replay tests passed. Server-authoritative creation/emission paths were exercised with the PostgreSQL repository; GET recovery endpoints did not add transitions in the red-team suite.

## Transaction Integrity

Extraction completion and MerchantReply parsing failure tests passed their rollback assertions. No half-written decision, orphan reply or orphan Delta was observed in the exercised transaction paths.

## Database Red Team

See [DB_RED_TEAM.md](DB_RED_TEAM.md). Verdict: `PASS_WITH_BOUNDARIES`. It found no P0/P1 product or database-integrity defect. The principal boundary is that the pytest fixture relies on the operator's explicit database-name gate rather than a universal in-code database-name assertion.

## Browser Availability

PASS. Codex In-app Browser reached the locally started app at `http://127.0.0.1:8001/` using `guancha_test` and FakeProvider. The temporary server and all validation tabs were stopped/closed after the run.

## Browser E2E

PASS for the exercised smoke path:

`Home → Candidate image upload → Analysis → Decision V1 → Questions → three merchant replies → aggregate rejudge → Decision V2/Delta → selection → Tea Stock`.

The product remained interactive after each reply and rejudge. Responsive checks at `390×844`, `430×932` and `1280×900` found no horizontal overflow; the final selection CTA was visible and enabled at each viewport.

Not yet exercised in a live browser session: normal/skip onboarding pair, two-candidate reorder, vague/conflicting replies, ranking-changed branch, refresh/cold-reopen recovery, and telemetry-sink failure. Existing automated tests cover portions of those contracts, but they are not a substitute for this remaining browser matrix.

## Regression Tests

- Frontend: `61/61 PASS`.
- Backend with isolated PostgreSQL: `304/304 PASS`.
- AI Eval with database: `30 PASS / 0 FAIL / 0 BLOCKED`.
- Node syntax: PASS for tracked JavaScript.
- Python AST parse: PASS.
- `git diff --check`: PASS.

## Secret Scan

Tracked files were checked for the supplied database password and password-bearing PostgreSQL URI patterns. No match was found. No credential, API key or full database URI was added to tracked files or this report.

## Code Changes

- `backend/tests/test_phase2_image_job_api.py`: align startup-recovery assertion timing with released ManualTaskRunner shutdown semantics.
- `backend/tests/test_phase6_merchant_rejudgement.py`: align conflict test with aggregate-rejudge parsing lifecycle.
- Validation documentation and the minimal SSOT verification-status update.

## Remaining Blockers

1. Complete the unexercised browser scenario matrix before calling browser validation comprehensive.
2. Real Provider extraction quality remains unverified by design; no MiMo/OpenAI call was made.
3. Existing demo seed / post-purchase presentation decisions remain a documented product-scope issue, not a database-integrity finding.
4. A fixture-level wrong-database assertion would reduce reliance on the manual safety gate.

## Deployment Recommendation

Do not deploy from this validation branch yet. The database gate is closed, but complete browser scenario coverage and an explicit deployment review remain necessary.

## Final Verdict

`DATABASE_GATE_CLOSED_BROWSER_FINAL_CHECK_REMAINING`
