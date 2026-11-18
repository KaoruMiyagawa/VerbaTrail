"""Persist isolated Writing assessment drafts and immutable text submissions."""

import sqlalchemy as sa
from alembic import op

revision = "0013_writing_assessment"
down_revision = "0012_speaking_assessment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_writing_drafts",
        sa.Column("assessment_task_id", sa.String(64), sa.ForeignKey("assessment_tasks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 0", name="ck_assessment_writing_draft_version"),
    )
    op.create_table(
        "assessment_writing_submissions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("assessment_task_id", sa.String(64), sa.ForeignKey("assessment_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_version", sa.String(64), nullable=False),
        sa.Column("prompt_id", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("paragraph_count", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.UniqueConstraint("assessment_task_id", name="uq_assessment_writing_submission_task"),
        sa.UniqueConstraint("assessment_task_id", "idempotency_key", name="uq_assessment_writing_submission_key"),
        sa.CheckConstraint("draft_version >= 0 AND word_count > 0 AND paragraph_count >= 0", name="ck_assessment_writing_submission_metrics"),
    )
    op.create_index("ix_assessment_writing_submissions_assessment_task_id", "assessment_writing_submissions", ["assessment_task_id"])


def downgrade() -> None:
    op.execute(
        "UPDATE assessment_tasks SET status='in_progress', evidence_source=NULL, evidence_reference=NULL "
        "WHERE skill_id='writing' AND evidence_source='assessment_writing_submission'"
    )
    op.drop_index("ix_assessment_writing_submissions_assessment_task_id", table_name="assessment_writing_submissions")
    op.drop_table("assessment_writing_submissions")
    op.drop_table("assessment_writing_drafts")
