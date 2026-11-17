"""Register client-reported Speaking assessment recordings separately from practice."""

import sqlalchemy as sa
from alembic import op

revision = "0012_speaking_assessment"
down_revision = "0011_assessment_reading_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speaking_assessment_recordings",
        sa.Column("recording_id", sa.String(64), primary_key=True),
        sa.Column("assessment_task_id", sa.String(64), sa.ForeignKey("assessment_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.UniqueConstraint("assessment_task_id", name="uq_speaking_assessment_recording_task"),
        sa.CheckConstraint("duration_seconds > 0 AND duration_seconds <= 600", name="ck_speaking_assessment_recording_duration"),
    )


def downgrade() -> None:
    # Remove references before dropping the evidence table. The run and other skill tasks survive.
    op.execute(
        "UPDATE assessment_tasks SET status = 'not_started', content_version = NULL, "
        "repeated_exposure = FALSE, evidence_source = NULL, evidence_reference = NULL "
        "WHERE skill_id = 'speaking' AND content_version = 'speaking-initial-community-skill-v1'"
    )
    op.drop_table("speaking_assessment_recordings")
