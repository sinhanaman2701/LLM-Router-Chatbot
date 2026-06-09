"""Create policy_change_log table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_policy_change_log"
down_revision = "0005_policy_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_change_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("old_value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("new_value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("changed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute("GRANT INSERT ON policy_change_log TO chatbot_admin_role")
    op.execute("GRANT SELECT ON policy_change_log TO chatbot_audit_reader_role")


def downgrade() -> None:
    op.drop_table("policy_change_log")
