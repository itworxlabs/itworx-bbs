import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

# Must run before app.db is imported: it builds the engine from DATABASE_URL at import time.
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'test.db'}"

from app.db import engine  # noqa: E402
from app.migrate import run_migrations  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    run_migrations()


@pytest.fixture(autouse=True)
def empty_tables(migrated_db: None) -> Iterator[None]:
    # Clearing sqlite_sequence too restarts post ids at 1, so ids like 999 stay missing in every test.
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            connection.execute(table.delete())
        connection.execute(text("DELETE FROM sqlite_sequence"))
    yield
