"""SQLAlchemy 2.x ORM models for VerbaTrail Stage 2.

Subject-agnostic schema covering:
- KnowledgeTopic & KnowledgeTopicDependency
- LearningMaterial
- Question
- LearningSession
- Attempt (immutable raw answer snapshot)
- DiagnosticCase
- Hypothesis (supports multiple competing hypotheses & insufficient_evidence)
- DecisionEvent (strictly append-only immutable audit trail)
- EvidenceLink (immutable evidence record with mandatory source tracking)
- KnowledgeState (traceable mastery status requiring transfer verification for 'stable')
- ReviewItem
"""

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.domain.enums import (
    AttemptEvaluationStatus,
    DiagnosticCaseStatus,
    EvidenceRelation,
    EvidenceSourceType,
    HypothesisStatus,
    KnowledgeMasteryStatus,
    ModelCallStatus,
    QuestionRole,
    QuestionType,
    ReviewStatus,
    SessionStatus,
    UncertaintyLevel,
)
from app.domain.exceptions import DomainRuleViolationError, ImmutableRecordError
from app.persistence.database import Base


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Generate a readable prefixed unique identifier."""
    return f"{prefix}_{uuid4().hex[:16]}"


class KnowledgeTopicDependency(Base):
    """Prerequisite relationship between two KnowledgeTopics."""

    __tablename__ = "knowledge_topic_dependencies"
    __table_args__ = (
        CheckConstraint(
            "topic_id != prerequisite_topic_id",
            name="ck_topic_dependency_not_self",
        ),
    )

    topic_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_topics.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    prerequisite_topic_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_topics.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class KnowledgeTopic(Base):
    """Subject-agnostic knowledge topic entity."""

    __tablename__ = "knowledge_topics"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("topic"),
    )
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    domain_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    common_error_patterns: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    materials: Mapped[list["LearningMaterial"]] = relationship(
        back_populates="topic",
        cascade="save-update, merge",
    )
    questions: Mapped[list["Question"]] = relationship(
        back_populates="topic",
        cascade="save-update, merge",
    )
    prerequisites: Mapped[list["KnowledgeTopic"]] = relationship(
        secondary="knowledge_topic_dependencies",
        primaryjoin=id == KnowledgeTopicDependency.topic_id,
        secondaryjoin=id == KnowledgeTopicDependency.prerequisite_topic_id,
        viewonly=False,
    )


class LearningMaterial(Base):
    """Short study material associated with a KnowledgeTopic."""

    __tablename__ = "learning_materials"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("mat"),
    )
    topic_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_topics.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_body: Mapped[str] = mapped_column(Text, nullable=False)
    content_format: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="markdown",
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    topic: Mapped["KnowledgeTopic"] = relationship(back_populates="materials")
    questions: Mapped[list["Question"]] = relationship(back_populates="material")


class ReadingRuleVersion(Base):
    """Private, immutable rule payload. Never include this in a learner DTO."""

    __tablename__ = "reading_rule_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    private_rules_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ReadingGroupVersion(Base):
    """Immutable public content version with a server-only scoring rule reference."""

    __tablename__ = "reading_group_versions"
    __table_args__ = (UniqueConstraint("group_id", "content_hash", name="uq_reading_group_content"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    exam_variant: Mapped[str] = mapped_column(String(32), nullable=False)
    content_version: Mapped[str] = mapped_column(String(64), nullable=False)
    display_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    display_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    rule_version_id: Mapped[str] = mapped_column(ForeignKey("reading_rule_versions.id", ondelete="RESTRICT"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EvidenceValidityEvent(Base):
    """Append-only audit event for whether stored evidence may support learning state."""

    __tablename__ = "evidence_validity_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_evidence_validity_idempotency_key"),
        CheckConstraint(
            "status IN ('eligible', 'invalidated', 'review_required')",
            name="ck_evidence_validity_status",
        ),
        Index("ix_evidence_validity_target", "target_type", "target_id", "audited_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("evv"))
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    group_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("reading_group_versions.id", ondelete="RESTRICT"), nullable=True
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rules_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    replacement_group_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("reading_group_versions.id", ondelete="RESTRICT"), nullable=True
    )
    affected_refs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    audited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Question(Base):
    """Question item supporting initial, diagnostic_followup, targeted_practice, and transfer_check."""

    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_questions_version_positive"),
        CheckConstraint("difficulty >= 1", name="ck_questions_difficulty_positive"),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("q"),
    )
    reading_group_version_id: Mapped[str | None] = mapped_column(ForeignKey("reading_group_versions.id", ondelete="RESTRICT"), nullable=True)
    topic_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_topics.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    material_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("learning_materials.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_question_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
    )
    question_role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=QuestionRole.INITIAL.value,
        index=True,
    )
    question_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=QuestionType.SHORT_TEXT.value,
    )
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    standard_answer: Mapped[str] = mapped_column(Text, nullable=False)
    answer_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_rubric: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    topic: Mapped["KnowledgeTopic"] = relationship(back_populates="questions")
    material: Mapped["LearningMaterial | None"] = relationship(
        back_populates="questions"
    )
    attempts: Mapped[list["Attempt"]] = relationship(back_populates="question")


class LearningSession(Base):
    """Learner session tracking attempts, diagnostic cases, and decision events."""

    __tablename__ = "learning_sessions"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("sess"),
    )
    learner_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="local_default_learner",
        index=True,
    )
    topic_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_topics.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    current_question_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_group_version_id: Mapped[str | None] = mapped_column(ForeignKey("reading_group_versions.id", ondelete="RESTRICT"), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SessionStatus.ACTIVE.value,
    )
    # Null for all pre-IELTS sessions; new IELTS sessions snapshot the selected exam
    # and product's official-spec catalogue version without rewriting history.
    exam_variant: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exam_spec_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    topic: Mapped["KnowledgeTopic"] = relationship()
    current_question: Mapped["Question | None"] = relationship()
    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="session",
        order_by="Attempt.submitted_at",
    )
    diagnostic_cases: Mapped[list["DiagnosticCase"]] = relationship(
        back_populates="session",
        order_by="DiagnosticCase.created_at",
    )
    decision_events: Mapped[list["DecisionEvent"]] = relationship(
        back_populates="session",
        order_by="DecisionEvent.created_at",
    )


class WritingDraft(Base):
    """The editable text and pinned task for a shared learning session."""

    __tablename__ = "writing_drafts"

    session_id: Mapped[str] = mapped_column(ForeignKey("learning_sessions.id"), primary_key=True)
    task_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class WritingRevision(Base):
    """An immutable submission and its reproducible text metrics."""

    __tablename__ = "writing_revisions"
    __table_args__ = (
        UniqueConstraint("session_id", "number", name="uq_writing_revision_number"),
        UniqueConstraint("session_id", "idempotency_key", name="uq_writing_revision_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("writing"))
    session_id: Mapped[str] = mapped_column(ForeignKey("writing_drafts.session_id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_revision_id: Mapped[str | None] = mapped_column(ForeignKey("writing_revisions.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


@event.listens_for(WritingRevision, "before_update")
@event.listens_for(WritingRevision, "before_delete")
def _protect_writing_revision(*_args: Any) -> None:
    raise ImmutableRecordError("Writing submissions are immutable.")


class SpeakingPractice(Base):
    """Metadata for a locally stored recording; never stores audio bytes."""

    __tablename__ = "speaking_practices"

    recording_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("learning_sessions.id"), nullable=False, index=True)
    prompt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    self_check: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evaluation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_assessed")
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False, default="practice_only")


class ExamPreparationProfile(Base):
    """Persisted exam choice and targets; assessed ability remains absent until evidence exists."""

    __tablename__ = "exam_preparation_profiles"
    __table_args__ = (
        CheckConstraint(
            "goal_type = 'band' AND target_overall_band IS NOT NULL AND target_outcome IS NULL "
            "OR goal_type = 'pass' AND target_overall_band IS NULL AND target_outcome = 'pass'",
            name="ck_exam_profile_goal_matches_scoring_system",
        ),
        CheckConstraint(
            "actual_level_status IN ('not_assessed', 'assessed')",
            name="ck_exam_profile_actual_level_status",
        ),
    )

    learner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exam_variant: Mapped[str] = mapped_column(String(32), nullable=False)
    goal_type: Mapped[str] = mapped_column(String(16), nullable=False, default="band")
    target_overall_band: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_skill_bands: Mapped[dict[str, float] | None] = mapped_column(JSON, nullable=True)
    target_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exam_date: Mapped[date | None] = mapped_column(nullable=True)
    actual_level_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_assessed"
    )
    # This is our catalogue snapshot identifier, not an IELTS-issued version label.
    spec_version: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class Attempt(Base):
    """Immutable learner attempt snapshot associated with a Question and LearningSession."""

    __tablename__ = "attempts"
    __table_args__ = (
        Index(
            "uq_review_attempt_session_question",
            "session_id",
            "question_id",
            unique=True,
            sqlite_where=text("attempt_role = 'review'"),
            postgresql_where=text("attempt_role = 'review'"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("att"),
    )
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("learning_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    question_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("questions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    question_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    question_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    attempt_role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=QuestionRole.INITIAL.value,
    )
    submitted_answer: Mapped[str] = mapped_column(Text, nullable=False)
    group_version_id: Mapped[str | None] = mapped_column(ForeignKey("reading_group_versions.id", ondelete="RESTRICT"), nullable=True)
    structured_answer_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    group_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    item_evaluation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evaluation_rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluation_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AttemptEvaluationStatus.PENDING.value,
    )
    evaluation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128),
        unique=True,
        nullable=True,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    session: Mapped["LearningSession"] = relationship(back_populates="attempts")
    question: Mapped["Question"] = relationship(back_populates="attempts")


@event.listens_for(LearningSession.current_question_id, "set")
def _mark_question_assignment(target: LearningSession, _value: Any, _old: Any, _initiator: Any) -> None:
    # An explicit re-dispatch may choose the same question ID again.
    target.__dict__["_reading_assignment_pending"] = True


@event.listens_for(Session, "before_flush")
def _pin_assigned_reading_group(db: Session, _context: Any, _instances: Any) -> None:
    """Pin content when any existing dispatch path changes the current question."""
    for obj in db.new.union(db.dirty):
        if not isinstance(obj, LearningSession):
            continue
        if obj not in db.new and not obj.__dict__.get("_reading_assignment_pending"):
            continue
        question = db.get(Question, obj.current_question_id) if obj.current_question_id else None
        version_id = question.reading_group_version_id if question else None
        if version_id:
            version = db.get(ReadingGroupVersion, version_id)
            if version is None or version.exam_variant != obj.exam_variant or version.question_type != question.question_type:
                raise DomainRuleViolationError("Assigned reading content does not match the session exam variant.")
        obj.current_group_version_id = version_id
        obj.__dict__.pop("_reading_assignment_pending", None)


class DiagnosticCase(Base):
    """Error diagnosis case holding competing hypotheses and uncertainty state."""

    __tablename__ = "diagnostic_cases"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("diag"),
    )
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("learning_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trigger_attempt_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    topic_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_topics.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    current_followup_question_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DiagnosticCaseStatus.COLLECTING_ATTEMPT.value,
    )
    uncertainty_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_intervention: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    session: Mapped["LearningSession"] = relationship(
        back_populates="diagnostic_cases"
    )
    trigger_attempt: Mapped["Attempt"] = relationship()
    topic: Mapped["KnowledgeTopic"] = relationship()
    hypotheses: Mapped[list["Hypothesis"]] = relationship(
        back_populates="diagnostic_case",
        order_by="Hypothesis.created_at",
    )
    decision_events: Mapped[list["DecisionEvent"]] = relationship(
        back_populates="diagnostic_case",
        order_by="DecisionEvent.created_at",
    )


class Hypothesis(Base):
    """Candidate error hypothesis within a DiagnosticCase.

    Multiple competing hypotheses may coexist for the same DiagnosticCase.
    """

    __tablename__ = "hypotheses"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_hypotheses_confidence_range",
        ),
        UniqueConstraint(
            "diagnostic_case_id",
            "error_code",
            name="uq_hypothesis_case_error_code",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("hyp"),
    )
    diagnostic_case_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("diagnostic_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=HypothesisStatus.CANDIDATE.value,
    )
    missing_evidence_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    diagnostic_case: Mapped["DiagnosticCase"] = relationship(
        back_populates="hypotheses"
    )
    evidence_links: Mapped[list["EvidenceLink"]] = relationship(
        back_populates="hypothesis",
        order_by="EvidenceLink.created_at",
    )


class DecisionEvent(Base):
    """Strictly append-only domain decision event (ARCHITECTURE.md Section 3 & 6)."""

    __tablename__ = "decision_events"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("evt"),
    )
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("learning_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    diagnostic_case_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("diagnostic_cases.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty_level: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=UncertaintyLevel.MEDIUM.value,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128),
        unique=True,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    session: Mapped["LearningSession"] = relationship(
        back_populates="decision_events"
    )
    diagnostic_case: Mapped["DiagnosticCase | None"] = relationship(
        back_populates="decision_events"
    )


class EvidenceLink(Base):
    """Immutable evidence record linking a source observation to a Hypothesis."""

    __tablename__ = "evidence_links"
    __table_args__ = (
        CheckConstraint(
            "length(trim(source_ref)) > 0",
            name="ck_evidence_links_source_ref_nonempty",
        ),
        CheckConstraint(
            "length(trim(summary)) > 0",
            name="ck_evidence_links_summary_nonempty",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("evi"),
    )
    hypothesis_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("hypotheses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("attempts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decision_event_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("decision_events.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EvidenceSourceType.INITIAL_ATTEMPT.value,
    )
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    relation: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EvidenceRelation.SUPPORTS.value,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    reliability_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    hypothesis: Mapped["Hypothesis"] = relationship(back_populates="evidence_links")
    attempt: Mapped["Attempt | None"] = relationship()
    decision_event: Mapped["DecisionEvent | None"] = relationship()


class KnowledgeState(Base):
    """Topic-level knowledge mastery state with mandatory decision traceability."""

    __tablename__ = "knowledge_states"
    __table_args__ = (
        UniqueConstraint(
            "learner_id",
            "topic_id",
            name="uq_knowledge_state_learner_topic",
        ),
        CheckConstraint(
            "length(trim(transition_reason)) > 0",
            name="ck_knowledge_states_reason_nonempty",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("kst"),
    )
    learner_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="local_default_learner",
        index=True,
    )
    topic_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_topics.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=KnowledgeMasteryStatus.INSUFFICIENT_EVIDENCE.value,
    )
    unresolved_error_codes: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    last_evidence_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("evidence_links.id", ondelete="RESTRICT"),
        nullable=True,
    )
    last_decision_event_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("decision_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    last_transfer_attempt_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("attempts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    transition_reason: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    topic: Mapped["KnowledgeTopic"] = relationship()
    last_evidence: Mapped["EvidenceLink | None"] = relationship()
    last_decision_event: Mapped["DecisionEvent"] = relationship()
    last_transfer_attempt: Mapped["Attempt | None"] = relationship()


class ReviewItem(Base):
    """Scheduled review task tied to unresolved error patterns or transfer results."""

    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint("schedule_key", name="uq_review_items_schedule_key"),
        UniqueConstraint("review_session_id", name="uq_review_items_review_session_id"),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("rev"),
    )
    learner_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="local_default_learner",
        index=True,
    )
    topic_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_topics.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("learning_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    hypothesis_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("hypotheses.id", ondelete="RESTRICT"),
        nullable=True,
    )
    trigger_reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ReviewStatus.PENDING.value,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    review_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    review_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_session_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("learning_sessions.id", ondelete="RESTRICT"), nullable=True
    )
    question_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("questions.id", ondelete="RESTRICT"), nullable=True
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("attempts.id", ondelete="RESTRICT"), nullable=True
    )
    trigger_attempt_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("attempts.id", ondelete="RESTRICT"), nullable=True
    )
    group_version_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("reading_group_versions.id", ondelete="RESTRICT"), nullable=True
    )
    decision_event_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("decision_events.id", ondelete="RESTRICT"), nullable=True
    )
    knowledge_state_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("knowledge_states.id", ondelete="RESTRICT"), nullable=True
    )
    retention_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retention_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    topic: Mapped["KnowledgeTopic"] = relationship()
    session: Mapped["LearningSession | None"] = relationship(foreign_keys=[session_id])
    review_session: Mapped["LearningSession | None"] = relationship(foreign_keys=[review_session_id])
    hypothesis: Mapped["Hypothesis | None"] = relationship()
    trigger_attempt: Mapped["Attempt | None"] = relationship(foreign_keys=[trigger_attempt_id])


class ModelCall(Base):
    """Append-only redacted audit record of an AI model invocation (ARCHITECTURE.md Section 4 & 7).

    Never stores API keys or raw unredacted secrets.
    """

    __tablename__ = "model_calls"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="ck_model_calls_latency_nonneg"),
        CheckConstraint("total_tokens >= 0", name="ck_model_calls_tokens_nonneg"),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("mcall"),
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("learning_sessions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    diagnostic_case_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("diagnostic_cases.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    decision_event_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("decision_events.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ModelCallStatus.SUCCEEDED.value,
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redacted_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    session: Mapped["LearningSession | None"] = relationship()
    diagnostic_case: Mapped["DiagnosticCase | None"] = relationship()
    decision_event: Mapped["DecisionEvent | None"] = relationship()


# ---------------------------------------------------------------------------
# ORM Immutability & Append-Only Guards (ARCHITECTURE.md Section 3)
# ---------------------------------------------------------------------------


@event.listens_for(DecisionEvent, "before_update")
def _prevent_decision_event_update(
    _mapper: Any, _connection: Any, _target: DecisionEvent
) -> None:
    raise ImmutableRecordError(
        "DecisionEvent is strictly append-only and cannot be modified after creation."
    )


@event.listens_for(DecisionEvent, "before_delete")
def _prevent_decision_event_delete(
    _mapper: Any, _connection: Any, _target: DecisionEvent
) -> None:
    raise ImmutableRecordError(
        "DecisionEvent is strictly append-only and cannot be deleted."
    )


@event.listens_for(EvidenceLink, "before_update")
def _prevent_evidence_link_update(
    _mapper: Any, _connection: Any, _target: EvidenceLink
) -> None:
    raise ImmutableRecordError(
        "EvidenceLink is immutable diagnostic evidence and cannot be overwritten."
    )


@event.listens_for(EvidenceLink, "before_delete")
def _prevent_evidence_link_delete(
    _mapper: Any, _connection: Any, _target: EvidenceLink
) -> None:
    raise ImmutableRecordError(
        "EvidenceLink is immutable diagnostic evidence and cannot be deleted."
    )


@event.listens_for(Attempt, "before_delete")
def _prevent_attempt_delete(
    _mapper: Any, _connection: Any, _target: Attempt
) -> None:
    raise ImmutableRecordError(
        "Attempt records raw learner answers and cannot be deleted."
    )


@event.listens_for(Attempt, "before_update")
def _prevent_attempt_snapshot_mutation(
    _mapper: Any, _connection: Any, target: Attempt
) -> None:
    state = inspect(target)
    immutable_fields = (
        "submitted_answer",
        "question_id",
        "session_id",
        "question_version",
        "question_snapshot_json",
        "attempt_role",
        "submitted_at",
        "idempotency_key",
        "group_version_id",
        "structured_answer_json",
        "group_snapshot_json",
        "evaluation_rule_version",
    )
    for field_name in immutable_fields:
        hist = state.attrs[field_name].history
        if hist.has_changes():
            raise ImmutableRecordError(
                f"Attempt.{field_name} is an immutable snapshot field and cannot be overwritten."
            )

    eval_status_hist = state.attrs["evaluation_status"].history
    old_statuses = eval_status_hist.deleted or [target.evaluation_status]
    if old_statuses and old_statuses[0] == AttemptEvaluationStatus.EVALUATED.value:
        for eval_field in ("evaluation_status", "is_correct", "evaluation_summary", "item_evaluation_json"):
            if state.attrs[eval_field].history.has_changes():
                raise ImmutableRecordError(
                    f"Attempt.{eval_field} cannot be overwritten once evaluation_status is 'evaluated'."
                )


@event.listens_for(ReadingGroupVersion, "before_update")
@event.listens_for(ReadingGroupVersion, "before_delete")
@event.listens_for(ReadingRuleVersion, "before_update")
@event.listens_for(ReadingRuleVersion, "before_delete")
def _prevent_reading_version_mutation(_mapper: Any, _connection: Any, _target: Any) -> None:
    raise ImmutableRecordError("Reading content and rule versions are immutable.")


@event.listens_for(ModelCall, "before_update")
def _prevent_model_call_update(
    _mapper: Any, _connection: Any, _target: ModelCall
) -> None:
    raise ImmutableRecordError(
        "ModelCall audit records are append-only and cannot be modified."
    )


@event.listens_for(ModelCall, "before_delete")
def _prevent_model_call_delete(
    _mapper: Any, _connection: Any, _target: ModelCall
) -> None:
    raise ImmutableRecordError(
        "ModelCall audit records are append-only and cannot be deleted."
    )


@event.listens_for(EvidenceValidityEvent, "before_update")
@event.listens_for(EvidenceValidityEvent, "before_delete")
def _prevent_evidence_validity_event_mutation(
    _mapper: Any, _connection: Any, _target: EvidenceValidityEvent
) -> None:
    raise ImmutableRecordError("Evidence validity audit events are append-only.")

class AssessmentRun(Base):
    """A learner's assessment workflow; evidence remains in the learning records."""
    __tablename__ = "assessment_runs"
    __table_args__ = (
        UniqueConstraint("learner_id", "idempotency_key", name="uq_assessment_run_learner_key"),
        CheckConstraint("status IN ('in_progress', 'completed')", name="ck_assessment_run_status"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("asr"))
    learner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    assessment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="in_progress")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tasks: Mapped[list["AssessmentTask"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class AssessmentTask(Base):
    """Workflow state and future reference to a real learning record."""
    __tablename__ = "assessment_tasks"
    __table_args__ = (
        UniqueConstraint("run_id", "skill_id", name="uq_assessment_task_run_skill"),
        CheckConstraint("skill_id IN ('listening', 'speaking', 'reading', 'writing')", name="ck_assessment_task_skill"),
        CheckConstraint("status IN ('not_started', 'in_progress', 'skipped', 'completed')", name="ck_assessment_task_status"),
        CheckConstraint("status != 'completed' OR (evidence_source IS NOT NULL AND evidence_reference IS NOT NULL)", name="ck_assessment_task_completed_evidence"),
        CheckConstraint("(evidence_source IS NULL AND evidence_reference IS NULL) OR (evidence_source IS NOT NULL AND evidence_reference IS NOT NULL)", name="ck_assessment_task_evidence_pair"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ast"))
    run_id: Mapped[str] = mapped_column(ForeignKey("assessment_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_started")
    learning_session_id: Mapped[str | None] = mapped_column(ForeignKey("learning_sessions.id", ondelete="RESTRICT"), nullable=True, index=True)
    content_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repeated_exposure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run: Mapped["AssessmentRun"] = relationship(back_populates="tasks")


class SpeakingAssessmentRecording(Base):
    """Client-reported registration for one Speaking task, with no audio or evaluation."""

    __tablename__ = "speaking_assessment_recordings"
    __table_args__ = (
        UniqueConstraint("assessment_task_id", name="uq_speaking_assessment_recording_task"),
        CheckConstraint("duration_seconds > 0 AND duration_seconds <= 600", name="ck_speaking_assessment_recording_duration"),
    )
    recording_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assessment_task_id: Mapped[str] = mapped_column(ForeignKey("assessment_tasks.id", ondelete="CASCADE"), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class AssessmentWritingDraft(Base):
    """Mutable draft scoped to exactly one AssessmentTask."""

    __tablename__ = "assessment_writing_drafts"
    __table_args__ = (CheckConstraint("version >= 0", name="ck_assessment_writing_draft_version"),)
    assessment_task_id: Mapped[str] = mapped_column(ForeignKey("assessment_tasks.id", ondelete="CASCADE"), primary_key=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AssessmentWritingSubmission(Base):
    """Append-only original response attached to one version-pinned task."""

    __tablename__ = "assessment_writing_submissions"
    __table_args__ = (
        UniqueConstraint("assessment_task_id", name="uq_assessment_writing_submission_task"),
        UniqueConstraint("assessment_task_id", "idempotency_key", name="uq_assessment_writing_submission_key"),
        CheckConstraint("draft_version >= 0 AND word_count > 0 AND paragraph_count >= 0", name="ck_assessment_writing_submission_metrics"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("aws"))
    assessment_task_id: Mapped[str] = mapped_column(ForeignKey("assessment_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    task_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_count: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


@event.listens_for(AssessmentWritingSubmission, "before_update")
@event.listens_for(AssessmentWritingSubmission, "before_delete")
def _prevent_assessment_writing_submission_mutation(_mapper: Any, _connection: Any, _target: Any) -> None:
    raise ImmutableRecordError("Assessment Writing submissions are immutable evidence records.")


class AssessmentWritingDiagnosticDecision(Base):
    """One durable reservation and outcome per Writing diagnostic phase."""

    __tablename__ = "assessment_writing_diagnostic_decisions"
    __table_args__ = (
        UniqueConstraint("submission_id", "phase", name="uq_writing_diagnostic_phase"),
        UniqueConstraint("submission_id", "idempotency_key", name="uq_writing_diagnostic_key"),
        UniqueConstraint("id", "submission_id", name="uq_writing_diagnostic_id_submission"),
        UniqueConstraint("model_call_id", name="uq_writing_diagnostic_model_call"),
        ForeignKeyConstraint(
            ["submission_id", "required_initial_phase"],
            ["assessment_writing_diagnostic_decisions.submission_id", "assessment_writing_diagnostic_decisions.phase"],
            name="fk_writing_diagnostic_initial", ondelete="CASCADE",
        ),
        CheckConstraint(
            "(phase = 'initial' AND answer_body IS NULL AND required_initial_phase IS NULL) OR "
            "(phase = 'followup' AND answer_body IS NOT NULL AND required_initial_phase IS NOT NULL "
            "AND required_initial_phase = 'initial' AND followup_limit = 1)",
            name="ck_writing_diagnostic_phase_answer",
        ),
        CheckConstraint("reserved_tokens >= 0 AND reserved_tokens <= token_budget AND followup_limit IN (0, 1)",
                        name="ck_writing_diagnostic_reserved_tokens"),
        CheckConstraint(
            "(result IS NULL AND completed_at IS NULL) OR (result IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_writing_diagnostic_finalization",
        ),
        CheckConstraint(
            "(reserved_tokens = 0 AND planned_model IS NULL AND model_call_id IS NULL) OR "
            "(reserved_tokens > 0 AND planned_model IS NOT NULL AND allow_model = true)",
            name="ck_writing_diagnostic_model_reservation",
        ),
        CheckConstraint(
            "completed_at IS NOT NULL OR (reserved_tokens > 0 AND model_call_id IS NULL)",
            name="ck_writing_diagnostic_pending",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("awd"))
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_writing_submissions.id", ondelete="CASCADE"), nullable=False,
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    required_initial_phase: Mapped[str | None] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    allow_model: Mapped[bool] = mapped_column(Boolean, nullable=False)
    answer_body: Mapped[str | None] = mapped_column(Text)
    evidence_version: Mapped[str] = mapped_column(String(64), nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    followup_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_model: Mapped[dict[str, str] | None] = mapped_column(JSON(none_as_null=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    model_call_id: Mapped[str | None] = mapped_column(ForeignKey("model_calls.id", ondelete="RESTRICT"), active_history=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    recover_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), active_history=True)
    model_call: Mapped["ModelCall | None"] = relationship()


@event.listens_for(AssessmentWritingDiagnosticDecision, "before_update")
def _finalize_writing_diagnostic_once(_mapper: Any, _connection: Any, target: Any) -> None:
    state = inspect(target)
    mutable = {"result", "model_call_id", "completed_at", "model_call"}
    if any(attr.history.has_changes() for attr in state.attrs if attr.key not in mutable):
        raise ImmutableRecordError("Writing diagnostic input cannot be changed after reservation.")
    completed = state.attrs.completed_at.history
    model_call = state.attrs.model_call_id.history
    if model_call.has_changes() and (tuple(model_call.deleted) != (None,) or target.model_call_id is None):
        raise ImmutableRecordError("A Writing model call audit can be attached only once.")
    if completed.has_changes() and target.completed_at is not None and tuple(completed.deleted) == (None,):
        return
    if (not completed.has_changes() and not state.attrs.result.history.has_changes()
            and model_call.has_changes() and target.completed_at is not None):
        # An expired request may already have a final local result when its model call returns.
        return
    if any(state.attrs[key].history.has_changes() for key in mutable):
        raise ImmutableRecordError("Writing diagnostic decisions can be finalized only once.")


class AssessmentWritingPractice(Base):
    """A pinned micro exercise and a mutable, versioned revision draft."""

    __tablename__ = "assessment_writing_practices"
    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_assessment_writing_practice_submission"),
        ForeignKeyConstraint(
            ["decision_id", "submission_id"],
            ["assessment_writing_diagnostic_decisions.id", "assessment_writing_diagnostic_decisions.submission_id"],
            name="fk_writing_practice_decision_submission", ondelete="CASCADE",
        ),
        Index("ix_writing_practice_decision_submission", "decision_id", "submission_id"),
        CheckConstraint("draft_version >= 0", name="ck_assessment_writing_practice_version"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("awp"))
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_writing_submissions.id", ondelete="CASCADE"), nullable=False,
    )
    decision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    draft_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


@event.listens_for(AssessmentWritingPractice, "before_update")
def _prevent_writing_practice_plan_mutation(_mapper: Any, _connection: Any, target: Any) -> None:
    state = inspect(target)
    if any(state.attrs[key].history.has_changes() for key in ("id", "submission_id", "decision_id", "plan")):
        raise ImmutableRecordError("A Writing practice plan cannot change after it is pinned.")


class AssessmentWritingPracticeRevision(Base):
    """The immutable final revision for a pinned practice."""

    __tablename__ = "assessment_writing_practice_revisions"
    __table_args__ = (
        UniqueConstraint("practice_id", name="uq_assessment_writing_practice_revision"),
        CheckConstraint("base_version >= 0", name="ck_assessment_writing_revision_version"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("awpr"))
    practice_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_writing_practices.id", ondelete="CASCADE"), nullable=False,
    )
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


@event.listens_for(AssessmentWritingPracticeRevision, "before_update")
@event.listens_for(AssessmentWritingPracticeRevision, "before_delete")
def _prevent_writing_practice_revision_mutation(_mapper: Any, _connection: Any, _target: Any) -> None:
    raise ImmutableRecordError("Final Writing practice revisions are immutable.")


class AssessmentWritingTransfer(Base):
    """A pinned material assignment and its editable Writing transfer draft."""

    __tablename__ = "assessment_writing_transfers"
    __table_args__ = (
        UniqueConstraint("practice_revision_id", name="uq_assessment_writing_transfer_revision"),
        CheckConstraint("draft_version >= 0", name="ck_assessment_writing_transfer_draft_version"),
        Index(
            "uq_writing_transfer_first_exposure", "learner_id", "material_id", unique=True,
            sqlite_where=text("repeated_exposure = false"), postgresql_where=text("repeated_exposure = false"),
        ),
        Index("ix_writing_transfer_learner_material", "learner_id", "material_id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("awt"))
    practice_revision_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_writing_practice_revisions.id", ondelete="CASCADE"), nullable=False,
    )
    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    material_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    repeated_exposure: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    draft_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


@event.listens_for(AssessmentWritingTransfer, "before_update")
def _protect_writing_transfer_assignment(_mapper: Any, _connection: Any, target: Any) -> None:
    editable = {"draft_body", "draft_version", "updated_at"}
    if any(attr.history.has_changes() for attr in inspect(target).attrs if attr.key not in editable):
        raise ImmutableRecordError("The Writing transfer assignment is pinned.")


class AssessmentWritingTransferResponse(Base):
    """One immutable response with deterministic observations per assignment."""

    __tablename__ = "assessment_writing_transfer_responses"
    __table_args__ = (
        UniqueConstraint("transfer_id", name="uq_assessment_writing_transfer_response"),
        CheckConstraint("base_version >= 0", name="ck_assessment_writing_transfer_response_version"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("awtr"))
    transfer_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_writing_transfers.id", ondelete="CASCADE"), nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    observation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


@event.listens_for(AssessmentWritingTransferResponse, "before_update")
@event.listens_for(AssessmentWritingTransferResponse, "before_delete")
def _protect_writing_transfer_response(_mapper: Any, _connection: Any, _target: Any) -> None:
    raise ImmutableRecordError("A formal Writing transfer response is immutable.")
