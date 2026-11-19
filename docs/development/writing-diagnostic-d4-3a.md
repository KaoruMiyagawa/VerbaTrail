# Writing D4.3A practice and revision

The Writing results page shows a micro exercise after the diagnostic session stops. The selector reads the latest completed decision, the original question's hypothesis targets and the D4.1 evidence. It prefers an open hypothesis with an exact essay excerpt, then one targeted by the diagnostic question. When neither supplies a focus, it starts with a position rewrite anchored to the essay. The three templates exercise position, reason development and reasoning links. A template and its stored rationale specify what the new response can clarify. All three produce exploratory practice.

| Endpoint under `/api/v1/assessment-runs/{run_id}/writing/training` | Behavior |
|---|---|
| `GET /` | Returns the existing exercise, versioned draft and final revision if saved. Returns JSON null before creation. |
| `POST /start` | Pins one exercise for the completed diagnostic session and restores that exercise on replay. |
| `PUT /draft` | Saves a body with its expected draft version. A stale write with different text returns 409. |
| `POST /submit` | Saves exact revision text, the expected draft version and an idempotency key. The same request restores the original result. Changed text, version or key conflicts after finalization. |

All operations follow the assessment's learner scope. Writes reuse the assessment task lock. Migration `0015_writing_practice` follows `0014_writing_diagnostic` and adds an exercise table and an immutable final revision table. The plan pins the source decision, prompt, evidence and rule versions. Previously saved D1 submissions and D4.2 diagnostic records remain separate.

The D4.2 client uses the server's reservation expiry state after reading the session. An expired reservation can be replayed as a local decision without another model call. A model's essay citation must match stored characters, and a clarification cannot by itself support a status change about the original writing.

The revision result compares the two texts and reports counts and equality. The optional structured feedback contract accepts specific changes, hypothesis links, uncertainties and a next-step suggestion. Each comparison must cite exact original and revision excerpts and known hypothesis IDs. Semantic interpretations remain candidates. This release uses local feedback and has no training-related model call, automatic assignment or Learner Model update.
