"""Read and write practice records under the existing AssessmentTask lock."""

from datetime import datetime, timezone
from hashlib import sha256

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.writing_initial_assessment import PROMPT
from app.decision.writing_agent_schemas import WritingAgentOutcome
from app.decision.writing_practice import (
    MAX_REVISION_CHARACTERS,
    WritingPracticePlan,
    WritingRevisionFeedback,
    build_revision_feedback,
    select_writing_practice,
)
from app.decision.writing_schemas import WritingDiagnosticResult
from app.persistence.models import (
    AssessmentWritingDiagnosticDecision,
    AssessmentWritingPractice,
    AssessmentWritingPracticeRevision,
    new_id,
    utc_now,
)


class WritingPracticeConflict(ValueError):
    """The requested practice operation conflicts with saved state."""


class RevisionDraftView(BaseModel):
    body: str
    version: int
    updated_at: datetime


class RevisionView(BaseModel):
    id: str
    body: str
    body_sha256: str
    base_version: int
    submitted_at: datetime
    feedback: WritingRevisionFeedback


class WritingPracticeView(BaseModel):
    id: str
    plan: WritingPracticePlan
    draft: RevisionDraftView
    revision: RevisionView | None
    max_characters: int = MAX_REVISION_CHARACTERS


def _utc(timestamp: datetime) -> datetime:
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


class WritingPracticeService:
    def __init__(self, db: Session, snapshot: WritingDiagnosticResult):
        self.db = db
        self.snapshot = snapshot

    def _find(self) -> AssessmentWritingPractice | None:
        return self.db.scalar(select(AssessmentWritingPractice).where(
            AssessmentWritingPractice.submission_id == self.snapshot.source.submission_id,
        ))

    def _required(self) -> AssessmentWritingPractice:
        practice = self._find()
        if practice is None:
            raise WritingPracticeConflict("Start the Writing micro practice before saving a revision.")
        return practice

    def _revision(self, practice: AssessmentWritingPractice) -> AssessmentWritingPracticeRevision | None:
        return self.db.scalar(select(AssessmentWritingPracticeRevision).where(
            AssessmentWritingPracticeRevision.practice_id == practice.id,
        ))

    def _view(
        self, practice: AssessmentWritingPractice, revision: AssessmentWritingPracticeRevision | None,
    ) -> WritingPracticeView:
        plan = WritingPracticePlan.model_validate(practice.plan)
        source = self.snapshot.source
        anchor = plan.anchor.source_reference
        if (anchor.submission_id, anchor.body_sha256, anchor.task_version, anchor.prompt_id, anchor.prompt_version,
                plan.decision_id) != (
            source.submission_id, source.body_sha256, source.task_version, source.prompt_id, source.prompt_version,
            practice.decision_id,
        ):
            raise WritingPracticeConflict("The saved practice no longer matches its evidence source.")
        if (plan.evidence_version != self.snapshot.evidence_version or plan.anchor not in self.snapshot.evidence
                or (plan.diagnostic_excerpt is not None and plan.diagnostic_excerpt not in anchor.quote)):
            raise WritingPracticeConflict("The saved practice citation does not match the original essay.")
        if revision and (sha256(revision.body.encode("utf-8")).hexdigest() != revision.body_sha256
                         or revision.base_version != practice.draft_version):
            raise WritingPracticeConflict("The stored Writing revision failed its text integrity check.")
        return WritingPracticeView(
            id=practice.id, plan=plan,
            draft=RevisionDraftView(body=practice.draft_body, version=practice.draft_version,
                                    updated_at=_utc(practice.updated_at)),
            revision=RevisionView(
                id=revision.id, body=revision.body, body_sha256=revision.body_sha256,
                base_version=revision.base_version, submitted_at=_utc(revision.submitted_at),
                feedback=WritingRevisionFeedback.model_validate(revision.feedback),
            ) if revision else None,
        )

    def read(self) -> WritingPracticeView | None:
        row = self.db.execute(
            select(AssessmentWritingPractice, AssessmentWritingPracticeRevision)
            .select_from(AssessmentWritingPractice)
            .outerjoin(AssessmentWritingPracticeRevision,
                       AssessmentWritingPracticeRevision.practice_id == AssessmentWritingPractice.id)
            .where(AssessmentWritingPractice.submission_id == self.snapshot.source.submission_id)
        ).one_or_none()
        return self._view(*row) if row else None

    def start(self) -> WritingPracticeView:
        if practice := self._find():
            return self._view(practice, self._revision(practice))
        rows = self.db.scalars(select(AssessmentWritingDiagnosticDecision).where(
            AssessmentWritingDiagnosticDecision.submission_id == self.snapshot.source.submission_id,
        ))
        by_phase = {item.phase: item for item in rows}
        initial = by_phase.get("initial")
        if initial is None or initial.result is None:
            raise WritingPracticeConflict("Finish the initial Writing diagnosis before starting practice.")
        first_outcome = WritingAgentOutcome.model_validate(initial.result)
        decision = by_phase.get("followup", initial)
        if (decision.result is None or (decision is initial and first_outcome.followup is not None)):
            raise WritingPracticeConflict("Finish the diagnostic follow-up before starting practice.")
        if any(item.evidence_version != self.snapshot.evidence_version for item in by_phase.values()):
            raise WritingPracticeConflict("The saved diagnostic evidence version is unavailable.")
        plan = select_writing_practice(
            self.snapshot, WritingAgentOutcome.model_validate(decision.result),
            decision_id=decision.id, task_prompt=PROMPT["text"],
            questioned_hypotheses=first_outcome.followup.hypothesis_ids if first_outcome.followup else [],
        )
        practice = AssessmentWritingPractice(
            submission_id=self.snapshot.source.submission_id, decision_id=decision.id,
            plan=plan.model_dump(mode="json"),
        )
        self.db.add(practice)
        self.db.flush()
        return self._view(practice, None)

    def save_draft(self, body: str, version: int) -> WritingPracticeView:
        practice = self._required()
        if self._revision(practice) is not None:
            raise WritingPracticeConflict("The revision has already been formally submitted.")
        if body == practice.draft_body and version in {practice.draft_version, practice.draft_version - 1}:
            return self._view(practice, None)
        if version != practice.draft_version:
            raise WritingPracticeConflict("The revision draft version changed. Read the latest draft.")
        practice.draft_body = body
        practice.draft_version += 1
        practice.updated_at = utc_now()
        return self._view(practice, None)

    def submit(self, body: str, version: int, key: str) -> WritingPracticeView:
        practice = self._required()
        if revision := self._revision(practice):
            if (key, version, body) != (revision.idempotency_key, revision.base_version, revision.body):
                raise WritingPracticeConflict("A different revision has already been formally submitted.")
            return self._view(practice, revision)
        if version != practice.draft_version:
            raise WritingPracticeConflict("The revision draft version changed. Read the latest draft.")
        plan = WritingPracticePlan.model_validate(practice.plan)
        revision_id = new_id("awpr")
        revision = AssessmentWritingPracticeRevision(
            id=revision_id, practice_id=practice.id, base_version=version, idempotency_key=key,
            body=body, body_sha256=sha256(body.encode("utf-8")).hexdigest(),
            feedback=build_revision_feedback(plan, revision_id, body).model_dump(mode="json"),
        )
        self.db.add(revision)
        self.db.flush()
        return self._view(practice, revision)
