"""Create policy_rules table and seed defaults."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_policy_rules"
down_revision = "0004_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_rules",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("conditions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE ON policy_rules TO chatbot_app_role"))
    # Insert two confirmation rules (no JSON params needed)
    op.execute(sa.text(
        "INSERT INTO policy_rules (id, capability, tool_name, conditions_json, action, risk_level, reason, active, version) "
        "VALUES ('always_confirm_booking', 'facility_booking', 'create_booking', '[]'::jsonb, 'REQUIRE_CONFIRMATION', 'HIGH', 'Bookings always require explicit user confirmation.', true, 1)"
    ))
    op.execute(sa.text(
        "INSERT INTO policy_rules (id, capability, tool_name, conditions_json, action, risk_level, reason, active, version) "
        "VALUES ('always_confirm_cancellation', 'facility_booking', 'cancel_booking', '[]'::jsonb, 'REQUIRE_CONFIRMATION', 'HIGH', 'Cancellations always require explicit user confirmation.', true, 1)"
    ))
    # Use jsonb_build_array/object to avoid colon-in-JSON being parsed as bind param
    op.execute(sa.text(
        "INSERT INTO policy_rules (id, capability, tool_name, conditions_json, action, risk_level, reason, active, version) "
        "VALUES ('rate_limit_booking', 'facility_booking', 'create_booking', "
        "jsonb_build_array(jsonb_build_object('type', 'hourly_limit', 'operator', '>', 'value', 5)), "
        "'DENY', 'MEDIUM', 'Users may not create more than 5 bookings per hour.', true, 1)"
    ))


def downgrade() -> None:
    op.drop_table("policy_rules")
