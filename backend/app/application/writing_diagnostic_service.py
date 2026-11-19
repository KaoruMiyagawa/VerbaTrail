"""Derive read-only Writing evidence before asking a proposer for interpretations."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from app.decision.writing_schemas import (
    WritingCandidateHypothesis,
    WritingDiagnosticMethod,
    WritingDiagnosticProposal,
    WritingDiagnosticResult,
    WritingEvidence,
    WritingEvidenceLink,
    WritingFollowupSuggestion,
    WritingHypothesisProposal,
    WritingSourceReference,
    WritingSubmissionSource,
)
from app.domain.enums import EvidenceRelation
from app.persistence.models import AssessmentWritingSubmission

EVIDENCE_VERSION = "writing-paragraph-spans-v1"
PARAGRAPH_BREAK = re.compile(r"\n[^\S\n]*\n")


def matches_submission_reference(reference: WritingSourceReference, submission: AssessmentWritingSubmission) -> bool:
    if (reference.submission_id, reference.body_sha256, reference.task_version,
            reference.prompt_id, reference.prompt_version) != (
        submission.id, submission.body_sha256, submission.task_version, submission.prompt_id, submission.prompt_version,
    ):
        return False
    body = submission.body.encode("utf-16-le")
    start, end = reference.start * 2, reference.end * 2
    return 0 <= start < end <= len(body) and body[start:end] == reference.quote.encode("utf-16-le")


def _paragraph_spans(body: str) -> Iterator[tuple[int, int]]:
    start = 0
    for separator in PARAGRAPH_BREAK.finditer(body):
        yield start, separator.start()
        start = separator.end()
    yield start, len(body)


def _extract_evidence(body: str, source: WritingSubmissionSource) -> list[WritingEvidence]:
    # Python indexes code points. JavaScript's slice indexes UTF-16 code units.
    # Build the mapping once, preserving the exact stored text and its boundaries.
    utf16_offsets = [0]
    for character in body:
        utf16_offsets.append(utf16_offsets[-1] + (2 if ord(character) > 0xFFFF else 1))

    evidence: list[WritingEvidence] = []
    for start, end in _paragraph_spans(body):
        segment = body[start:end]
        quote = segment.strip()
        if not quote:
            continue
        start += len(segment) - len(segment.lstrip())
        end = start + len(quote)
        ordinal = len(evidence) + 1
        evidence.append(WritingEvidence(
            id=f"{source.submission_id}:{EVIDENCE_VERSION}:paragraph-{ordinal}",
            paragraph_number=ordinal,
            source_reference=WritingSourceReference(
                **source.model_dump(), start=utf16_offsets[start], end=utf16_offsets[end], quote=quote,
            ),
        ))
    return evidence


@dataclass(frozen=True)
class WritingDiagnosticContext:
    prompt: str
    instructions: str
    evidence: tuple[WritingEvidence, ...]


class WritingDiagnosticProposer(Protocol):
    """A rule implementation or adapter over the existing ModelProvider may propose a next step."""

    @property
    def method(self) -> WritingDiagnosticMethod: ...

    def propose(self, context: WritingDiagnosticContext) -> WritingDiagnosticProposal: ...


class RuleBasedWritingProposer:
    method = WritingDiagnosticMethod(name="deterministic_rules", version="writing-argument-followup-v1")

    def propose(self, context: WritingDiagnosticContext) -> WritingDiagnosticProposal:
        # Opening excerpts provide a concrete starting point for the learner.
        # Their position in the essay does not establish that one interpretation is likelier.
        anchors = [item.id for item in context.evidence[:2]]
        context_links = [WritingEvidenceLink(
            evidence_id=anchor,
            relation=EvidenceRelation.NEUTRAL,
            reason="Starting excerpt for the follow-up. No semantic judgment has been made about this text.",
        ) for anchor in anchors]
        hypotheses = [
            WritingHypothesisProposal(
                id="position_unclear",
                statement="The intended position may be hard to identify in the submitted wording.",
                unresolved_question="Which sentence or sentences state the writer's position?",
                evidence_links=context_links,
            ),
            WritingHypothesisProposal(
                id="reasoning_link_unclear",
                statement="The position may be identifiable while the connection from a reason to that position remains implicit.",
                unresolved_question="What reasoning connects the cited reason to the position?",
                evidence_links=context_links,
            ),
            WritingHypothesisProposal(
                id="context_may_resolve",
                statement="Passages elsewhere in the response, or read together, may already make both the position and the connection clear.",
                unresolved_question="Which other passage, if any, makes the position and reasoning explicit?",
                evidence_links=context_links,
            ),
        ]
        return WritingDiagnosticProposal(
            hypotheses=hypotheses,
            followup_suggestions=[WritingFollowupSuggestion(
                question=("Quote the words in your essay that state your main position, and identify one reason you give for it. "
                          "How does that reason support your position? You can use any paragraph. "
                          "If part of your explanation was left unstated, say which part you are adding now."),
                hypothesis_ids=[item.id for item in hypotheses],
                evidence_ids=anchors,
                discriminating_reason=("Separates an unstated position from an unstated reasoning link and checks whether "
                                       "another passage already supplies the missing context."),
            )],
        )


def _validate_references(proposal: WritingDiagnosticProposal, evidence: list[WritingEvidence]) -> None:
    evidence_ids = {item.id for item in evidence}
    hypothesis_ids = {item.id for item in proposal.hypotheses}
    if len(hypothesis_ids) != len(proposal.hypotheses):
        raise ValueError("Writing hypothesis IDs must be unique within a proposal.")
    for hypothesis in proposal.hypotheses:
        if any(link.evidence_id not in evidence_ids for link in hypothesis.evidence_links):
            raise ValueError("Writing hypothesis cites evidence outside the current submission.")
    for followup in proposal.followup_suggestions:
        targets = set(followup.hypothesis_ids)
        if len(targets) < 2 or not targets <= hypothesis_ids:
            raise ValueError("Writing follow-up must target at least two distinct candidate hypotheses.")
        if not set(followup.evidence_ids) <= evidence_ids:
            raise ValueError("Writing follow-up cites evidence outside the current submission.")


def build_writing_diagnostic(
    submission: AssessmentWritingSubmission,
    *,
    prompt: str,
    instructions: str,
    proposer: WritingDiagnosticProposer | None = None,
) -> WritingDiagnosticResult:
    """Use a persisted submission whose task linkage, versions and digest were verified by the caller."""
    source = WritingSubmissionSource(
        submission_id=submission.id,
        body_sha256=submission.body_sha256,
        task_version=submission.task_version,
        prompt_id=submission.prompt_id,
        prompt_version=submission.prompt_version,
    )
    evidence = _extract_evidence(submission.body, source)
    candidate_proposer = proposer if proposer is not None else RuleBasedWritingProposer()
    proposal = candidate_proposer.propose(WritingDiagnosticContext(prompt, instructions, tuple(evidence)))
    _validate_references(proposal, evidence)

    # A proposal can guide a question. It cannot promote evidence or decide ability.
    return WritingDiagnosticResult(
        source=source,
        evidence_version=EVIDENCE_VERSION,
        method=candidate_proposer.method,
        evidence=evidence,
        hypotheses=[WritingCandidateHypothesis.model_validate(item.model_dump()) for item in proposal.hypotheses],
        followup_suggestions=proposal.followup_suggestions,
        unresolved_uncertainties=[
            "No semantic judgment or independent follow-up has established which interpretation fits this response.",
            "A verified quote establishes what was submitted, but does not establish performance or writing ability.",
        ],
    )
