# AI Eval Results

Run at: 2026-08-13T08:59:34.133151+00:00

This is the fixed deterministic/fixture test-set result, not real-world model accuracy.
No Provider network call or API key access is performed.

## Totals

- Total: 30
- PASS: 30
- FAIL: 0
- BLOCKED: 0

## Cases

| Case | Level | Category | Failure taxonomy | Result |
|---|---|---|---|---|
| EXT-01 | fixture_pipeline | Extraction Safety | EXTRACTION_MISS | PASS |
| EXT-02 | fixture_pipeline | Extraction Safety | EXTRACTION_HALLUCINATION | PASS |
| EXT-03 | fixture_pipeline | Extraction Safety | EXTRACTION_MISS | PASS |
| EXT-04 | fixture_pipeline | Extraction Safety | EVIDENCE_SOURCE_ERROR | PASS |
| EVD-01 | deterministic_unit | Evidence Safety | EVIDENCE_SOURCE_ERROR | PASS |
| EVD-02 | deterministic_unit | Evidence Safety | EVIDENCE_SOURCE_ERROR | PASS |
| EVD-03 | deterministic_unit | Evidence Safety | MARKETING_CLAIM_LEAK | PASS |
| EVD-04 | fixture_pipeline | Evidence Safety | EXTRACTION_HALLUCINATION | PASS |
| SEN-01 | deterministic_unit | Sensory Translation | SENSORY_OVERCLAIM | PASS |
| SEN-02 | deterministic_unit | Sensory Translation | SENSORY_OVERCLAIM | PASS |
| SEN-03 | deterministic_unit | Sensory Translation | SENSORY_MISSING | PASS |
| NEED-01 | deterministic_unit | Current Need | NEED_PRIORITY_ERROR | PASS |
| NEED-02 | deterministic_unit | Current Need | NEED_PRIORITY_ERROR | PASS |
| NEED-03 | deterministic_unit | Current Need | NEED_PRIORITY_ERROR | PASS |
| NEED-04 | deterministic_unit | Current Need | BUDGET_PARSE_ERROR | PASS |
| QST-01 | deterministic_unit | Question | QUESTION_LOW_VALUE | PASS |
| QST-02 | deterministic_unit | Question | QUESTION_DUPLICATE | PASS |
| QST-03 | deterministic_unit | Question | QUESTION_LOW_VALUE | PASS |
| MRP-01 | deterministic_unit | Merchant Reply | MERCHANT_REPLY_PARSE_ERROR | PASS |
| MRP-02 | deterministic_unit | Merchant Reply | MERCHANT_REPLY_PARSE_ERROR | PASS |
| MRP-03 | deterministic_unit | Merchant Reply | MERCHANT_CONFLICT_FALSE_POSITIVE | PASS |
| MRP-04 | fixture_pipeline | Merchant Reply | MERCHANT_REPLY_PARSE_ERROR | PASS |
| REJ-01 | deterministic_unit | Rejudge and Delta | REJUDGE_INCONSISTENT | PASS |
| REJ-02 | database_integration | Rejudge and Delta | REJUDGE_INCONSISTENT | PASS |
| REJ-03 | database_integration | Rejudge and Delta | REJUDGE_INCONSISTENT | PASS |
| ANS-01 | deterministic_unit | Decision Answer | DECISION_ANSWER_MISMATCH | PASS |
| STATE-01 | database_integration | State Safety | DECISION_STATE_STALE | PASS |
| REPLAY-EDGE-01 | deterministic_unit | Replay Created Edge | STATE_RECOVERY_ERROR | PASS |
| STATE-03 | deterministic_unit | State Safety | STATE_RECOVERY_ERROR | PASS |
| REPLAY-DB-01 | database_integration | Replay Exactly Once | DATABASE_ERROR | PASS |

## By category

- Current Need: PASS 4 / FAIL 0 / BLOCKED 0
- Decision Answer: PASS 1 / FAIL 0 / BLOCKED 0
- Evidence Safety: PASS 4 / FAIL 0 / BLOCKED 0
- Extraction Safety: PASS 4 / FAIL 0 / BLOCKED 0
- Merchant Reply: PASS 4 / FAIL 0 / BLOCKED 0
- Question: PASS 3 / FAIL 0 / BLOCKED 0
- Rejudge and Delta: PASS 3 / FAIL 0 / BLOCKED 0
- Replay Created Edge: PASS 1 / FAIL 0 / BLOCKED 0
- Replay Exactly Once: PASS 1 / FAIL 0 / BLOCKED 0
- Sensory Translation: PASS 3 / FAIL 0 / BLOCKED 0
- State Safety: PASS 2 / FAIL 0 / BLOCKED 0

## Boundary

- BLOCKED means an executable case could not run in this environment; it is never counted as PASS.
- `fixture_pipeline` starts from fixed structured Extraction fixtures and does not evaluate the live vision Provider.
- Failure taxonomy is the classification assigned if the case fails; PASS does not mean that a failure occurred.
