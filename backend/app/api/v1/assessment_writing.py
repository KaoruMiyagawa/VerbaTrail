"""Run-scoped initial Writing response collection; no semantic scoring."""

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_db
from app.content import writing_initial_assessment as content
from app.persistence.models import AssessmentRun, AssessmentTask, AssessmentWritingDraft, AssessmentWritingSubmission, new_id, utc_now

router = APIRouter(prefix="/assessment-runs/{run_id}/writing", tags=["assessment"])
Learner = Annotated[str, Query(min_length=1, max_length=64)]
WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*")


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    learner_id: str = Field(default="default_learner", min_length=1, max_length=64)
    body: str = Field(max_length=content.MAX_CHARACTERS)
    version: int = Field(ge=0, le=2147483646)

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        if any((ord(c) < 32 and c not in "\t\n\r") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Only plain Unicode text and standard whitespace are supported.")
        return value.replace("\r\n", "\n").replace("\r", "\n")


class SubmitInput(DraftInput):
    task_version: str = Field(min_length=1, max_length=64)
    prompt_id: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def _write_lock(db: Session) -> None:
    if db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _owned(db: Session, run_id: str, learner_id: str, write: bool = False) -> tuple[AssessmentRun, AssessmentTask]:
    if write:
        _write_lock(db)
    row = db.execute(select(AssessmentRun, AssessmentTask).join(
        AssessmentTask, AssessmentTask.run_id == AssessmentRun.id,
    ).where(AssessmentRun.id == run_id, AssessmentRun.learner_id == learner_id,
            AssessmentTask.skill_id == "writing")).one_or_none()
    if row is None:
        raise HTTPException(404, "Assessment run was not found.")
    run, task = row
    if db.get_bind().dialect.name != "sqlite" and write:
        db.execute(select(AssessmentTask.id).where(AssessmentTask.id == task.id).with_for_update())
        db.refresh(task)
    if task.content_version is None:
        if run.assessment_version != "initial-v2" or task.status != "not_started":
            raise HTTPException(409, "This assessment has no pinned Writing version and cannot safely enable or restore this task; start a new initial-v2 assessment.")
    elif task.content_version != content.TASK_VERSION:
        raise HTTPException(409, "The pinned Writing task version is unavailable.")
    return run, task


def _check_state(db: Session, task: AssessmentTask) -> tuple[AssessmentWritingDraft | None, AssessmentWritingSubmission | None]:
    draft = db.get(AssessmentWritingDraft, task.id)
    submission = db.scalar(select(AssessmentWritingSubmission).where(AssessmentWritingSubmission.assessment_task_id == task.id))
    if task.status == "not_started":
        if draft or submission or task.evidence_reference or task.evidence_source:
            raise HTTPException(409, "Unstarted Writing task contains unexpected saved state.")
    elif task.status == "in_progress":
        if draft is None or submission or task.evidence_reference or task.evidence_source:
            raise HTTPException(409, "Writing draft state cannot be safely restored.")
    elif task.status == "completed":
        if (draft is None or submission is None or task.evidence_source != content.SUBMISSION_SOURCE
                or task.evidence_reference != submission.id
                or submission.task_version != content.TASK_VERSION
                or submission.prompt_id != content.PROMPT_ID or submission.prompt_version != content.PROMPT_VERSION
                or sha256(submission.body.encode("utf-8")).hexdigest() != submission.body_sha256):
            raise HTTPException(409, "Completed Writing evidence is missing or inconsistent and cannot be safely restored.")
    else:
        raise HTTPException(409, "Writing task is in an unsupported state.")
    return draft, submission


def _submission_view(submission: AssessmentWritingSubmission | None) -> dict | None:
    if submission is None:
        return None
    submitted = submission.submitted_at
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=timezone.utc)
    return {
        "submission_id": submission.id,
        "task_version": submission.task_version,
        "prompt_id": submission.prompt_id,
        "prompt_version": submission.prompt_version,
        "draft_version": submission.draft_version,
        "body": submission.body,
        "body_sha256": submission.body_sha256,
        "word_count": submission.word_count,
        "paragraph_count": submission.paragraph_count,
        "submitted_at": submitted,
        "evidence_type": "assessment_activity",
        "source": content.SUBMISSION_SOURCE,
        "evaluation_status": "not_assessed",
        "semantic_quality": "not_assessed",
        "ability_evidence_valid": False,
    }


