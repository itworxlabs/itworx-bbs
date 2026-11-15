import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Must run before app.db is imported: it builds the engine from DATABASE_URL at import time.
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'test.db'}"

from app.db import engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_tables() -> Iterator[None]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
