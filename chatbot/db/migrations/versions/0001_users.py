"""Create users table and roles."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_users"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("DO $$ BEGIN CREATE ROLE chatbot_app_role NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.execute("DO $$ BEGIN CREATE ROLE chatbot_audit_reader_role NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.execute("DO $$ BEGIN CREATE ROLE chatbot_admin_role NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("external_user_id", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("community_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unit_id", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'resident'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON users
        USING (community_id = current_setting('app.community_id')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON users TO chatbot_app_role")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    op.drop_table("users")
