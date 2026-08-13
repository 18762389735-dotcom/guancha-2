# Phase 17A Database Red Team

## Verdict

`PASS_WITH_BOUNDARIES`

## Scope and safety

- Connection target was verified before each mutating pytest invocation with `SELECT current_database()`; the result was exactly `guancha_test`.
- Connected database user: `postgres`.
- Credentials: `NOT RECORDED` (`POSTGRES_PASSWORD=PRESENT` only).
- The isolated public schema contained 16 project tables. No other database was mutated.
- `backend/scripts/demo_preflight.py` also rejects its real PostgreSQL exercise unless the database name is `guancha_test`.

Boundary: pytest fixtures do not yet have a universal in-code database-name assertion. This validation therefore relied on the explicit operator safety gate before every destructive test command.

## Attacks exercised on real PostgreSQL

| Attack surface | Result | Evidence |
|---|---|---|
| Wrong-database safety | PASS_WITH_BOUNDARY | Repeated operator gate; preflight rejects non-test name. |
| Same-key replay / exactly once | PASS | Session/candidate replay and concurrent replay, plus image/retry concurrency tests. |
| V1 overwritten by V2 | PASS | Append-only rejudge test preserves V1, creates V2, and binds Delta to both versions. |
| Reply bound to wrong question | PASS | Repository constraints and merchant/rejudge PostgreSQL tests enforce session, version and question ownership. |
| Candidate identity | PASS | Three-candidate lineage and replay tests use stable candidate IDs. |
| Stale Need | PASS | Need change makes current decision stale and new analysis creates a fresh snapshot. |
| Partial transaction | PASS | Extraction and merchant parsing failure paths roll back without partial rows. |
| Conflict persistence | PASS | Explicit known-opposite product evidence yields a distinct conflict rather than overwriting product evidence. |
| Selection mismatch | PASS_WITH_SCOPE_BOUNDARY | Decision/question/reply/rejudge lineage is verified. Final user selection and tea stock remain client-local, not a PostgreSQL-authoritative selection record. |

## Executed red-team suites

- 12 passing tests: same-key session/candidate replay, concurrent replay, Need invalidation, Questions and three-candidate lineage.
- 49 passing tests: MerchantReply and aggregate rejudge, V1→V2, conflicts, parser rollback, repository transactions, job claim-once and image/retry concurrency.

## Remaining boundaries

1. Do not extend this result to cross-device tea-stock persistence or real Provider quality.
2. A future fixture-level `current_database() == 'guancha_test'` assertion would turn this operational safety rule into an in-code guard.
