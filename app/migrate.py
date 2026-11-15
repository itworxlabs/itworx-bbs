from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

from app.db import engine as default_engine

BASE_DIR = Path(__file__).resolve().parent.parent
BASELINE_REVISION = "0001"


def run_migrations(engine: Engine = default_engine) -> str:
    config = Config(BASE_DIR / "alembic.ini")
    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        current = MigrationContext.configure(connection).get_current_revision()
        # An empty alembic_version also counts: pysqlite commits its CREATE TABLE even when
        # the upgrade after the stamp fails and rolls back.
        if current is None and "posts" in tables:
            command.stamp(config, BASELINE_REVISION)
            command.upgrade(config, "head")
            return f"기준선 등록 후 업그레이드: {BASELINE_REVISION} -> {head}"
        if current == head:
            return f"변경 없음: {head}"
        command.upgrade(config, "head")
        if current is None:
            return f"신규 생성: {head}"
        return f"업그레이드: {current} -> {head}"


if __name__ == "__main__":
    print(run_migrations())
