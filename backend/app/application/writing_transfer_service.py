"""Pin a Writing transfer task, preserve its response, and expose factual observations."""

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.writing_diagnostic_service import matches_submission_reference
from app.content import writing_initial_assessment as initial
from app.content.writing_transfer import CATALOG, WritingTransferTask
from app.decision.writing_agent_schemas import WritingAspect
from app.decision.writing_practice import WORD, WritingPracticePlan
from app.persistence.models import (
    AssessmentWritingDiagnosticDecision,
    AssessmentWritingPractice,
    AssessmentWritingPracticeRevision,
    AssessmentWritingSubmission,
    AssessmentWritingTransfer,
    AssessmentWritingTransferResponse,
    utc_now,
)


class WritingTransferConflict(ValueError):
    """The request does not match the saved transfer state."""


class WritingTransferLineage(BaseModel):
    original_submission_id: str
    initial_diagnostic_id: str
    followup_diagnostic_id: str | None
    practice_id: str
    practice_revision_id: str


class WritingTransferObservation(BaseModel):
    target: WritingAspect
    word_count: int
    paragraph_count: int
    repeated_exposure: bool
    evidence_validity: Literal["not_evaluated", "repeated_exposure"]
    completed: Literal[True] = True
    distinct_material_from_training: Literal[True] = True
    evaluation_method: Literal["deterministic_text_observation"] = "deterministic_text_observation"
    evaluation_version: Literal["writing-transfer-text-v1"] = "writing-transfer-text-v1"
    evaluation_status: Literal["not_assessed"] = "not_assessed"
    assessment_sufficient: Literal[False] = False
    ability_evidence_valid: Literal[False] = False
    ability_conclusion: None = None


class TransferDraftView(BaseModel):
    body: str
    version: int
    updated_at: datetime


class TransferResponseView(BaseModel):
    id: str
    body: str
    body_sha256: str
    base_version: int
    submitted_at: datetime
    observation: WritingTransferObservation
    lineage: WritingTransferLineage


class WritingTransferView(BaseModel):
    id: str
    task: WritingTransferTask
    assigned_at: datetime
    repeated_exposure: bool
    draft: TransferDraftView | None
    response: TransferResponseView | None


class TransferComparisonText(BaseModel):
    id: str
    body: str
    body_sha256: str


class WritingTransferComparisonContext(BaseModel):
    """Internal input for a future source-grounded review. Never served to the editor."""

    task: WritingTransferTask
    original_prompt: str
    observation: WritingTransferObservation
    lineage: WritingTransferLineage
    original: TransferComparisonText
    revision: TransferComparisonText
    transfer: TransferComparisonText


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _digest(body: str) -> str:
    return sha256(body.encode("utf-8")).hexdigest()


