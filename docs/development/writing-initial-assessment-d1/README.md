# Writing Initial Assessment D1

## Scope and original content

This release collects one original IELTS Academic Task 2-style response. It does not cover the full IELTS Writing test and does not provide a calibrated writing proficiency measurement.

- `task_id`: `writing-initial-community-land-use`
- `task_version`: `writing-initial-community-land-use-v1`
- `prompt_id`: `community-land-use-choice`
- `prompt_version`: `community-land-use-choice-v1`
- Prompt: “Some towns have unused land that could become either a community garden or an outdoor sports area. Which use would benefit local residents more? Give reasons for your choice and consider the alternative.”
- Instructions: Write one response in English. Support your view with reasons and examples. A draft may be saved before submitting the final response.
- Suggested length: 250 words. Maximum: 20,000 Unicode characters.
- Source: original VerbaTrail prompt; no IELTS test material used.
- Scope: one Task 2-style response collection task, not complete IELTS Writing coverage.

Suggested length and completion are task facts only. The server does not require 250 words and does not infer ability from length, paragraphs, completion, or edits.

## API contract

All routes are under `/api/v1/assessment-runs/{run_id}/writing`. The `learner_id` is a development scope identifier, consistent with the rest of the Assessment API; it is not authentication.

| Method and path | Behavior |
|---|---|
| `GET /` | Returns run and task status, pinned versions and prompt, draft/version, immutable submission (including real body and digest), evidence reference, and limitations. |
| `POST /start` | Pins the task version and creates one empty draft for this AssessmentTask. Repeated calls return the same state. Only `initial-v2` runs with an unstarted Writing task can pin this release. |
| `PUT /draft` | JSON: `{ learner_id, body, version }`. Uses compare-and-swap on `version`; stale writes return 409. Drafts remain editable until formal submission. |
| `POST /submit` | JSON: `{ learner_id, body, version, task_version, prompt_id, prompt_version, idempotency_key }`. Saves the body and immutable evidence, then completes Writing atomically. |

An identical submission retry with the same key, body, draft version, task version, and prompt identifiers returns the original evidence. A different body, version, prompt, or key after first submission returns 409. Empty text returns 422 and does not complete the task. A submit may include text newer than the last saved draft if it presents the current draft version; the exact final body is stored both as the latest draft text and as the immutable submission. Afterward the draft is read-only.

Unpinned `initial-v1` runs and unknown/unrecoverable Writing content versions return 409. Writing updates only its own `AssessmentTask`; it does not close the run or alter Reading, Listening, or Speaking.

## Data ownership and persistence

`assessment_writing_drafts` is keyed by `assessment_task_id`, which belongs to one run's unique Writing task. `assessment_writing_submissions` has one row per task, a unique idempotency key per task, the exact UTF-8 body, task/prompt versions, submitted draft version, SHA-256, word and paragraph counts, timestamp, and request key. `AssessmentTask.evidence_source` and `evidence_reference` point to the submission ID. Database constraints enforce one submission per task; ORM event guards reject submission updates and deletes.

No `LearningSession`, ordinary `WritingDraft`, or `WritingRevision` is reused. The ordinary `/writing/sessions` and revision API therefore cannot address assessment drafts or submissions, and ordinary Writing history remains separate.

SQLite writes begin with `BEGIN IMMEDIATE`, serializing same-database updates before reading current draft/task state. The single transaction updates the draft, inserts the immutable submission, and marks the Writing task complete with its evidence reference. Optimistic draft versions prevent stale overwrites.

Migration `0013_writing_assessment` follows `0012_speaking_assessment`; it adds only the two assessment-specific tables and an index/constraints. Upgrade and ORM metadata were verified using disposable SQLite databases. No formal database was used for migration or HTTP acceptance.

## Evidence and Learner Model boundary

The submission is recorded as `evidence_type: assessment_activity`, `evidence_source: assessment_writing_submission`, with the immutable reference and task content version. `evaluation_status` and `semantic_quality` are `not_assessed`; `ability_evidence_valid` is `false`. Deterministic observations are the submitted body, SHA-256, word count, paragraph count, version, and timestamp.

The Learner Model includes a Writing `assessment_activity` record, reports `assessment_status: not_assessed`, `evidence_validity: not_evaluated`, and does not add a positive capability observation or an assessment evidence level. No AI or paid model is called. Task Response, Coherence and Cohesion, Lexical Resource, Grammatical Range and Accuracy, and IELTS Writing Band remain unassessed.

## Run lifecycle note

Each newly created AssessmentRun contains exactly Reading, Listening, Speaking, and Writing tasks. Existing code keeps each task state independent, but does not derive a unified terminal AssessmentRun state when all four are complete. This is a lifecycle gap for a later shared implementation; Writing does not add last-task-order-specific close-out logic.

## Reproduction and HTTP acceptance

From the repository root, run:

```powershell
.\.venv\Scripts\python.exe backend\scripts\verify_writing_assessment_http.py
```

The script creates a disposable SQLite file, migrates it, starts real Uvicorn, and makes real HTTP requests. For the simulated lost response it sends the submit request, closes the connection without reading the response, confirms the transaction reached SQLite, restarts Uvicorn, and recovers the same text via GET and idempotent retry.

### D1 acceptance record

- Real Uvicorn / HTTP: PASS.
- Lost response, process restart, same-body recovery and idempotent replay: PASS.
- Empty invalid submission leaves no evidence: PASS.
- Body, base version, task version conflicts: PASS.
- Same learner / different run separation and different learner 404: PASS.
- Ordinary Writing session remains separate: PASS.
- Reading, Listening and Speaking task states remain independent: PASS.
- Learner Model keeps Writing unassessed with no band or positive capability proof: PASS.
- Isolated database at Alembic head `0013_writing_assessment`: two runs, eight AssessmentTasks, two assessment drafts, one Writing submission, one ordinary Writing draft, zero ordinary Writing revisions.

The frontend uses Node.js's built-in test runner (not an `npm test` script): `node --test` in `frontend` discovered 58 tests and all 58 passed. Detailed suite totals and the formal database hash are recorded in `TEST_REPORT.md`.
