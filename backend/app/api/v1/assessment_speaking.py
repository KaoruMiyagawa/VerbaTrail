"""Register browser recording metadata for a versioned Speaking assessment."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_db
from app.content.speaking_initial_assessment import LIMITATIONS, PROMPT, RECORDING_SOURCE, TASK_VERSION
from app.persistence.models import AssessmentRun, AssessmentTask, SpeakingAssessmentRecording

router = APIRouter(prefix="/assessment-runs/{run_id}/speaking", tags=["assessment"])
Learner = Annotated[str, Query(min_length=1, max_length=64)]
Version = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learner_id: str = Field(default="default_learner", min_length=1, max_length=64)
    recording_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    task_version: Version
    prompt_id: Version
    prompt_version: Version
    duration_seconds: float = Field(gt=0, le=600, allow_inf_nan=False, strict=True)
    recorded_at: AwareDatetime
    idempotency_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=128)]


class PromptView(BaseModel):
    task_id: str
    prompt_id: str
    prompt_version: str
    text: str
    instructions: str
    suggested_seconds: int
    source: str


class RecordingView(BaseModel):
    recording_id: str
    duration_seconds: float
    recorded_at: AwareDatetime
    registered_at: AwareDatetime
    provenance: Literal["client_reported"] = "client_reported"


class SpeakingTaskView(BaseModel):
    assessment_run_id: str
    learner_id: str
    assessment_version: str
    skill_id: Literal["speaking"] = "speaking"
    task_status: Literal["not_started", "in_progress", "skipped", "completed"]
    task_version: str | None
    prompt: PromptView | None
    repeated_exposure: bool
    metadata_status: Literal["not_registered", "registered"]
    recording: RecordingView | None
    evidence_source: str | None
    evidence_reference: str | None
    evidence_validity: Literal["not_evaluated"] = "not_evaluated"
    audio_available_on_server: Literal[False] = False
    local_audio_availability: Literal["unknown"] = "unknown"
    evaluation_status: Literal["not_assessed"] = "not_assessed"
    limitations: list[str]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _owned_task(
    db: Session, run_id: str, learner_id: str, *, write: bool = False,
) -> tuple[AssessmentRun, AssessmentTask, SpeakingAssessmentRecording | None]:
    if write and db.get_bind().dialect.name == "sqlite":
        # Reserve the writer before reading the task, including on replay.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")

    query = select(AssessmentRun, AssessmentTask, SpeakingAssessmentRecording).join(
        AssessmentTask, AssessmentTask.run_id == AssessmentRun.id,
    ).outerjoin(
        SpeakingAssessmentRecording, SpeakingAssessmentRecording.assessment_task_id == AssessmentTask.id,
    ).where(
        AssessmentRun.id == run_id, AssessmentRun.learner_id == learner_id,
        AssessmentTask.skill_id == "speaking",
    )
    if write and db.get_bind().dialect.name != "sqlite":
        # Lock the task before taking a fresh snapshot of its recording.
        task_id = db.scalar(query.with_only_columns(AssessmentTask.id, maintain_column_froms=True)
                            .with_for_update(of=AssessmentTask))
        if task_id is None:
            raise HTTPException(404, "Speaking assessment task was not found.")
    row = db.execute(query).one_or_none()
    if row is None:
        raise HTTPException(404, "Speaking assessment task was not found.")
    run, task, recording = row
    if task.content_version is None:
        if run.assessment_version != "initial-v2" or task.status != "not_started":
            raise HTTPException(409, "This assessment has no pinned Speaking version and cannot enable this task.")
    elif task.content_version != TASK_VERSION:
        raise HTTPException(409, "The pinned Speaking task version is unavailable.")
    if task.status == "completed":
        if (recording is None or task.evidence_source != RECORDING_SOURCE
                or task.evidence_reference != recording.recording_id):
            raise HTTPException(409, "Speaking task is completed but its recording metadata is missing or inconsistent.")
    elif recording is not None or task.evidence_source is not None or task.evidence_reference is not None:
        raise HTTPException(409, "Speaking task has inconsistent recording metadata.")
    return run, task, recording


def _view(
    run: AssessmentRun, task: AssessmentTask, recording: SpeakingAssessmentRecording | None,
) -> SpeakingTaskView:
    return SpeakingTaskView(
        assessment_run_id=run.id,
        learner_id=run.learner_id,
        assessment_version=run.assessment_version,
        task_status=task.status,
        task_version=task.content_version,
        prompt=PromptView(**PROMPT) if task.content_version else None,
        repeated_exposure=task.repeated_exposure,
        metadata_status="registered" if recording else "not_registered",
        recording=RecordingView(
            recording_id=recording.recording_id,
            duration_seconds=recording.duration_seconds,
            recorded_at=_utc(recording.recorded_at),
            registered_at=_utc(recording.registered_at),
        ) if recording else None,
        evidence_source=task.evidence_source,
        evidence_reference=task.evidence_reference,
        limitations=LIMITATIONS,
    )


@router.get("")
def get_speaking(run_id: str, learner_id: Learner = "default_learner", db: Session = Depends(get_db)) -> SpeakingTaskView:
    return _view(*_owned_task(db, run_id, learner_id))


@router.post("/start")
def start_speaking(run_id: str, learner_id: Learner = "default_learner", db: Session = Depends(get_db)) -> SpeakingTaskView:
    run, task, recording = _owned_task(db, run_id, learner_id, write=True)
    if task.status in ("in_progress", "completed"):
        return _view(run, task, recording)
    if task.status != "not_started":
        raise HTTPException(409, "Speaking task is not startable.")
    prior_task = db.scalar(
        select(AssessmentTask.id).join(AssessmentRun).where(
            AssessmentRun.learner_id == learner_id, AssessmentTask.id != task.id,
            AssessmentTask.skill_id == "speaking", AssessmentTask.content_version == TASK_VERSION,
        ).limit(1)
    )
    task.content_version = TASK_VERSION
    task.repeated_exposure = prior_task is not None
    task.status = "in_progress"
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _view(run, task, None)


@router.post("/complete")
def complete_speaking(run_id: str, payload: CompleteRequest, db: Session = Depends(get_db)) -> SpeakingTaskView:
    run, task, recording = _owned_task(db, run_id, payload.learner_id, write=True)
    if (task.content_version != payload.task_version or payload.prompt_id != PROMPT["prompt_id"]
            or payload.prompt_version != PROMPT["prompt_version"]):
        raise HTTPException(409, "Start the Speaking task and submit metadata for its pinned task and prompt.")
    if recording is not None:
        if (recording.recording_id != payload.recording_id or recording.idempotency_key != payload.idempotency_key
                or recording.duration_seconds != payload.duration_seconds
                or _utc(recording.recorded_at) != _utc(payload.recorded_at)):
            raise HTTPException(409, "Speaking task already has a different recording registration.")
        return _view(run, task, recording)
    if task.status != "in_progress":
        raise HTTPException(409, "Speaking task is not ready for registration.")
    if db.get(SpeakingAssessmentRecording, payload.recording_id) is not None:
        raise HTTPException(409, "Recording ID is already registered for an assessment.")
    now = datetime.now(timezone.utc)
    recording = SpeakingAssessmentRecording(
        recording_id=payload.recording_id,
        assessment_task_id=task.id,
        duration_seconds=payload.duration_seconds,
        recorded_at=_utc(payload.recorded_at),
        registered_at=now,
        idempotency_key=payload.idempotency_key,
    )
    db.add(recording)
    task.status = "completed"
    task.evidence_source = RECORDING_SOURCE
    task.evidence_reference = recording.recording_id
    run.updated_at = now
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.get(SpeakingAssessmentRecording, payload.recording_id) is not None:
            raise HTTPException(409, "Recording ID is already registered for an assessment.") from None
        raise
    return _view(run, task, recording)
