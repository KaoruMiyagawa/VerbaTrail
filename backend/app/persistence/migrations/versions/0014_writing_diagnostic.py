"""Add Writing diagnostic decisions without altering the submission or general Agent tables."""

import sqlalchemy as sa
from alembic import op

revision = "0014_writing_diagnostic"
down_revision = "0013_writing_assessment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_writing_diagnostic_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("submission_id", sa.String(64), sa.ForeignKey("assessment_writing_submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("required_initial_phase", sa.String(16)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("allow_model", sa.Boolean(), nullable=False),
        sa.Column("answer_body", sa.Text()),
        sa.Column("evidence_version", sa.String(64), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("followup_limit", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("planned_model", sa.JSON(none_as_null=True)),
        sa.Column("result", sa.JSON(none_as_null=True)),
        sa.Column("model_call_id", sa.String(64), sa.ForeignKey("model_calls.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recover_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("submission_id", "phase", name="uq_writing_diagnostic_phase"),
        sa.UniqueConstraint("submission_id", "idempotency_key", name="uq_writing_diagnostic_key"),
        sa.UniqueConstraint("id", "submission_id", name="uq_writing_diagnostic_id_submission"),
        sa.UniqueConstraint("model_call_id", name="uq_writing_diagnostic_model_call"),
        sa.ForeignKeyConstraint(
            ["submission_id", "required_initial_phase"],
            ["assessment_writing_diagnostic_decisions.submission_id", "assessment_writing_diagnostic_decisions.phase"],
            name="fk_writing_diagnostic_initial", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(phase = 'initial' AND answer_body IS NULL AND required_initial_phase IS NULL) OR "
            "(phase = 'followup' AND answer_body IS NOT NULL AND required_initial_phase IS NOT NULL "
            "AND required_initial_phase = 'initial' AND followup_limit = 1)",
            name="ck_writing_diagnostic_phase_answer",
        ),
        sa.CheckConstraint("reserved_tokens >= 0 AND reserved_tokens <= token_budget AND followup_limit IN (0, 1)",
                           name="ck_writing_diagnostic_reserved_tokens"),
        sa.CheckConstraint(
            "(result IS NULL AND completed_at IS NULL) OR (result IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_writing_diagnostic_finalization",
        ),
        sa.CheckConstraint(
            "(reserved_tokens = 0 AND planned_model IS NULL AND model_call_id IS NULL) OR "
            "(reserved_tokens > 0 AND planned_model IS NOT NULL AND allow_model = true)",
            name="ck_writing_diagnostic_model_reservation",
        ),
        sa.CheckConstraint(
            "completed_at IS NOT NULL OR (reserved_tokens > 0 AND model_call_id IS NULL)",
            name="ck_writing_diagnostic_pending",
        ),
    )


def downgrade() -> None:
    # Diagnostic text and decisions are lost. Shared append-only model audits survive.
    op.drop_table("assessment_writing_diagnostic_decisions")
