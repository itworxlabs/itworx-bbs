import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.main import app
from app.models import Base, Post
from scripts.seed import main as seed

client = TestClient(app)

BASE_TIME = datetime(2026, 1, 1, 0, 0)


def add_posts(count: int) -> None:
    with SessionLocal() as db:
        db.add_all(
            Post(title=f"글 {i}", content="내용", author="작성자", created_at=BASE_TIME + timedelta(minutes=i))
            for i in range(1, count + 1)
        )
        db.commit()


def row_count(html: str) -> int:
    return html.count('class="post-row"')


def test_empty_list_shows_message() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "등록된 글이 없습니다." in response.text
    assert "1 / 1" in response.text


def test_list_is_paginated_newest_first() -> None:
    add_posts(25)

    first = client.get("/")
    assert first.status_code == 200
    assert row_count(first.text) == 10
    assert first.text.index("글 25") < first.text.index("글 24")
    assert "1 / 3" in first.text
    assert 'href="/?page=2"' in first.text
    assert "이전" not in first.text

    last = client.get("/?page=3")
    assert row_count(last.text) == 5
    assert 'href="/?page=2"' in last.text
    assert "다음" not in last.text


def test_posts_with_same_created_at_are_newest_id_first() -> None:
    with SessionLocal() as db:
        db.add_all(Post(title=f"동시 {i}", content="내용", author="작성자", created_at=BASE_TIME) for i in range(1, 4))
        db.commit()

    text = client.get("/").text

    assert text.index("동시 3") < text.index("동시 2") < text.index("동시 1")


def test_created_at_defaults_to_utc_now(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-UTC local zone makes a naive datetime.now() default distinguishable from UTC.
    monkeypatch.setenv("TZ", "Asia/Seoul")
    time.tzset()
    try:
        with SessionLocal() as db:
            db.add(Post(title="글", content="내용", author="작성자"))
            db.commit()
    finally:
        monkeypatch.undo()
        time.tzset()

    with SessionLocal() as db:
        stored = db.scalars(select(Post.created_at)).one()
    assert abs(stored - datetime.now(UTC).replace(tzinfo=None)) < timedelta(minutes=1)


def test_seed_adds_samples_only_once() -> None:
    seed()
    seed()

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Post)) == 25


def test_created_at_is_shown_in_kst() -> None:
    add_posts(25)

    response = client.get("/")

    # Stored UTC 2026-01-01 00:25 is shown as KST (UTC+9).
    assert "2026-01-01 09:25" in response.text
    assert "2026-01-01 00:25" not in response.text


@pytest.mark.parametrize(("page", "rows"), [("0", 10), ("-1", 10), ("abc", 10), ("", 10), ("999", 5)])
def test_invalid_page_is_clamped(page: str, rows: int) -> None:
    add_posts(25)

    response = client.get("/", params={"page": page})

    assert response.status_code == 200
    assert row_count(response.text) == rows


def test_htmx_request_renders_only_list() -> None:
    add_posts(25)

    response = client.get("/?page=2", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "htmx.org" not in response.text
    assert 'id="post-list"' in response.text
    assert "2 / 3" in response.text
    assert "HX-Request" in response.headers["Vary"]


def test_htmx_history_restore_renders_full_page() -> None:
    response = client.get("/", headers={"HX-Request": "true", "HX-History-Restore-Request": "true"})

    assert "<html" in response.text
    assert 'id="post-list"' in response.text


def test_startup_creates_tables() -> None:
    Base.metadata.drop_all(engine)

    with TestClient(app) as started:
        response = started.get("/")

    assert response.status_code == 200
