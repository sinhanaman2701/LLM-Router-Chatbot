"""Create user_preferences table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_user_preferences"
down_revision = "0002_bookings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("community_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preference_key", sa.Text(), nullable=False),
        sa.Column("preference_value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("last_observed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "preference_key", name="uq_user_preferences_user_id_preference_key"),
    )
    op.create_index("idx_user_preferences_user_id", "user_preferences", ["user_id"])
    op.execute("ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON user_preferences
        USING (community_id = current_setting('app.community_id')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON user_preferences TO chatbot_app_role")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON user_preferences")
    op.drop_index("idx_user_preferences_user_id", table_name="user_preferences")
    op.drop_table("user_preferences")
