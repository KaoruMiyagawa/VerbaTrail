# Writing D4.2 diagnostic interaction

These endpoints extend the existing run-scoped Writing diagnostic route. They use D1's learner scope and require a completed, digest-verified submission.

| Endpoint under `/writing/diagnostic` | Request | Result |
|---|---|---|
| `GET /session` | Learner scope in the query | Current state, essay evidence, initial decision, answer and review when saved |
| `POST /start` | `idempotency_key` and optional `allow_model` | Initial decision with an optional question |
| `POST /answer` | `idempotency_key`, initial decision ID as `question_id`, answer `body` and optional `allow_model` | Saves the answer verbatim, reviews the original hypotheses and stops |

The server derives essay citations using D4.1. The model can propose links only to supplied evidence IDs. Supporting or contradicting links require an exact excerpt within the cited source. The essay analysis itself can cite only essay paragraphs. A later clarification gets a separate immutable reference and hash. Hypothesis updates retain their original IDs and statements. A change to a supported, lowered or ruled-out status requires an essay excerpt with the corresponding relation. The excerpt check establishes provenance. The relation remains an unverified interpretation.

The server setting `ALLOW_REMOTE_MODEL_CALLS` and the request's `allow_model` must both be enabled for model use. The default path uses the existing deterministic prompt and preserves uncertainty after the answer. A configured provider uses the existing structured generation and error contract. Invalid responses, provider errors and insufficient budget switch to the local path with a recorded reason. Each phase permits one provider attempt. The service reserves estimated input tokens plus the completion allowance and charges reported use when available. This estimate is independent of provider billing. The diagnostic allows at most one follow-up and uses the existing configured limit if it is zero.

Migration `0014_writing_diagnostic` adds one table linked to the formal submission. A follow-up row requires the same submission's initial decision. The rows retain the request key, original answer, pinned evidence version, model reservation and outcome. The reservation is committed before the provider call. Recovery requests carry the existing decision ID and can only settle that reservation. If the worker returns after local recovery, its `ModelCall` audit is attached to the decision without replacing the local outcome. If the worker crashes after sending a request, the planned provider remains recorded and the actual provider outcome can be unknown.

The session reports provider-reported tokens, outstanding reservations and the amount currently charged against the application budget. A pending call, interrupted call, or response with no usable usage report keeps its estimated reservation in the budget charge. The estimate is not a provider billing total or a hard bound on actual prompt tokens. All model interpretations with substantive claims in the current prompt version require an essay excerpt. Quotation checks establish provenance and do not verify what the excerpt means.

The result has an optional training suggestion contract and leaves it empty in this release. No evaluation changes the Learner Model or schedules training. The Writing page keeps the deterministic observations and saved Agent decisions in separate sections.
