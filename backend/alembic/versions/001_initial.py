"""Standalone Hub users, sessions, tracks and durable submissions."""

from alembic import op
from baseline_hub import models  # noqa: F401
from baseline_hub.db import Base

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind())


def downgrade():
    Base.metadata.drop_all(op.get_bind())
