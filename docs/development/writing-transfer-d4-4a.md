# Writing D4.4A independent transfer

Writing reuses the Reading transfer pattern of server assignment, stable material and exposure tracking. Writing records source-based observations for later review. It uses no Reading correctness or promotion rules.

The catalogue provides two original materials for each target (position, reasons and reasoning link). The selected target comes from the completed Writing practice plan. The server verifies the final revision and its diagnostic and original-submission lineage. It pins the catalogue task, content version and material ID when the learner starts transfer. A subsequent catalogue change does not change an existing assignment. The transfer interface mounts separately from the original essay, diagnostic and revision. Its API returns the transfer material and transfer text. The result adds lineage IDs.

| Method | Path under `/api/v1/assessment-runs/{run_id}/writing` | Behavior |
|---|---|---|
| GET | `/transfer` | Read the assignment, draft or formal response. Returns JSON null before assignment. |
| POST | `/transfer/start` | Assign once after a formal practice revision. Replays return the pinned assignment. |
| PUT | `/transfer/draft` | Save `body` with expected `version`. Conflicting edits return 409. |
| POST | `/transfer/submit` | Save exact `body`, expected `version` and `idempotency_key`. Identical retries return the original response. |

Exposure begins at assignment, even when the response is unfinished. The server prefers a material ID with no previous assignment for that learner. A database index allows only one recorded first exposure per learner and material. If compatible materials are all previously assigned, the repeat is disclosed and recorded as `repeated_exposure`. This only tracks assignments in VerbaTrail. The separate editor reduces cueing in the UI. It cannot establish independent work or detect prior exposure outside VerbaTrail.

Migration `0016_writing_transfer` follows `0015_writing_practice`. It adds one assignment per final practice revision and one immutable response per assignment. The revision foreign key traces back to its unique practice, same-submission diagnostic and original submission. This chain provides one assignment per original submission. Responses store text, SHA-256, UTC submission time, the draft version and versioned observations. Submission and draft changes use the existing Writing task lock. The browser keeps the transfer view and unsynced request in the current tab for refresh recovery.

Downgrades from `0016` to `0013` delete the Writing transfer, practice and diagnostic tables in reverse dependency order. They lose all drafts, answers, decisions and exposure history in those tables. D1 submissions survive. Existing shared model audit rows also survive, but their Writing decision links are lost. These are source definitions for migrations under development. If a database already has an older version of one of these revision IDs, calling upgrade alone will not apply the revised definition.

The observation reports completion, counts, target and exposure. It always reports `not_assessed`, insufficient evaluation and no ability conclusion. `comparison_context()` is internal. It exposes the three digest-checked source records to future review code only after a formal transfer response. This release has no model calls and does not update learner mastery.
