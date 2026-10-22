"""Link tags to multiple household categories."""
from alembic import op
import sqlalchemy as sa

revision = "0003_tag_categories"
down_revision = "0002_household_admin"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tag_categories",
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade():
    op.drop_table("tag_categories")
