"""Add backend identity mappings and encrypted per-session credentials."""

from alembic import op
from baseline_hub.models import PlatformCredential, PlatformIdentity

revision = "002_platform_accounts"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade():
    PlatformIdentity.__table__.create(op.get_bind(), checkfirst=True)
    PlatformCredential.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    PlatformCredential.__table__.drop(op.get_bind(), checkfirst=True)
    PlatformIdentity.__table__.drop(op.get_bind(), checkfirst=True)
