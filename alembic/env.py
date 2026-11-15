from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from app.db import engine
from app.models import Base

config = context.config

# Only the alembic CLI configures logging from alembic.ini. app.migrate passes its connection
# and must leave the server's logging (uvicorn's handlers) untouched.
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with engine.connect() as connection:
        _run(connection)


run_migrations_online()
