"""Article practice on the shared material, learning session and attempt tables."""

from datetime import timezone
from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_db
from app.api.v1.schemas import ReadingGroupEvaluation
from app.content.reading_practice import Article, DOMAIN, RULE_VERSION, reading_articles
from app.domain.exams import EXAM_CATALOG_VERSION
from app.domain.reading_groups import HeadingAnswerKey, MatchingHeadingsGroup, evaluate_headings
from app.persistence.models import (
    Attempt, ExamPreparationProfile, KnowledgeTopic, LearningMaterial, LearningSession,
    Question, ReadingGroupVersion, ReadingRuleVersion, utc_now,
)

router = APIRouter(prefix="/reading/articles", tags=["reading"])
Learner = Annotated[str, Query(min_length=1, max_length=64)]
Choice = Annotated[str, StringConstraints(min_length=1, max_length=12)]


class SubmitAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, Choice] = Field(min_length=10, max_length=14)


def _variant(db: Session, learner_id: str) -> str | None:
    profile = db.get(ExamPreparationProfile, learner_id)
    return profile.exam_variant if profile else None


def _slot_id(learner_id: str, article: Article) -> str:
    return "read_" + sha256(f"{article.id}:{learner_id}".encode("utf-8")).hexdigest()[:48]


def _result(db: Session, learner_id: str, article: Article) -> dict[str, Any] | None:
    session = db.get(LearningSession, _slot_id(learner_id, article))
    if session is None:
        return None
    if session.learner_id != learner_id or session.topic_id != article.topic_id or session.completed_at is None:
        raise HTTPException(409, "无法恢复这篇文章的学习记录。")
    attempts = db.scalars(select(Attempt).where(Attempt.session_id == session.id)).all()
    results = [
        item | {"attempt_id": attempt.id}
        for attempt in attempts for item in attempt.question_snapshot_json.get("practice_results", [])
    ]
    if (len(results) != len(article.items) or {row["item_id"] for row in results} != {item.id for item in article.items}
            or any(a.question_snapshot_json.get("content_version") != article.id for a in attempts)):
        raise HTTPException(409, "已保存的作答与文章版本不一致。")
    timestamp = session.completed_at
    return {
        "session_id": session.id, "content_version": article.id, "rule_version": RULE_VERSION,
        "submitted_at": (timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)).isoformat(),
        "correct_count": sum(row["is_correct"] for row in results),
        "total_count": len(results), "items": sorted(results, key=lambda row: row["number"]),
    }


def _published(db: Session, article: Article) -> bool:
    topic = db.get(KnowledgeTopic, article.topic_id)
    material = db.get(LearningMaterial, article.material_id)
    expected_ids = {row["id"] for row, _ in article.question_rows()}
    question_ids = set(db.scalars(select(Question.id).where(
        Question.id.in_(expected_ids), Question.topic_id == article.topic_id,
        Question.material_id == article.material_id, Question.version == 1,
    )))
    if not (topic and material and question_ids == expected_ids):
        return False
    if (topic.domain_code != DOMAIN or material.topic_id != topic.id or material.version != 1
            or material.content_body != "\n\n".join(article.paragraphs.values())):
        raise HTTPException(409, "已导入的文章与发布版本不一致。")
    return True


def _get_article(db: Session, article_id: str, learner_id: str) -> Article:
    article = reading_articles().get(article_id)
    if article is None:
        raise HTTPException(404, "未找到这篇阅读文章。")
    if _variant(db, learner_id) != "academic":
        raise HTTPException(409, "请先保存 Academic 考试类型。")
    return article


def _article_view(db: Session, article: Article, learner_id: str) -> dict[str, Any]:
    result = _result(db, learner_id, article)
    if not result and not _published(db, article):
        raise HTTPException(503, "这篇文章的题库尚未导入。")
    return {"article": article.public(), "result": result}


@router.get("")
def list_articles(learner_id: Learner = "default_learner", db: Session = Depends(get_db)) -> dict[str, Any]:
    variant = _variant(db, learner_id)
    if variant is None:
        return {"status": "selection_required", "exam_variant": None, "articles": [], "missing_count": 0}
    if variant != "academic":
        return {"status": "empty", "exam_variant": variant, "articles": [], "missing_count": 0}
    available = []
    missing = 0
    for article in reading_articles().values():
        result = _result(db, learner_id, article)
        if not result and not _published(db, article):
            missing += 1
            continue
        available.append(article.summary_view() | {
            "status": "completed" if result else "not_started",
            "correct_count": result["correct_count"] if result else None,
        })
    return {"status": "ready" if available else "unimported" if missing else "empty",
            "exam_variant": variant, "articles": available, "missing_count": missing}


@router.get("/{article_id}")
def get_article(article_id: str, learner_id: Learner = "default_learner",
                db: Session = Depends(get_db)) -> dict[str, Any]:
    return _article_view(db, _get_article(db, article_id, learner_id), learner_id)