def _view(db: Session, run: AssessmentRun, task: AssessmentTask) -> dict:
    draft, submission = _check_state(db, task)
    updated = draft.updated_at if draft else None
    if updated and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return {
        "assessment_run_id": run.id,
        "learner_id": run.learner_id,
        "assessment_version": run.assessment_version,
        "skill_id": "writing",
        "task_status": task.status,
        "task_id": content.TASK_ID if task.content_version else None,
        "task_version": task.content_version,
        "prompt": content.PROMPT if task.content_version else None,
        "draft": {"body": draft.body, "version": draft.version, "updated_at": updated} if draft else None,
        "submission": _submission_view(submission),
        "evidence_source": task.evidence_source,
        "evidence_reference": task.evidence_reference,
        "limitations": content.LIMITATIONS,
    }


@router.get("")
def get_writing(run_id: str, learner_id: Learner = "default_learner", db: Session = Depends(get_db)) -> dict:
    run, task = _owned(db, run_id, learner_id)
    return _view(db, run, task)


@router.post("/start")
def start_writing(run_id: str, learner_id: Learner = "default_learner", db: Session = Depends(get_db)) -> dict:
    run, task = _owned(db, run_id, learner_id, write=True)
    if task.status in ("in_progress", "completed"):
        return _view(db, run, task)
    if task.status != "not_started":
        raise HTTPException(409, "Writing task is not startable.")
    task.content_version = content.TASK_VERSION
    task.repeated_exposure = db.scalar(select(AssessmentTask.id).join(AssessmentRun).where(
        AssessmentRun.learner_id == run.learner_id,
        AssessmentTask.id != task.id,
        AssessmentTask.skill_id == "writing",
        AssessmentTask.content_version == content.TASK_VERSION,
    ).limit(1)) is not None
    task.status = "in_progress"
    draft = AssessmentWritingDraft(assessment_task_id=task.id, body="", version=0, updated_at=utc_now())
    db.add(draft)
    run.updated_at = utc_now()
    db.commit()
    return _view(db, run, task)


@router.put("/draft")
def save_draft(run_id: str, payload: DraftInput, db: Session = Depends(get_db)) -> dict:
    run, task = _owned(db, run_id, payload.learner_id, write=True)
    _draft, submission = _check_state(db, task)
    if task.status != "in_progress" or submission is not None:
        raise HTTPException(409, "Writing draft is not editable after formal submission.")
    draft = db.get(AssessmentWritingDraft, task.id)
    if payload.version != draft.version:
        raise HTTPException(409, "Writing draft version changed; reload the latest draft before saving.")
    if payload.body != draft.body:
        draft.body = payload.body
        draft.version += 1
        draft.updated_at = utc_now()
        run.updated_at = draft.updated_at
        db.commit()
    else:
        db.rollback()
        draft = db.get(AssessmentWritingDraft, task.id)
    return {"body": draft.body, "version": draft.version, "updated_at": draft.updated_at}


@router.post("/submit")
def submit_writing(run_id: str, payload: SubmitInput, db: Session = Depends(get_db)) -> dict:
    run, task = _owned(db, run_id, payload.learner_id, write=True)
    if (payload.task_version != content.TASK_VERSION or payload.prompt_id != content.PROMPT_ID
            or payload.prompt_version != content.PROMPT_VERSION or task.content_version != payload.task_version):
        raise HTTPException(409, "Submission does not match this run's pinned Writing task and prompt versions.")
    draft, prior = _check_state(db, task)
    if prior is not None:
        same = (prior.idempotency_key == payload.idempotency_key and prior.body == payload.body
                and prior.draft_version == payload.version and prior.task_version == payload.task_version
                and prior.prompt_id == payload.prompt_id and prior.prompt_version == payload.prompt_version)
        if not same:
            raise HTTPException(409, "Writing task already has a different immutable submission.")
        db.rollback()
        return _view(db, run, task)
    if task.status != "in_progress" or draft is None:
        raise HTTPException(409, "Start the Writing task before submitting.")
    if payload.version != draft.version:
        raise HTTPException(409, "Writing draft version changed; reload before submitting.")
    words = len(WORD.findall(payload.body))
    if words == 0:
        raise HTTPException(422, "Enter some essay text before submitting.")
    now = utc_now()
    draft.body = payload.body
    draft.version += 1
    draft.updated_at = now
    submission = AssessmentWritingSubmission(
        id=new_id("aws"), assessment_task_id=task.id, task_version=payload.task_version, prompt_id=payload.prompt_id,
        prompt_version=payload.prompt_version, draft_version=payload.version, body=payload.body,
        body_sha256=sha256(payload.body.encode("utf-8")).hexdigest(), word_count=words,
        paragraph_count=sum(bool(block.strip()) for block in re.split(r"\n[^\S\n]*\n", payload.body)),
        submitted_at=now, idempotency_key=payload.idempotency_key,
    )
    task.evidence_reference = submission.id
    task.evidence_source = content.SUBMISSION_SOURCE
    task.status = "completed"
    db.add(submission)
    run.updated_at = now
    db.commit()
    return _view(db, run, task)
