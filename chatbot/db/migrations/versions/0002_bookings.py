"""Create bookings table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_bookings"
down_revision = "0001_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("external_booking_id", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("facility_id", sa.Text(), nullable=False),
        sa.Column("facility_name", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'Confirmed'")),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'chatbot'")),
        sa.Column("community_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_bookings_user_id", "bookings", ["user_id"])
    op.create_index("idx_bookings_facility_date", "bookings", ["facility_id", "date"])
    op.execute("ALTER TABLE bookings ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON bookings
        USING (community_id = current_setting('app.community_id')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON bookings TO chatbot_app_role")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON bookings")
    op.drop_index("idx_bookings_facility_date", table_name="bookings")
    op.drop_index("idx_bookings_user_id", table_name="bookings")
    op.drop_table("bookings")
