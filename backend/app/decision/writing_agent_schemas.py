"""Writing Agent proposals are separate from exact evidence and saved decisions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.decision.writing_schemas import (
    NonBlank,
    WritingEvidence,
    WritingEvidenceLink,
    WritingFollowupSuggestion,
    WritingHypothesisProposal,
    WritingSubmissionSource,
)

MAX_ANSWER_CHARACTERS = 4000
WritingAspect = Literal["position", "reasons", "reasoning_link"]
FallbackReason = Literal[
    "remote_disabled", "model_not_requested", "model_unavailable", "budget_exhausted",
    "model_error", "invalid_model_output", "interrupted",
]


class WritingArgumentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aspect: WritingAspect
    interpretation: NonBlank
    evidence_ids: list[NonBlank] = Field(max_length=8)
    assessment: Literal["candidate", "insufficient_evidence"] = "candidate"
    excerpt: str | None = Field(default=None, max_length=600)


class WritingAgentEvidenceLink(WritingEvidenceLink):
    excerpt: str | None = Field(default=None, max_length=600)


class WritingAgentHypothesis(WritingHypothesisProposal):
    status: Literal["candidate", "supported", "lowered", "ruled_out", "insufficient_evidence"]
    focus_aspect: WritingAspect | None = None
    evidence_links: list[WritingAgentEvidenceLink] = Field(default_factory=list)


class WritingAgentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argument_analysis: list[WritingArgumentObservation] = Field(max_length=3)
    hypotheses: list[WritingAgentHypothesis] = Field(min_length=2, max_length=5)
    followup: WritingFollowupSuggestion | None
    decision_reason: NonBlank
    unresolved_uncertainties: list[NonBlank] = Field(min_length=1, max_length=5)


class WritingTrainingSuggestion(BaseModel):
    hypothesis_ids: list[str]
    suggestion: str


class WritingAgentOutcome(WritingAgentProposal):
    method: Literal["structured_model", "deterministic_rules"]
    method_version: str
    fallback_reason: FallbackReason | None = None
    uncertainty_level: Literal["insufficient_evidence"] = "insufficient_evidence"
    ability_evidence_valid: Literal[False] = False
    training_suggestion: WritingTrainingSuggestion | None = None


class WritingAgentAnswer(BaseModel):
    evidence_id: str
    question_id: str
    body: str
    body_sha256: str
    source: Literal["learner_followup_response"] = "learner_followup_response"


class WritingAgentModelSource(BaseModel):
    provider_name: str
    model_name: str
    prompt_version: str


class WritingAgentModelAudit(WritingAgentModelSource):
    id: str
    status: str
    error_code: str | None
    reported_tokens: int
    attempts: int


class WritingAgentDecisionView(BaseModel):
    id: str
    phase: Literal["initial", "followup"]
    state: Literal["processing", "completed"]
    allow_model: bool
    idempotency_key: str
    recover_after: datetime
    recoverable: bool = False
    completed_at: datetime | None
    answer: WritingAgentAnswer | None
    outcome: WritingAgentOutcome | None
    planned_model: WritingAgentModelSource | None
    model_call: WritingAgentModelAudit | None


class WritingAgentSessionView(BaseModel):
    source: WritingSubmissionSource
    evidence_version: str
    evidence: list[WritingEvidence]
    state: Literal["not_started", "processing", "awaiting_answer", "stopped"]
    model_available: bool
    token_budget: int
    tokens_charged: int
    tokens_reported: int
    tokens_pending_reservation: int
    followup_limit: int
    max_answer_characters: int = MAX_ANSWER_CHARACTERS
    initial: WritingAgentDecisionView | None
    followup: WritingAgentDecisionView | None
    verified_conclusion: Literal[None] = None
