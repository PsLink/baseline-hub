from alembic import context
from baseline_hub import models  # noqa: F401
from baseline_hub.config import Settings
from baseline_hub.db import Base, database

config = Settings()
engine, _ = database(config.database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
