from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, inspect, text

from app.db import engine as test_engine
from app.main import app
from app.migrate import BASE_DIR, BASELINE_REVISION, run_migrations
from app.models import Base

HEAD = ScriptDirectory.from_config(Config(BASE_DIR / "alembic.ini")).get_current_head()
# The DDL create_all produced before migrations existed; fixed text so model changes cannot alter it.
PRE_MIGRATION_POSTS_DDL = """
CREATE TABLE posts (
    id INTEGER NOT NULL,
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,
    author VARCHAR(50) NOT NULL,
    password_hash VARCHAR(200) NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id)
)
"""
EXISTING_ROWS = [
    (2, "첫 글", "내용 1", "홍길동", "scrypt$hash-a", "2026-01-01 00:00:00.000000"),
    (7, "둘째 글", "내용\n2", "김철수", "scrypt$hash-b", "2026-01-02 03:04:05.678901"),
    (9, "셋째 글", "내용 3", "이영희", "scrypt$hash-c", "2026-01-03 23:59:59.000000"),
]


def make_engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{tmp_path / 'bbs.db'}")


def make_pre_migration_db(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(PRE_MIGRATION_POSTS_DDL))
        connection.execute(text("INSERT INTO posts VALUES (:a, :b, :c, :d, :e, :f)"), [
            dict(zip("abcdef", row, strict=True)) for row in EXISTING_ROWS
        ])


def rows(engine: Engine) -> list[tuple]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text("SELECT * FROM posts ORDER BY id"))]


def posts_sql(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT sql FROM sqlite_master WHERE name = 'posts'"))


def revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def run_alembic(engine: Engine, action: str, target: str) -> None:
    config = Config(BASE_DIR / "alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        getattr(command, action)(config, target)


def test_models_match_migrations() -> None:
    with test_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    # compare_metadata ignores table options such as sqlite_autoincrement.
    assert "AUTOINCREMENT" in posts_sql(test_engine)


def test_empty_db_is_created_at_head(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)

    assert run_migrations(engine) == f"신규 생성: {HEAD}"
    assert {"posts", "alembic_version"} <= set(inspect(engine).get_table_names())
    assert revision(engine) == HEAD


def test_existing_db_is_stamped_and_upgraded(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    make_pre_migration_db(engine)

    assert run_migrations(engine) == f"기준선 등록 후 업그레이드: {BASELINE_REVISION} -> {HEAD}"
    assert rows(engine) == EXISTING_ROWS
    assert revision(engine) == HEAD
    assert "AUTOINCREMENT" in posts_sql(engine)


def test_failed_upgrade_after_stamp_recovers_on_next_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    make_pre_migration_db(engine)

    def fail(*args: object) -> None:
        raise RuntimeError("upgrade interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(command, "upgrade", fail)
        with pytest.raises(RuntimeError):
            run_migrations(engine)
    assert "alembic_version" in inspect(engine).get_table_names()
    assert revision(engine) is None

    assert run_migrations(engine) == f"기준선 등록 후 업그레이드: {BASELINE_REVISION} -> {HEAD}"
    assert rows(engine) == EXISTING_ROWS
    assert revision(engine) == HEAD


def test_second_run_changes_nothing(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    make_pre_migration_db(engine)
    run_migrations(engine)
    sql = posts_sql(engine)

    assert run_migrations(engine) == f"변경 없음: {HEAD}"
    assert posts_sql(engine) == sql
    assert rows(engine) == EXISTING_ROWS


def test_downgrade_and_upgrade_keep_rows(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    make_pre_migration_db(engine)
    run_migrations(engine)

    run_alembic(engine, "downgrade", BASELINE_REVISION)
    assert "AUTOINCREMENT" not in posts_sql(engine)
    assert rows(engine) == EXISTING_ROWS

    run_alembic(engine, "upgrade", "head")
    assert "AUTOINCREMENT" in posts_sql(engine)
    assert rows(engine) == EXISTING_ROWS


def test_deleted_highest_id_is_not_reused() -> None:
    client = TestClient(app)
    form = {"title": "제목", "author": "작성자", "content": "내용", "password": "pass1234"}

    locations = [client.post("/posts", data=form, follow_redirects=False).headers["location"] for _ in range(3)]
    assert locations == ["/posts/1?created=1", "/posts/2?created=1", "/posts/3?created=1"]

    deleted = client.post("/posts/3/delete", data={"password": "pass1234"}, follow_redirects=False)
    assert deleted.status_code == 303

    created = client.post("/posts", data=form, follow_redirects=False)
    assert created.headers["location"] == "/posts/4?created=1"
