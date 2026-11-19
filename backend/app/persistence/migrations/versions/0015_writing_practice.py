"""Add Writing micro exercises and final revisions to the D4.2 schema."""

import sqlalchemy as sa
from alembic import op

revision = "0015_writing_practice"
down_revision = "0014_writing_diagnostic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_writing_practices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("submission_id", sa.String(64), sa.ForeignKey("assessment_writing_submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_id", sa.String(64), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("draft_body", sa.Text(), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("submission_id", name="uq_assessment_writing_practice_submission"),
        sa.ForeignKeyConstraint(
            ["decision_id", "submission_id"],
            ["assessment_writing_diagnostic_decisions.id", "assessment_writing_diagnostic_decisions.submission_id"],
            name="fk_writing_practice_decision_submission", ondelete="CASCADE",
        ),
        sa.CheckConstraint("draft_version >= 0", name="ck_assessment_writing_practice_version"),
    )
    op.create_index("ix_writing_practice_decision_submission", "assessment_writing_practices", ["decision_id", "submission_id"])
    op.create_table(
        "assessment_writing_practice_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("practice_id", sa.String(64), sa.ForeignKey("assessment_writing_practices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("feedback", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("practice_id", name="uq_assessment_writing_practice_revision"),
        sa.CheckConstraint("base_version >= 0", name="ck_assessment_writing_revision_version"),
    )


def downgrade() -> None:
    op.drop_table("assessment_writing_practice_revisions")
    op.drop_index("ix_writing_practice_decision_submission", table_name="assessment_writing_practices")
    op.drop_table("assessment_writing_practices")
