# D1 Tests and HTTP Acceptance

## Test commands

| Check | Result |
|---|---|
| Backend complete suite: `.venv\Scripts\python.exe -m pytest backend/tests -q` | PASS — 197 tests. |
| Writing assessment unit/API/migration cases | 5 tests pass. |
| Frontend `npm run typecheck` | PASS. |
| Frontend `npm run build` | PASS; Vite production bundle built. |
| Frontend Node tests: `node --test` from `frontend` (Node v26.12.1) | PASS — 58 tests, 58 passed, 0 failed, 0 skipped. |
| Alembic heads | `0013_writing_assessment (head)`; one head. |
| `git diff --check` | PASS; only existing line-ending notices were printed. |

Migration `0012_speaking_assessment` → `0013_writing_assessment` was applied to temporary SQLite. The two new tables' columns, indexes, unique constraints and metadata comparison match the ORM. A repository-wide Alembic autogenerate check also reports three pre-existing `ondelete=RESTRICT` foreign-key differences on `attempts.group_version_id`, `learning_sessions.current_group_version_id`, and `questions.reading_group_version_id`; these are outside this migration and no changes were made to them.

## Real Uvicorn and HTTP

Reproduction: `backend/scripts/verify_writing_assessment_http.py`.

The script runs a real Uvicorn process against an isolated migrated SQLite database, sends requests over HTTP, disconnects after sending a formal submit request without reading the response, confirms that the request committed, stops and restarts Uvicorn, then restores the same body and evidence by GET and idempotent retry.

Verified outcomes:

- V2 run creation; four tasks exist.
- Writing task start pins `writing-initial-community-land-use-v1`; repeated start is stable.
- Empty submit returns 422 and leaves the task uncompleted with no submission.
- Draft stale-version update returns 409.
- Original body persists with SHA-256 and deterministic word/paragraph counts.
- Same request and key restore the original submission; body, draft version, task version, and changed-key conflicts return 409.
- The Writing AssessmentTask links to the submission and completes atomically.
- Lost-response recovery works after server restart.
- Same learner's separate runs have independent drafts; another learner receives 404.
- Ordinary Writing session creates a normal draft separately, with no assessment revisions mixed in.
- Reading, Listening and Speaking task state stays `not_started` when Writing is started/completed.
- Learner Model leaves Writing at `not_assessed`, uses `assessment_activity`, and does not produce a Band or positive capability evidence.

Observed isolated database counts after the HTTP scenario:

| Table | Rows |
|---|---:|
| `assessment_runs` | 2 |
| `assessment_tasks` | 8 |
| `assessment_writing_drafts` | 2 |
| `assessment_writing_submissions` | 1 |
| ordinary `writing_drafts` | 1 |
| ordinary `writing_revisions` | 0 |

## Formal database protection

The formal `backend/unlearnloop.db` SHA-256 before and after work:

`BC4656AA2BC624F0A257254BA25597DB3031126FDFCBE79E5F3BB0CB607793E0`

It matches the supplied expected digest. Migrations, unit/API tests, and HTTP acceptance used temporary SQLite databases only.

## Final run

Backend suite completed with all 197 tests passing. The Writing suite is 5/5. No tests or frontend code were changed outside the assessment API/schema and the two existing backend expectations that track Alembic head and public OpenAPI inventory.
