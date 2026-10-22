"""Sessions metadata, budgets, Telegram preferences, passkeys and exchange rates."""
from alembic import op
import sqlalchemy as sa

revision = "0004_features"
down_revision = "0003_tag_categories"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sessions", sa.Column("public_id", sa.String(24), nullable=True))
    op.add_column("sessions", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sessions", sa.Column("user_agent", sa.String(300), nullable=False, server_default=""))
    op.add_column("sessions", sa.Column("method", sa.String(12), nullable=False, server_default="password"))
    op.create_index("ix_sessions_public_id", "sessions", ["public_id"], unique=True)
    op.add_column("categories", sa.Column("monthly_budget", sa.Numeric(12, 2), nullable=True))
    op.add_column("telegram_users", sa.Column("language", sa.String(2), nullable=False, server_default="it"))
    op.add_column("telegram_users", sa.Column("notify_period", sa.String(10), nullable=False,
                                              server_default="off"))
    op.add_column("telegram_users", sa.Column("notify_budget", sa.Boolean(), nullable=False,
                                              server_default=sa.true()))
    op.add_column("telegram_users", sa.Column("last_summary_key", sa.String(12), nullable=False,
                                              server_default=""))
    op.create_table(
        "budget_alerts_sent",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("level", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "passkey_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("credential_id", sa.String(512), nullable=False, unique=True),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(80), nullable=False, server_default="Passkey"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_passkey_credentials_user_id", "passkey_credentials", ["user_id"])
    op.create_table(
        "exchange_rates",
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("rate", sa.Numeric(18, 6), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("exchange_rates")
    op.drop_index("ix_passkey_credentials_user_id", table_name="passkey_credentials")
    op.drop_table("passkey_credentials")
    op.drop_table("budget_alerts_sent")
    for column in ("last_summary_key", "notify_budget", "notify_period", "language"):
        op.drop_column("telegram_users", column)
    op.drop_column("categories", "monthly_budget")
    op.drop_index("ix_sessions_public_id", table_name="sessions")
    for column in ("method", "user_agent", "last_seen_at", "created_at", "public_id"):
        op.drop_column("sessions", column)
