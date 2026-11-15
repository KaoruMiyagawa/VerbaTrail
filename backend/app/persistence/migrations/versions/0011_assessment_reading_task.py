"""Pin reading assessment session, content version and exposure to its task."""
from alembic import op
import sqlalchemy as sa
revision = "0011_assessment_reading_task"
down_revision = "0010_assessment_runs"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("assessment_tasks") as batch:
        batch.add_column(sa.Column("learning_session_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("content_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("repeated_exposure", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key("fk_assessment_tasks_learning_session", "learning_sessions", ["learning_session_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_assessment_tasks_learning_session_id", ["learning_session_id"])

def downgrade():
    with op.batch_alter_table("assessment_tasks") as batch:
        batch.drop_index("ix_assessment_tasks_learning_session_id")
        batch.drop_constraint("fk_assessment_tasks_learning_session", type_="foreignkey")
        batch.drop_column("repeated_exposure")
        batch.drop_column("content_version")
        batch.drop_column("learning_session_id")
