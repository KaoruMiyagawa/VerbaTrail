"""Contracts for Writing diagnostic evidence and replaceable hypothesis proposals."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.enums import EvidenceRelation, HypothesisStatus, UncertaintyLevel

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WritingSubmissionSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    submission_id: str
    body_sha256: str
    task_version: str
    prompt_id: str
    prompt_version: str
    source: Literal["assessment_writing_submission"] = "assessment_writing_submission"


class WritingSourceReference(WritingSubmissionSource):
    coordinate_system: Literal["utf16_code_units"] = "utf16_code_units"
    start: int = Field(ge=0, description="Zero-based inclusive offset in the stored body.")
    end: int = Field(gt=0, description="Exclusive offset in UTF-16 code units.")
    quote: str


class WritingEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal["paragraph_segment"] = "paragraph_segment"
    paragraph_number: int = Field(ge=1)
    source_reference: WritingSourceReference
    source_validity: Literal["exact_quote"] = "exact_quote"


class WritingEvidenceLink(BaseModel):
    # Proposers refer to server evidence IDs. They cannot supply replacement quotes or offsets.
    model_config = ConfigDict(extra="forbid")

    evidence_id: NonBlank
    relation: EvidenceRelation
    reason: NonBlank


class WritingHypothesisProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonBlank
    statement: NonBlank
    unresolved_question: NonBlank
    evidence_links: list[WritingEvidenceLink] = Field(default_factory=list)


class WritingFollowupSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: NonBlank
    hypothesis_ids: list[NonBlank] = Field(min_length=2)
    evidence_ids: list[NonBlank] = Field(min_length=1)
    discriminating_reason: NonBlank


class WritingDiagnosticProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypotheses: list[WritingHypothesisProposal] = Field(min_length=2)
    followup_suggestions: list[WritingFollowupSuggestion] = Field(min_length=1)


class WritingCandidateHypothesis(WritingHypothesisProposal):
    status: Literal[HypothesisStatus.CANDIDATE] = HypothesisStatus.CANDIDATE


class WritingDiagnosticMethod(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str


class WritingDiagnosticResult(BaseModel):
    source: WritingSubmissionSource
    evidence_version: str
    method: WritingDiagnosticMethod
    evidence: list[WritingEvidence]
    hypotheses: list[WritingCandidateHypothesis]
    followup_suggestions: list[WritingFollowupSuggestion]
    uncertainty_level: Literal[UncertaintyLevel.INSUFFICIENT_EVIDENCE] = UncertaintyLevel.INSUFFICIENT_EVIDENCE
    unresolved_uncertainties: list[str]
    response_performance: Literal["not_assessed"] = "not_assessed"
    ability_inference: Literal["not_supported"] = "not_supported"
    ability_evidence_valid: Literal[False] = False
