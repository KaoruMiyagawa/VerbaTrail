# Writing D4.1 diagnostic evidence

`GET /api/v1/assessment-runs/{run_id}/writing/diagnostic?learner_id=…` returns a read-only diagnostic snapshot. The endpoint uses the existing Writing assessment learner scope. It returns 404 for a run outside that scope and 409 when a formal submission is missing or its saved state cannot be verified. The scope identifier has the same development-only role as in D1.

The server follows the task's evidence reference to the immutable `AssessmentWritingSubmission` and checks the pinned content identifiers and SHA-256 of the stored body. The request has no input fields for body, quotes or evidence IDs. The endpoint derives each snapshot on read, so existing valid D1 submissions work without a migration or backfill.

## Exact references

`writing-paragraph-spans-v1` splits the stored text on blank lines using the D1 paragraph rule, skips whitespace-only segments, and excludes leading and trailing whitespace from each quote's range. Each `source_reference` includes the submission ID, body SHA-256, task and prompt versions, and the exact quoted text. `start` is zero-based and inclusive. `end` is exclusive. Both count UTF-16 code units, so JavaScript consumers can check `storedBody.slice(start, end) === quote`. Supplementary characters count as two units. Combining marks remain separate code units, and there is no Unicode normalization or newline conversion during extraction. Each evidence ID includes the submission ID, extraction version and paragraph number.

## Diagnostic boundary

The result separates quote validity (`exact_quote`), response performance (`not_assessed`) and ability inference (`not_supported`). Evidence links use `supports`, `contradicts` or `neutral`. The current deterministic method attaches only neutral context and retains three candidate explanations around the writer's position and reasoning. It reports `insufficient_evidence` and suggests a question that can distinguish the explanations. Suggested follow-ups are returned for review and are not dispatched.

`build_writing_diagnostic` takes a verified submission and an optional `WritingDiagnosticProposer`. A future adapter can call the existing `ModelProvider.generate_structured` with `WritingDiagnosticProposal` as its response schema, using the pinned prompt and server-derived evidence in `WritingDiagnosticContext`. The proposal can refer to evidence by ID only. The service validates references and assigns candidate status and the evidence sufficiency boundary. There is no numeric confidence, Band result, mastery update, model call or diagnosis-case write in D4.1.
