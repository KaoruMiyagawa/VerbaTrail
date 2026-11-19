"""Add Writing transfer assignments and immutable responses."""

import sqlalchemy as sa
from alembic import op

revision = "0016_writing_transfer"
down_revision = "0015_writing_practice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_writing_transfers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("practice_revision_id", sa.String(64), sa.ForeignKey("assessment_writing_practice_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("learner_id", sa.String(64), nullable=False),
        sa.Column("material_id", sa.String(64), nullable=False),
        sa.Column("task_snapshot", sa.JSON(), nullable=False),
        sa.Column("repeated_exposure", sa.Boolean(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("draft_body", sa.Text(), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("practice_revision_id", name="uq_assessment_writing_transfer_revision"),
        sa.CheckConstraint("draft_version >= 0", name="ck_assessment_writing_transfer_draft_version"),
    )
    op.create_index(
        "uq_writing_transfer_first_exposure", "assessment_writing_transfers", ["learner_id", "material_id"],
        unique=True, sqlite_where=sa.text("repeated_exposure = false"), postgresql_where=sa.text("repeated_exposure = false"),
    )
    op.create_index("ix_writing_transfer_learner_material", "assessment_writing_transfers", ["learner_id", "material_id"])
    op.create_table(
        "assessment_writing_transfer_responses",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("transfer_id", sa.String(64), sa.ForeignKey("assessment_writing_transfers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation", sa.JSON(), nullable=False),
        sa.UniqueConstraint("transfer_id", name="uq_assessment_writing_transfer_response"),
        sa.CheckConstraint("base_version >= 0", name="ck_assessment_writing_transfer_response_version"),
    )


def downgrade() -> None:
    op.drop_table("assessment_writing_transfer_responses")
    op.drop_index("ix_writing_transfer_learner_material", table_name="assessment_writing_transfers")
    op.drop_index("uq_writing_transfer_first_exposure", table_name="assessment_writing_transfers")
    op.drop_table("assessment_writing_transfers")
