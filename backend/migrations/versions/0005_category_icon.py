"""Optional icon for categories."""
from alembic import op
import sqlalchemy as sa

revision = "0005_category_icon"
down_revision = "0004_features"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("categories", sa.Column("icon", sa.String(24), nullable=False, server_default=""))
    for name, icon in {"Casa": "home", "Alimentari": "cart", "Trasporti": "car",
                       "Salute": "heart", "Utenze": "bolt", "Altro": "tag"}.items():
        op.execute(sa.text("UPDATE categories SET icon = :icon WHERE name = :name").bindparams(
            icon=icon, name=name))


def downgrade():
    op.drop_column("categories", "icon")
