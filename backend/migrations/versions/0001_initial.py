"""Initial ExpenseLens schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("households", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("name", sa.String(100), nullable=False))
    op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("email", sa.String(255), nullable=False),
                    sa.Column("password_hash", sa.String(255), nullable=False),
                    sa.Column("display_name", sa.String(100), nullable=False),
                    sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id"), nullable=False),
                    sa.Column("mfa_secret", sa.String(64)), sa.Column("mfa_enabled", sa.Boolean(), nullable=False),
                    sa.Column("recovery_codes", sa.Text()),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table("sessions", sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("csrf_token", sa.String(64), nullable=False))
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_table("categories", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("name", sa.String(60), nullable=False), sa.Column("color", sa.String(7), nullable=False),
                    sa.UniqueConstraint("household_id", "name"))
    op.create_table("tags", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("name", sa.String(40), nullable=False), sa.UniqueConstraint("household_id", "name"))
    op.create_table("expenses", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
                    sa.Column("spent_on", sa.Date(), nullable=False), sa.Column("merchant", sa.String(160), nullable=False),
                    sa.Column("description", sa.String(500), nullable=False),
                    sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id")),
                    sa.Column("payment_method", sa.String(40)), sa.Column("visibility", sa.String(12), nullable=False),
                    sa.Column("review_status", sa.String(12), nullable=False), sa.Column("document_key", sa.String(80)),
                    sa.Column("tax_amount", sa.Numeric(12, 2)),
                    sa.Column("items", sa.JSON(), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_expenses_household_id", "expenses", ["household_id"])
    op.create_index("ix_expenses_spent_on", "expenses", ["spent_on"])
    op.create_table("expense_tags",
                    sa.Column("expense_id", sa.Integer(), sa.ForeignKey("expenses.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("telegram_link_tokens", sa.Column("token_hash", sa.String(64), primary_key=True),
                    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("consumed", sa.Boolean(), nullable=False))
    op.create_table("household_invites", sa.Column("token_hash", sa.String(64), primary_key=True),
                    sa.Column("household_id", sa.Integer(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("consumed", sa.Boolean(), nullable=False))
    op.create_table("telegram_users", sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
                    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                              nullable=False, unique=True))


def downgrade():
    op.drop_table("telegram_users")
    op.drop_table("household_invites")
    op.drop_table("telegram_link_tokens")
    op.drop_table("expense_tags")
    op.drop_index("ix_expenses_spent_on", table_name="expenses")
    op.drop_index("ix_expenses_household_id", table_name="expenses")
    op.drop_table("expenses")
    op.drop_table("tags")
    op.drop_table("categories")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("households")
