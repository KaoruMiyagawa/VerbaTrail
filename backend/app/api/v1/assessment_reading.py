"""Reading initial-assessment task backed by shared LearningSession and Attempt records."""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.v1.dependencies import get_db
from app.application.evidence_validity import evidence_status
from app.domain.enums import QuestionRole
from app.evaluation.ielts_reading_evaluator import IeltsReadingEvaluator
from app.persistence.models import (AssessmentRun, AssessmentTask, Attempt, KnowledgeTopic, LearningSession, Question)
from app.content.reading_initial_assessment import (DESIGN_RATIONALE, LIMITATIONS, V2_VERSION, VERSIONS)

router = APIRouter(prefix="/assessment-runs/{run_id}/reading", tags=["assessment"])
LEARNER = Query(default="default_learner", min_length=1, max_length=64)

class SubmitAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    learner_id: str = Field(min_length=1, max_length=64)
    answers: dict[str, str] = Field(min_length=4, max_length=4)
    idempotency_key: str = Field(min_length=8, max_length=128)


def _owned_task(db: Session, run_id: str, learner_id: str) -> tuple[AssessmentRun, AssessmentTask]:
    run = db.scalar(select(AssessmentRun).options(selectinload(AssessmentRun.tasks)).where(
        AssessmentRun.id == run_id, AssessmentRun.learner_id == learner_id))
    if run is None:
        raise HTTPException(404, "Assessment run was not found.")
    task = next(t for t in run.tasks if t.skill_id == "reading")
    return run, task


def _prior_exposure(db: Session, learner_id: str) -> bool:
    qids = [q['id'] for version in VERSIONS.values() for q in version['questions']]
    return db.scalar(select(Attempt.id).join(LearningSession, Attempt.session_id == LearningSession.id).where(
        LearningSession.learner_id == learner_id, Attempt.question_id.in_(qids)).limit(1)) is not None


def _version_for(task: AssessmentTask, run: AssessmentRun) -> dict[str, Any]:
    if task.content_version is None and run.assessment_version == 'initial-v1':
        raise HTTPException(409, "This older unstarted assessment has no pinned Reading version; start a new assessment.")
    version = task.content_version or V2_VERSION
    spec = VERSIONS.get(version)
    if spec is None:
        raise HTTPException(409, "Reading task content version is no longer available.")
    return spec


def _result(db: Session, run: AssessmentRun, task: AssessmentTask) -> dict[str, Any]:
    if task.learning_session_id is not None and task.content_version is None:
        raise HTTPException(409, "Reading task has no pinned content version and cannot be safely restored.")
    spec = _version_for(task, run)
    questions = spec['questions']
    session = db.get(LearningSession, task.learning_session_id) if task.learning_session_id else None
    attempts = db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id).order_by(Attempt.submitted_at)).all() if session else []
    by_question = {a.question_id: a for a in attempts}
    results = []
    for item in questions:
        a = by_question.get(item['id'])
        row = {"question_id":item['id'], "prompt":item['prompt'], "options":item['options'], "skill":item['skill']}
        if task.status == "completed" and a:
            row.update({"answer":a.submitted_answer, "correct_answer":item['answer'], "is_correct":bool(a.is_correct), "explanation":item['explanation'], "attempt_id":a.id, "evidence_validity":evidence_status(db,"attempt",a.id)})
        results.append(row)
    return {"assessment_run_id":run.id,"learner_id":run.learner_id,"skill_id":"reading","task_id":spec['task_id'],
        "task_version":spec['version'],"rule_version":spec['rule_version'],"task_status":task.status,
        "learning_session_id":task.learning_session_id,"repeated_exposure":task.repeated_exposure,
        "diagnostic_quality":"legacy_answer_position_biased" if spec['position_biased'] else "limited_observation",
        "evidence_insufficient":True,"limitations":LIMITATIONS if task.status == 'completed' else ["本次短测评仅观察有限范围的阅读表现，不能推断整体阅读水平或 IELTS 分数。"],"design_rationale":spec['design_rationale'] if task.status == 'completed' else None,
        "passage":spec['passage'] if task.status != "not_started" else None,
        "questions":results if task.status != "not_started" else [],
        "correct_count":sum(bool(a.is_correct) for a in attempts) if task.status == "completed" else None,
        "attempt_references":[{"attempt_id":a.id,"question_id":a.question_id,"evidence_validity":evidence_status(db,"attempt",a.id)} for a in attempts],
        "unassessed_capabilities":["整体阅读水平","IELTS Band","长期保持","独立迁移"]}