class WritingTransferService:
    """Mutations run under the same AssessmentTask lock as Writing practice."""

    def __init__(self, db: Session, source: AssessmentWritingSubmission, learner_id: str):
        self.db = db
        self.source = source
        self.learner_id = learner_id

    def _assignment_query(self):
        return (
            select(AssessmentWritingTransfer)
            .select_from(AssessmentWritingTransfer)
            .join(AssessmentWritingPracticeRevision,
                  AssessmentWritingPracticeRevision.id == AssessmentWritingTransfer.practice_revision_id)
            .join(AssessmentWritingPractice, AssessmentWritingPractice.id == AssessmentWritingPracticeRevision.practice_id)
            .where(AssessmentWritingPractice.submission_id == self.source.id)
        )

    def _check_learner(self, assignment: AssessmentWritingTransfer | None) -> None:
        if assignment is not None and assignment.learner_id != self.learner_id:
            raise WritingTransferConflict("The Writing transfer no longer matches its learner scope.")

    def _find(self) -> AssessmentWritingTransfer | None:
        assignment = self.db.scalar(self._assignment_query())
        self._check_learner(assignment)
        return assignment

    def _required(self) -> AssessmentWritingTransfer:
        assignment = self._find()
        if assignment is None:
            raise WritingTransferConflict("Start the Writing transfer before saving an answer.")
        return assignment

    def _basis(
        self, revision_id: str | None = None,
    ) -> tuple[WritingPracticePlan, AssessmentWritingPracticeRevision, WritingTransferLineage]:
        practice, revision, decision = self._basis_rows(revision_id)
        plan = WritingPracticePlan.model_validate(practice.plan)
        anchor = plan.anchor.source_reference
        if (plan.decision_id != decision.id or plan.evidence_version != decision.evidence_version
                or not matches_submission_reference(anchor, self.source)
                or (plan.diagnostic_excerpt is not None and plan.diagnostic_excerpt not in anchor.quote)
                or plan.task_prompt != initial.PROMPT["text"] or _digest(revision.body) != revision.body_sha256):
            raise WritingTransferConflict("The completed Writing practice has inconsistent evidence.")
        initial_id = decision.id if decision.phase == "initial" else self.db.scalar(
            select(AssessmentWritingDiagnosticDecision.id).where(
                AssessmentWritingDiagnosticDecision.submission_id == self.source.id,
                AssessmentWritingDiagnosticDecision.phase == "initial",
                AssessmentWritingDiagnosticDecision.completed_at.is_not(None),
            )
        )
        if initial_id is None:
            raise WritingTransferConflict("The initial Writing diagnostic decision is unavailable.")
        return plan, revision, WritingTransferLineage(
            original_submission_id=self.source.id, initial_diagnostic_id=initial_id,
            followup_diagnostic_id=decision.id if decision.phase == "followup" else None,
            practice_id=practice.id, practice_revision_id=revision.id,
        )

    def _basis_rows(self, revision_id: str | None):
        query = select(
            AssessmentWritingPractice, AssessmentWritingPracticeRevision, AssessmentWritingDiagnosticDecision,
        ).select_from(AssessmentWritingPractice).join(
            AssessmentWritingPracticeRevision,
            AssessmentWritingPracticeRevision.practice_id == AssessmentWritingPractice.id,
        ).join(
            AssessmentWritingDiagnosticDecision,
            AssessmentWritingDiagnosticDecision.id == AssessmentWritingPractice.decision_id,
        ).where(
            AssessmentWritingPractice.submission_id == self.source.id,
            AssessmentWritingDiagnosticDecision.submission_id == self.source.id,
            AssessmentWritingDiagnosticDecision.completed_at.is_not(None),
        )
        if revision_id is not None:
            query = query.where(AssessmentWritingPracticeRevision.id == revision_id)
        row = self.db.execute(query).one_or_none()
        if row is None:
            raise WritingTransferConflict("Formally submit the Writing practice revision before starting transfer.")
        return row

    def _seen_materials(self) -> set[str]:
        return set(self.db.scalars(select(AssessmentWritingTransfer.material_id).where(
            AssessmentWritingTransfer.learner_id == self.learner_id,
        )))

    def start(self) -> WritingTransferView:
        if assignment := self._find():
            return self._view(assignment, self._response(assignment))
        plan, revision, _ = self._basis()
        # These materials are scoped to the D1 prompt used by the micro exercise.
        if (self.source.task_version, self.source.prompt_id, self.source.prompt_version) != (
            initial.TASK_VERSION, initial.PROMPT_ID, initial.PROMPT_VERSION,
        ):
            raise WritingTransferConflict("No compatible transfer catalogue has been published for this prompt.")
        candidates = [task for task in CATALOG if task.aspect == plan.aspect
                      and task.topic_id != initial.TASK_ID.removeprefix("writing-initial-")
                      and task.prompt != plan.task_prompt and task.material != plan.anchor.source_reference.quote]
        if not candidates:
            raise WritingTransferConflict("No distinct Writing transfer material is available for this target.")

        def assign(task: WritingTransferTask, repeated: bool) -> AssessmentWritingTransfer:
            return AssessmentWritingTransfer(
                practice_revision_id=revision.id, learner_id=self.learner_id,
                material_id=task.material_id, task_snapshot=task.model_dump(mode="json"),
                repeated_exposure=repeated,
            )

        seen = self._seen_materials()
        for task in candidates:
            if task.material_id in seen:
                continue
            assignment = assign(task, False)
            try:
                with self.db.begin_nested():
                    self.db.add(assignment)
                    self.db.flush()
            except IntegrityError:
                # A different run can claim the same material concurrently. Its committed
                # exposure wins; try another catalogue item before falling back to a repeat.
                seen = self._seen_materials()
                if task.material_id not in seen:
                    raise
                continue
            return self._view(assignment, None)
        assignment = assign(candidates[0], True)
        self.db.add(assignment)
        self.db.flush()
        return self._view(assignment, None)

    def _response(self, assignment: AssessmentWritingTransfer) -> AssessmentWritingTransferResponse | None:
        return self.db.scalar(select(AssessmentWritingTransferResponse).where(
            AssessmentWritingTransferResponse.transfer_id == assignment.id,
        ))

    def _view(
        self, assignment: AssessmentWritingTransfer, response: AssessmentWritingTransferResponse | None,
    ) -> WritingTransferView:
        task = WritingTransferTask.model_validate(assignment.task_snapshot)
        if task.material_id != assignment.material_id:
            raise WritingTransferConflict("The saved Writing transfer material is inconsistent.")
        plan, _, lineage = self._basis(assignment.practice_revision_id)
        if plan.aspect != task.aspect:
            raise WritingTransferConflict("The saved Writing transfer target does not match the training plan.")
        return WritingTransferView(
            id=assignment.id, task=task, assigned_at=_utc(assignment.assigned_at),
            repeated_exposure=assignment.repeated_exposure,
            draft=TransferDraftView(body=assignment.draft_body, version=assignment.draft_version,
                                    updated_at=_utc(assignment.updated_at)) if response is None else None,
            response=self._response_view(assignment, task, response, lineage) if response else None,
        )

    def _response_view(
        self, assignment: AssessmentWritingTransfer, task: WritingTransferTask,
        response: AssessmentWritingTransferResponse, lineage: WritingTransferLineage,
    ) -> TransferResponseView:
        if _digest(response.body) != response.body_sha256:
            raise WritingTransferConflict("The saved Writing transfer response failed its integrity check.")
        observation = WritingTransferObservation.model_validate(response.observation)
        if (task.aspect != observation.target
                or assignment.repeated_exposure != observation.repeated_exposure
                or observation.evidence_validity != ("repeated_exposure" if assignment.repeated_exposure else "not_evaluated")
                or assignment.draft_version != response.base_version):
            raise WritingTransferConflict("The saved Writing transfer evidence does not match its assignment.")
        return TransferResponseView(
            id=response.id, body=response.body, body_sha256=response.body_sha256,
            base_version=response.base_version, submitted_at=_utc(response.submitted_at),
            observation=observation, lineage=lineage,
        )

    def read(self) -> WritingTransferView | None:
        row = self.db.execute(
            self._assignment_query().add_columns(AssessmentWritingTransferResponse)
            .outerjoin(AssessmentWritingTransferResponse,
                       AssessmentWritingTransferResponse.transfer_id == AssessmentWritingTransfer.id)
        ).one_or_none()
        if row is None:
            return None
        assignment, response = row
        self._check_learner(assignment)
        return self._view(assignment, response)

    def save_draft(self, body: str, version: int) -> WritingTransferView:
        assignment = self._required()
        if self._response(assignment) is not None:
            raise WritingTransferConflict("The Writing transfer response is final.")
        if body == assignment.draft_body and version in {assignment.draft_version, assignment.draft_version - 1}:
            return self._view(assignment, None)
        if version != assignment.draft_version:
            raise WritingTransferConflict("The transfer draft version changed. Read the latest draft.")
        assignment.draft_body = body
        assignment.draft_version += 1
        assignment.updated_at = utc_now()
        return self._view(assignment, None)

    def submit(self, body: str, version: int, key: str) -> WritingTransferView:
        assignment = self._required()
        if response := self._response(assignment):
            if (key, version, body) != (response.idempotency_key, response.base_version, response.body):
                raise WritingTransferConflict("A different Writing transfer response is already final.")
            return self._view(assignment, response)
        if version != assignment.draft_version:
            raise WritingTransferConflict("The transfer draft version changed. Read the latest draft.")

        text_for_paragraphs = body.replace("\r\n", "\n").replace("\r", "\n")
        observation = WritingTransferObservation(
            target=WritingTransferTask.model_validate(assignment.task_snapshot).aspect,
            word_count=len(WORD.findall(body)),
            paragraph_count=sum(bool(part.strip()) for part in re.split(r"\n[^\S\n]*\n", text_for_paragraphs)),
            repeated_exposure=assignment.repeated_exposure,
            evidence_validity="repeated_exposure" if assignment.repeated_exposure else "not_evaluated",
        )
        response = AssessmentWritingTransferResponse(
            transfer_id=assignment.id, body=body, body_sha256=_digest(body), base_version=version,
            idempotency_key=key, observation=observation.model_dump(mode="json"),
        )
        self.db.add(response)
        self.db.flush()
        return self._view(assignment, response)

    def comparison_context(self) -> WritingTransferComparisonContext:
        """Assemble only saved source texts after a formal transfer submission."""
        if _digest(self.source.body) != self.source.body_sha256:
            raise WritingTransferConflict("The original Writing response failed its integrity check.")
        view = self.read()
        if view is None or view.response is None:
            raise WritingTransferConflict("A formal Writing transfer response is required for comparison.")
        _, revision, _ = self._basis(view.response.lineage.practice_revision_id)
        return WritingTransferComparisonContext(
            task=view.task, original_prompt=initial.PROMPT["text"],
            observation=view.response.observation, lineage=view.response.lineage,
            original=TransferComparisonText(id=self.source.id, body=self.source.body, body_sha256=self.source.body_sha256),
            revision=TransferComparisonText(id=revision.id, body=revision.body, body_sha256=revision.body_sha256),
            transfer=TransferComparisonText(id=view.response.id, body=view.response.body, body_sha256=view.response.body_sha256),
        )
