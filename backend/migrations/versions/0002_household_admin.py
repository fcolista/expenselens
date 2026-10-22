"""Add a household administrator."""
from alembic import op
import sqlalchemy as sa

revision = "0002_household_admin"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE users SET is_admin = TRUE "
        "WHERE id = (SELECT MIN(member.id) FROM users AS member "
        "WHERE member.household_id = users.household_id)"
    )


def downgrade():
    op.drop_column("users", "is_admin")