@router.post("/start")
def start_reading(run_id: str, learner_id: str = LEARNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    run, task = _owned_task(db, run_id, learner_id)
    spec = _version_for(task, run)
    if task.status == "completed":
        return _result(db, run, task)
    if task.learning_session_id:
        return _result(db, run, task)
    topic_id = spec['topic_id']
    topic = db.get(KnowledgeTopic, topic_id)
    if topic is None:
        topic = KnowledgeTopic(id=topic_id, slug=topic_id, domain_code="ielts_academic", title="Initial Reading Observation", summary=DESIGN_RATIONALE)
        db.add(topic)
        db.flush()
    for item in spec['questions']:
        q = db.get(Question, item['id'])
        if q is None:
            db.add(Question(id=item['id'], topic_id=topic_id, question_role=QuestionRole.INITIAL.value,
                question_type="multiple_choice", prompt_text=item['prompt'], context_snippet=spec['passage'],
                standard_answer=item['answer'], answer_explanation=item['explanation'],
                evaluation_rubric={"accepted_answers":[item['answer']],"content_version":spec['version'],"rule_version":spec['rule_version'],"skill":item['skill']}, difficulty=2, version=1))
        elif (q.topic_id != topic_id or q.version != 1 or q.prompt_text != item['prompt']
              or q.context_snippet != spec['passage'] or q.standard_answer != item['answer']
              or q.answer_explanation != item['explanation']
              or (q.evaluation_rubric or {}).get("accepted_answers") != [item['answer']]
              or (q.evaluation_rubric or {}).get("skill") != item['skill']
              or (q.evaluation_rubric or {}).get("content_version") != spec['version']
              or (q.evaluation_rubric or {}).get("rule_version") != spec['rule_version']):
            raise HTTPException(409,"Assessment content version conflicts with existing question data.")
    session = LearningSession(learner_id=learner_id, topic_id=topic_id, exam_spec_version=spec['version'], status="active")
    db.add(session)
    db.flush()
    task.learning_session_id=session.id; task.content_version=spec['version']; task.status="in_progress"
    task.repeated_exposure = task.repeated_exposure or _prior_exposure(db, learner_id)
    run.updated_at=datetime.now(timezone.utc)
    db.commit()
    return _result(db, run, task)


@router.get("")
def get_reading(run_id: str, learner_id: str = LEARNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    run, task = _owned_task(db, run_id, learner_id)
    return _result(db,run,task)


@router.post("/submit")
def submit_reading(run_id: str, payload: SubmitAnswers, db: Session = Depends(get_db)) -> dict[str, Any]:
    run, task = _owned_task(db,run_id,payload.learner_id)
    spec = _version_for(task, run)
    questions = spec['questions']
    if set(payload.answers) != {q['id'] for q in questions}:
        raise HTTPException(422,"Provide one answer for every Reading question.")
    if task.learning_session_id is None or task.content_version != spec['version']:
        raise HTTPException(409,"Start the Reading task before submitting.")
    session = db.get(LearningSession,task.learning_session_id)
    if session is None or session.learner_id != payload.learner_id or session.exam_spec_version != spec['version'] or session.topic_id != spec['topic_id']:
        raise HTTPException(404,"Reading task session was not found.")
    existing = db.scalars(select(Attempt).where(Attempt.session_id==session.id)).all()
    if task.status != "completed" and existing:
        # A concurrent request can read the old task state and then observe
        # the winner's just-committed Attempts under SQLite's deferred reads.
        # Refresh the task before treating those rows as a partial submission.
        db.refresh(task)
        if task.status == "completed":
            existing = db.scalars(select(Attempt).where(Attempt.session_id == session.id)).all()
            if all(next((a.submitted_answer for a in existing if a.question_id == q['id']), None) == payload.answers[q['id']] for q in questions):
                return _result(db, run, task)
            raise HTTPException(409, "Reading task is already completed with different answers.")
        raise HTTPException(409,"Partial Reading submissions require support recovery.")
    if task.status == "completed":
        if all(next((a.submitted_answer for a in existing if a.question_id==q['id']),None)==payload.answers[q['id']] for q in questions):
            return _result(db,run,task)
        raise HTTPException(409,"Reading task is already completed with different answers.")
    task.repeated_exposure = task.repeated_exposure or _prior_exposure(db,payload.learner_id)
    topic=db.get(KnowledgeTopic,session.topic_id)
    evaluator=IeltsReadingEvaluator()
    try:
        for item in questions:
            question=db.get(Question,item['id'])
            answer=payload.answers[item['id']]
            attempt=Attempt(session_id=session.id,question_id=question.id,question_version=question.version,
                question_snapshot_json={"question_type":"multiple_choice","content_version":spec['version'],"rule_version":spec['rule_version'],"assessment_task_id":spec['task_id'],"assessment_type":"initial_reading","repeated_exposure":task.repeated_exposure},
                attempt_role=QuestionRole.INITIAL.value,submitted_answer=answer,
                evaluation_rule_version=spec['rule_version'],is_correct=" ".join(answer.casefold().split())==" ".join(item['answer'].casefold().split()),
                evaluation_status="evaluated",idempotency_key=f"{run.id}:{spec['task_id']}:{item['id']}")
            evaluation=evaluator.evaluate_attempt(attempt=attempt,question=question,topic=topic)
            attempt.evaluation_summary=evaluation.summary
            attempt.item_evaluation_json={"evaluation_source":"rule_check","content_version":spec['version'],"rule_version":spec['rule_version'],"outcome":"correct" if attempt.is_correct else "incorrect","assessment_run_id":run.id,"assessment_task_id":task.id}
            db.add(attempt)
        db.flush()
        task.status="completed"; task.evidence_source="attempt"; task.evidence_reference=attempt.id
        session.status="completed"; session.completed_at=datetime.now(timezone.utc); session.updated_at=session.completed_at
        run.updated_at=session.completed_at
        db.commit()
    except IntegrityError:
        db.rollback()
        run, task = _owned_task(db, run_id, payload.learner_id)
        if task.status == "completed":
            attempts = db.scalars(select(Attempt).where(Attempt.session_id == task.learning_session_id)).all()
            if all(next((a.submitted_answer for a in attempts if a.question_id == q["id"]), None) == payload.answers[q["id"]] for q in questions):
                return _result(db, run, task)
        raise HTTPException(409, "Concurrent Reading submission conflicted with a different result.")
    db.refresh(task); db.refresh(run)
    return _result(db,run,task)
