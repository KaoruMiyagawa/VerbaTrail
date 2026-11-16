"""Listening initial assessment backed by shared learning sessions and attempts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload

from app.api.v1.dependencies import get_db
from app.application.evidence_validity import evidence_status
from app.content import listening_initial_assessment as content
from app.domain.enums import QuestionRole
from app.persistence.models import (
    AssessmentRun,
    AssessmentTask,
    Attempt,
    KnowledgeTopic,
    LearningSession,
    Question,
)

router = APIRouter(prefix="/assessment-runs/{run_id}/listening", tags=["assessment"])
audio_router = APIRouter(tags=["assessment-audio"])
LEARNER = Query(default="default_learner", min_length=1, max_length=64)


class SubmitAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    learner_id: str = Field(min_length=1, max_length=64)
    answers: dict[str, str] = Field(min_length=6, max_length=6)
    idempotency_key: str = Field(min_length=8, max_length=128)


def _owned_task(db: Session, run_id: str, learner_id: str) -> tuple[AssessmentRun, AssessmentTask]:
    run = db.scalar(select(AssessmentRun).options(selectinload(AssessmentRun.tasks)).where(
        AssessmentRun.id == run_id, AssessmentRun.learner_id == learner_id))
    if run is None:
        raise HTTPException(404, "Assessment run was not found.")
    task = next((item for item in run.tasks if item.skill_id == "listening"), None)
    if task is None:
        raise HTTPException(409, "Assessment run has no Listening task.")
    return run, task


def _owned_task_for_write(db: Session, run_id: str, learner_id: str) -> tuple[AssessmentRun, AssessmentTask]:
    """Serialize same-run mutations before inspecting task state."""
    if db.get_bind().dialect.name == "sqlite":
        # SQLite's deferred read-to-write transaction upgrade can fail when two
        # identical submissions race. Claim the writer slot before reading.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    run, task = _owned_task(db, run_id, learner_id)
    if db.get_bind().dialect.name != "sqlite":
        db.execute(select(AssessmentTask.id).where(AssessmentTask.id == task.id).with_for_update())
        db.refresh(task)
    return run, task


def _ensure_supported_version(run: AssessmentRun, task: AssessmentTask) -> str:
    if task.content_version:
        if task.content_version != content.CONTENT_VERSION:
            raise HTTPException(409, "This Listening task is pinned to an unavailable content version.")
        return task.content_version
    if run.assessment_version != "initial-v2":
        raise HTTPException(409, "This older unpinned assessment has no Listening content version; start a new assessment.")
    return content.CONTENT_VERSION


def _prior_exposure(db: Session, learner_id: str) -> bool:
    prior_session = db.scalar(select(LearningSession.id).where(
        LearningSession.learner_id == learner_id,
        LearningSession.topic_id == content.TOPIC_ID,
        LearningSession.exam_spec_version == content.CONTENT_VERSION,
    ).limit(1))
    if prior_session:
        return True
    qids = [item["id"] for item in content.QUESTIONS]
    return db.scalar(select(Attempt.id).join(LearningSession, Attempt.session_id == LearningSession.id).where(
        LearningSession.learner_id == learner_id, Attempt.question_id.in_(qids)).limit(1)) is not None


def _attempts(db: Session, session_id: str) -> list[Attempt]:
    return list(db.scalars(select(Attempt).where(Attempt.session_id == session_id)).all())


def _result(db: Session, run: AssessmentRun, task: AssessmentTask) -> dict[str, Any]:
    version = _ensure_supported_version(run, task)
    session = db.get(LearningSession, task.learning_session_id) if task.learning_session_id else None
    if task.learning_session_id and (
        session is None
        or session.learner_id != run.learner_id
        or session.exam_spec_version != version
        or session.topic_id != content.TOPIC_ID
    ):
        raise HTTPException(409, "Pinned Listening assessment session cannot be safely restored.")
    attempts = _attempts(db, session.id) if session else []
    attempts_by_question = {attempt.question_id: attempt for attempt in attempts}
    completed = task.status == "completed"
    if completed and set(attempts_by_question) != {item["id"] for item in content.QUESTIONS}:
        raise HTTPException(409, "Completed Listening task has incomplete evidence and cannot be safely restored.")
    questions: list[dict[str, Any]] = []
    for item in content.QUESTIONS if task.status != "not_started" else []:
        question: dict[str, Any] = {
            "question_id": item["id"],
            "prompt": item["prompt"],
            "options": item["options"],
            "item_type": item["item_type"],
        }
        attempt = attempts_by_question.get(item["id"])
        if completed and attempt:
            question.update({
                "answer": attempt.submitted_answer,
                "correct_answer": item["answer"],
                "is_correct": bool(attempt.is_correct),
                "explanation": item["explanation"],
                "attempt_id": attempt.id,
                "evidence_validity": evidence_status(db, "attempt", attempt.id),
            })
        questions.append(question)
    response: dict[str, Any] = {
        "assessment_run_id": run.id,
        "learner_id": run.learner_id,
        "skill_id": "listening",
        "task_id": content.TASK_ID if task.status != "not_started" else None,
        "task_version": version if task.content_version else None,
        "rule_version": content.RULE_VERSION if task.content_version else None,
        "assessment_version": run.assessment_version,
        "task_status": task.status,
        "learning_session_id": task.learning_session_id,
        "audio_url": content.AUDIO_URL if task.status != "not_started" else None,
        "audio_sha256": content.AUDIO_SHA256 if task.status != "not_started" else None,
        "repeated_exposure": task.repeated_exposure,
        "evidence_insufficient": True,
        "limitations": content.LIMITATIONS,
        "questions": questions,
        "correct_count": sum(bool(a.is_correct) for a in attempts) if completed else None,
        "attempt_references": [
            {"attempt_id": a.id, "question_id": a.question_id,
             "evidence_validity": evidence_status(db, "attempt", a.id)}
            for a in attempts
        ],
        "unassessed_capabilities": ["整体听力能力", "IELTS Band", "长期保持", "独立迁移"],
    }
    if completed:
        response["observation"] = "本次原创听力材料中的有限客观作答表现；答错记录保留为观察，不代表能力薄弱或掌握。"
    return response


@audio_router.get(content.AUDIO_URL, include_in_schema=False)
def get_initial_listening_audio() -> FileResponse:
    if not content.AUDIO_PATH.is_file():
        raise HTTPException(404, "Listening assessment audio is unavailable.")
    return FileResponse(content.AUDIO_PATH, media_type="audio/wav", filename=content.AUDIO_FILENAME)


@router.get("")
def get_listening(run_id: str, learner_id: str = LEARNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    run, task = _owned_task(db, run_id, learner_id)
    _ensure_supported_version(run, task)
    return _result(db, run, task)


@router.post("/start")
def start_listening(run_id: str, learner_id: str = LEARNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    run, task = _owned_task_for_write(db, run_id, learner_id)
    version = _ensure_supported_version(run, task)
    if task.status in ("in_progress", "completed"):
        if task.content_version != version:
            raise HTTPException(409, "Listening task content version conflicts with its saved task state.")
        return _result(db, run, task)
    if task.status != "not_started":
        raise HTTPException(409, "Listening task is not in a startable state.")

    topic = db.get(KnowledgeTopic, content.TOPIC_ID)
    if topic is None:
        topic = KnowledgeTopic(
            id=content.TOPIC_ID, slug=content.TOPIC_ID, domain_code="ielts_listening",
            title="Initial Listening Observation: Community Arts Centre",
            summary="A short original voicemail with details, cause/effect, inference, and speaker purpose.",
            difficulty_level=2,
        )
        db.add(topic)
        db.flush()
    for item in content.QUESTIONS:
        question = db.get(Question, item["id"])
        if question is None:
            db.add(Question(
                id=item["id"], topic_id=content.TOPIC_ID, question_role=QuestionRole.INITIAL.value,
                question_type="multiple_choice", prompt_text=item["prompt"],
                context_snippet=content.SCRIPT, standard_answer=item["answer"],
                answer_explanation=item["explanation"],
                evaluation_rubric={"options": item["options"], "content_version": version,
                                   "rule_version": content.RULE_VERSION, "item_type": item["item_type"]},
                difficulty=2, version=1,
            ))
        elif (
            question.topic_id != content.TOPIC_ID or question.version != 1
            or question.prompt_text != item["prompt"] or question.standard_answer != item["answer"]
            or question.answer_explanation != item["explanation"]
            or (question.evaluation_rubric or {}).get("content_version") != version
            or (question.evaluation_rubric or {}).get("rule_version") != content.RULE_VERSION
        ):
            raise HTTPException(409, "Pinned Listening assessment content conflicts with existing question data.")

    repeated_exposure = task.repeated_exposure or _prior_exposure(db, learner_id)
    session = LearningSession(
        learner_id=learner_id, topic_id=content.TOPIC_ID,
        exam_spec_version=version, status="active",
    )
    db.add(session)
    db.flush()
    task.learning_session_id = session.id
    task.content_version = version
    task.repeated_exposure = repeated_exposure
    task.status = "in_progress"
    run.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except (IntegrityError, OperationalError):
        db.rollback()
        refreshed_run, refreshed_task = _owned_task(db, run_id, learner_id)
        if refreshed_task.status in ("in_progress", "completed") and refreshed_task.content_version == version:
            return _result(db, refreshed_run, refreshed_task)
        raise HTTPException(409, "Concurrent Listening task start conflicted; reload and retry.")
    db.refresh(task)
    db.refresh(run)
    return _result(db, run, task)


@router.post("/submit")
def submit_listening(run_id: str, payload: SubmitAnswers, db: Session = Depends(get_db)) -> dict[str, Any]:
    run, task = _owned_task_for_write(db, run_id, payload.learner_id)
    version = _ensure_supported_version(run, task)
    expected = {item["id"] for item in content.QUESTIONS}
    if set(payload.answers) != expected:
        raise HTTPException(422, "Provide one answer for every Listening question.")
    for item in content.QUESTIONS:
        if payload.answers[item["id"]] not in item["options"]:
            raise HTTPException(422, "Every answer must match one of the displayed Listening options.")
    if task.learning_session_id is None or task.content_version != version:
        raise HTTPException(409, "Start the Listening task before submitting.")
    session = db.get(LearningSession, task.learning_session_id)
    if (
        session is None or session.learner_id != payload.learner_id
        or session.topic_id != content.TOPIC_ID or session.exam_spec_version != version
    ):
        raise HTTPException(404, "Listening assessment session was not found.")

    prior = _attempts(db, session.id)
    if task.status == "completed":
        by_question = {attempt.question_id: attempt for attempt in prior}
        same_answers = len(by_question) == len(content.QUESTIONS) and all(
            by_question.get(item["id"]) is not None
            and by_question[item["id"]].submitted_answer == payload.answers[item["id"]]
            for item in content.QUESTIONS
        )
        if same_answers:
            return _result(db, run, task)
        raise HTTPException(409, "Listening task is already completed with different answers.")
    if task.status != "in_progress":
        raise HTTPException(409, "Start the Listening task before submitting.")
    if prior:
        raise HTTPException(409, "Partial Listening submissions require support recovery.")

    topic = db.get(KnowledgeTopic, content.TOPIC_ID)
    created_attempts: list[Attempt] = []
    try:
        for item in content.QUESTIONS:
            question = db.get(Question, item["id"])
            answer = payload.answers[item["id"]]
            correct = answer == item["answer"]
            attempt = Attempt(
                session_id=session.id, question_id=question.id, question_version=question.version,
                question_snapshot_json={
                    "question_type": "multiple_choice", "assessment_type": "initial_listening",
                    "assessment_run_id": run.id, "assessment_task_id": task.id,
                    "content_version": version, "rule_version": content.RULE_VERSION,
                    "audio_url": content.AUDIO_URL, "audio_sha256": content.AUDIO_SHA256,
                    "competency_id": item["competency_id"], "item_type": item["item_type"],
                    "repeated_exposure": task.repeated_exposure,
                    "submit_idempotency_key": payload.idempotency_key,
                    "options": item["options"], "standard_answer": item["answer"],
                    "explanation": item["explanation"],
                    "source": content.PROVENANCE_SUMMARY,
                },
                attempt_role=QuestionRole.INITIAL.value,
                submitted_answer=answer,
                evaluation_rule_version=content.RULE_VERSION,
                is_correct=correct,
                evaluation_status="evaluated",
                evaluation_summary="Correct" if correct else "Incorrect for this item; retained as an observation.",
                idempotency_key=hashlib.sha256(f"{run.id}:{task.id}:{question.id}".encode()).hexdigest(),
                item_evaluation_json={
                    "evaluation_source": "rule_check", "content_version": version,
                    "rule_version": content.RULE_VERSION,
                    "outcome": "correct" if correct else "incorrect",
                    "assessment_run_id": run.id, "assessment_task_id": task.id,
                },
            )
            db.add(attempt)
            created_attempts.append(attempt)
        db.flush()
        now = datetime.now(timezone.utc)
        task.status = "completed"
        task.evidence_source = "attempt"
        task.evidence_reference = created_attempts[-1].id
        session.status = "completed"
        session.completed_at = now
        session.updated_at = now
        run.updated_at = now
        # Other skill tasks remain untouched; the run is not complete.
        db.commit()
    except (IntegrityError, OperationalError):
        db.rollback()
        refreshed_run, refreshed_task = _owned_task(db, run_id, payload.learner_id)
        refreshed_session = db.get(LearningSession, refreshed_task.learning_session_id) if refreshed_task.learning_session_id else None
        existing = _attempts(db, refreshed_session.id) if refreshed_session else []
        by_question = {attempt.question_id: attempt for attempt in existing}
        same_answers = len(by_question) == len(content.QUESTIONS) and all(
            by_question.get(item["id"]) is not None
            and by_question[item["id"]].submitted_answer == payload.answers[item["id"]]
            for item in content.QUESTIONS
        )
        if refreshed_task.status == "completed" and same_answers:
            return _result(db, refreshed_run, refreshed_task)
        raise HTTPException(409, "Concurrent Listening submission conflicted with a different result.")
    db.refresh(task)
    db.refresh(run)
    return _result(db, run, task)
