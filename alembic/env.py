"""Only the verified M2-A wrapper may execute online migrations."""
from alembic import context

from supportlens.backend.db.base import Base
from supportlens.backend.db import models  # register tables; no connections

config = context.config
connection = config.attributes.get("verified_connection")
if (context.is_offline_mode() or connection is None
        or config.attributes.get("operation") != "m2a-initial-upgrade"):
    raise RuntimeError("Use supportlens-db migrate after identity and empty-inventory checks. Direct Alembic commands are blocked.")

context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True,
                  compare_server_default=True, version_table_schema="public")
with context.begin_transaction():
    context.run_migrations()