def _replay(article: Article, result: dict[str, Any], submitted: dict[str, str]) -> dict[str, Any]:
    if submitted != {row["item_id"]: row["selected_option"] for row in result["items"]}:
        raise HTTPException(409, "本版文章已有正式提交，已保存的答案保持不变。")
    return {"article": article.public(), "result": result}


@router.post("/{article_id}/submit")
def submit_article(article_id: str, payload: SubmitAnswers, learner_id: Learner = "default_learner",
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    # Acquire the SQLite writer lock before the first read to serialize submissions.
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    article = _get_article(db, article_id, learner_id)
    if prior := _result(db, learner_id, article):
        return _replay(article, prior, payload.answers)
    if not _published(db, article):
        raise HTTPException(503, "这篇文章的题库尚未导入。")
    expected = {item.id: item for item in article.items}
    if (set(payload.answers) != set(expected)
            or any(choice not in expected[item_id].options for item_id, choice in payload.answers.items())):
        raise HTTPException(422, "请在本篇文章的每一道题选择一个有效答案。")

    now = utc_now()
    session = LearningSession(id=_slot_id(learner_id, article), learner_id=learner_id,
                              topic_id=article.topic_id, exam_variant="academic",
                              exam_spec_version=EXAM_CATALOG_VERSION, status="completed",
                              started_at=now, updated_at=now, completed_at=now)
    attempts = []
    for row, items in article.question_rows():
        question = db.get(Question, row["id"])
        if (question is None or question.question_type != row["question_type"]
                or question.prompt_text != row["prompt_text"]):
            raise HTTPException(409, "已导入的题目与发布版本不一致。")
        choices = {item.id: payload.answers[item.id] for item in items}
        group = None
        group_evaluation = None
        if "reading_group" in row:
            group = db.get(ReadingGroupVersion, question.reading_group_version_id) if question.reading_group_version_id else None
            rule = db.get(ReadingRuleVersion, group.rule_version_id) if group else None
            if not group or not rule or group.content_version != article.id or group.exam_variant != "academic":
                raise HTTPException(409, "文章的段落标题题版本与题库不一致。")
            display = MatchingHeadingsGroup.model_validate(group.display_json)
            key = HeadingAnswerKey.model_validate(rule.private_rules_json)
            expected_group = row["reading_group"]
            if (display != MatchingHeadingsGroup.model_validate(expected_group["display"])
                    or key != HeadingAnswerKey.model_validate(expected_group["private_rules"])):
                raise HTTPException(409, "文章的段落标题题版本与题库不一致。")
            try:
                score = evaluate_headings(display, key, choices)
            except ValueError as exc:
                raise HTTPException(422, "每个段落标题最多使用一次。") from exc
            correctness = score.correct_by_item
            group_evaluation = ReadingGroupEvaluation(
                **score.model_dump(), group_version_id=group.id,
                content_version=group.content_version, display_schema_version=group.display_schema_version,
                evaluation_rule_version=rule.rules_version, rule_version_id=rule.id,
            ).model_dump(mode="json")
        else:
            item = items[0]
            if question.standard_answer != item.answer or question.evaluation_rubric != row["evaluation_rubric"]:
                raise HTTPException(409, "文章的选择题版本与题库不一致。")
            correctness = {item.id: choices[item.id] == question.standard_answer}
        results = [{
            "item_id": item.id, "number": item.number, "selected_option": choices[item.id],
            "correct_option": item.answer, "is_correct": correctness[item.id], "explanation": item.explanation,
            "evidence": {"material_id": article.material_id, "content_version": article.id,
                         "paragraph": item.paragraph, "quote": item.quote},
        } for item in items]
        attempts.append(Attempt(
            session_id=session.id, question_id=question.id, question_version=question.version,
            question_snapshot_json={
                "activity_type": "reading_article_practice", "content_version": article.id,
                "question_type": question.question_type, "prompt_text": row["prompt_text"],
                "material_id": article.material_id, "items": [item.public() for item in items],
                "practice_results": results,
            },
            submitted_answer="; ".join(choices.values()), structured_answer_json=choices if group else None,
            group_version_id=group.id if group else None, group_snapshot_json=group.display_json if group else None,
            item_evaluation_json=group_evaluation,
            is_correct=all(correctness.values()), evaluation_status="evaluated",
            evaluation_rule_version=RULE_VERSION, attempt_role="initial", submitted_at=now,
        ))
    try:
        db.add(session)
        db.flush()
        db.add_all(attempts)
        db.commit()
    except IntegrityError:
        db.rollback()
        if prior := _result(db, learner_id, article):
            return _replay(article, prior, payload.answers)
        raise
    return _article_view(db, article, learner_id)
