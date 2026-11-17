import logging
import re
import time
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import KST, _page_items, app, format_list_date
from app.migrate import BASE_DIR
from app.models import Comment, Post
from app.passwords import hash_password, verify_password
from scripts.seed import main as seed

client = TestClient(app)

BASE_TIME = datetime(2026, 1, 1, 0, 0)
# Unique enough that finding it in a page can only mean the password leaked.
TEST_PASSWORD = "test-pass-9876"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


def add_posts(count: int) -> None:
    with SessionLocal() as db:
        db.add_all(
            Post(
                title=f"글 {i}",
                content="내용",
                author="작성자",
                password_hash=TEST_PASSWORD_HASH,
                created_at=BASE_TIME + timedelta(minutes=i),
            )
            for i in range(1, count + 1)
        )
        db.commit()


def row_count(html: str) -> int:
    return html.count('class="post-row"')


def current_page(html: str) -> str:
    match = re.search(r'aria-current="page"[^>]*>(\d+)<', html)
    assert match
    return match.group(1)


def page_numbers(html: str) -> list[str]:
    nav = html[html.index('<nav aria-label="페이지"') :]
    return re.findall(r">(\d+)</(?:a|strong)>", nav[: nav.index("</nav>")])


def test_empty_list_shows_message() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "등록된 글이 없습니다." in response.text
    assert page_numbers(response.text) == ["1"]
    assert current_page(response.text) == "1"


def test_list_is_paginated_newest_first() -> None:
    add_posts(25)

    first = client.get("/")
    assert first.status_code == 200
    assert row_count(first.text) == 10
    assert first.text.index("글 25") < first.text.index("글 24")
    assert page_numbers(first.text) == ["1", "2", "3"]
    assert current_page(first.text) == "1"
    assert 'href="/?page=2"' in first.text
    assert '<span aria-label="이전 쪽" aria-disabled="true"' in first.text
    assert '<a aria-label="다음 쪽"' in first.text

    last = client.get("/?page=3")
    assert row_count(last.text) == 5
    assert 'href="/?page=2"' in last.text
    assert '<a aria-label="이전 쪽"' in last.text
    assert '<span aria-label="다음 쪽" aria-disabled="true"' in last.text


def test_posts_with_same_created_at_are_newest_id_first() -> None:
    with SessionLocal() as db:
        db.add_all(
            Post(title=f"동시 {i}", content="내용", author="작성자", password_hash=TEST_PASSWORD_HASH, created_at=BASE_TIME)
            for i in range(1, 4)
        )
        db.commit()

    text = client.get("/").text

    assert text.index("동시 3") < text.index("동시 2") < text.index("동시 1")


def test_created_at_defaults_to_utc_now(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-UTC local zone makes a naive datetime.now() default distinguishable from UTC.
    monkeypatch.setenv("TZ", "Asia/Seoul")
    time.tzset()
    try:
        with SessionLocal() as db:
            db.add(Post(title="글", content="내용", author="작성자", password_hash=TEST_PASSWORD_HASH))
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
        sample_hash = db.scalars(select(Post.password_hash).limit(1)).one()
        commented = db.execute(
            select(Post.title, func.count(Comment.id)).join(Post.comments).group_by(Post.id).order_by(Post.title)
        ).all()
        comment_hash = db.scalars(select(Comment.password_hash).limit(1)).one()
    assert verify_password("1234", sample_hash)
    assert commented == [("샘플 글 23", 1), ("샘플 글 24", 2), ("샘플 글 25", 3)]
    assert verify_password("1234", comment_hash)


def test_seed_comments_are_not_dated_in_the_future() -> None:
    seed()
    seeded_at = datetime.now(UTC).replace(tzinfo=None)

    with SessionLocal() as db:
        rows = db.execute(select(Post.created_at, Comment.created_at).join(Post.comments)).all()
    assert rows
    assert all(post_at <= comment_at <= seeded_at for post_at, comment_at in rows)


def test_seed_adds_comments_to_existing_sample_posts_only() -> None:
    add_posts(3)
    with SessionLocal() as db:
        db.add(Post(title="샘플 글 25", content="내용", author="작성자", password_hash=TEST_PASSWORD_HASH))
        db.commit()

    seed()

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Post)) == 4
        commented = db.execute(select(Post.title, func.count(Comment.id)).join(Post.comments).group_by(Post.id)).all()
    assert commented == [("샘플 글 25", 3)]


def add_post_at(created_at: datetime, title: str = "글") -> None:
    with SessionLocal() as db:
        db.add(Post(title=title, content="내용", author="작성자", password_hash=TEST_PASSWORD_HASH, created_at=created_at))
        db.commit()


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def test_created_at_is_shown_in_kst() -> None:
    add_post_at(datetime(2025, 12, 31, 15, 30))

    text = client.get("/").text

    # Stored UTC 2025-12-31 15:30 is already 2026-01-01 in KST (UTC+9).
    assert "26.01.01" in text
    assert "25.12.31" not in text


@pytest.mark.parametrize(
    ("now", "created_at", "shown"),
    [
        # UTC 15:00 is KST midnight.
        (datetime(2026, 1, 1, 15, 0, tzinfo=UTC), datetime(2026, 1, 1, 15, 0), "00:00"),
        (datetime(2026, 1, 1, 15, 0, tzinfo=UTC), datetime(2026, 1, 1, 14, 59), "26.01.01"),
        (datetime(2026, 1, 1, 14, 59, tzinfo=UTC), datetime(2026, 1, 1, 14, 0), "23:00"),
        (datetime(2026, 1, 1, 14, 59, tzinfo=UTC), datetime(2025, 12, 31, 14, 59), "25.12.31"),
    ],
)
def test_list_date_format(now: datetime, created_at: datetime, shown: str) -> None:
    assert format_list_date(created_at, now) == shown


def test_list_shows_time_for_today_and_date_before() -> None:
    now = utc_now()
    add_post_at(now, "오늘 글")
    add_post_at(BASE_TIME, "옛 글")

    text = client.get("/").text

    assert now.replace(tzinfo=UTC).astimezone(KST).strftime("%H:%M") in text
    assert "26.01.01" in text
    assert "2026-01-01 09:" not in text


def test_new_mark_within_24_hours() -> None:
    now = utc_now()
    add_post_at(now - timedelta(hours=23, minutes=59), "새 글")
    add_post_at(now - timedelta(hours=24, minutes=1), "지난 글")

    newer, older = client.get("/").text.split('<tr class="post-row">')[1:]

    assert "새 글</a>" in newer and "new-mark" in newer
    assert "지난 글</a>" in older and "new-mark" not in older


@pytest.mark.parametrize(
    ("page", "last", "expected"),
    [
        (1, 1, [1]),
        (4, 7, [1, 2, 3, 4, 5, 6, 7]),
        (1, 12, [1, 2, 3, 4, None, 12]),
        (3, 12, [1, 2, 3, 4, None, 12]),
        (4, 12, [1, None, 3, 4, 5, None, 12]),
        (6, 12, [1, None, 5, 6, 7, None, 12]),
        (10, 12, [1, None, 9, 10, 11, 12]),
        (12, 12, [1, None, 9, 10, 11, 12]),
    ],
)
def test_page_items_show_ends_and_neighbours(page: int, last: int, expected: list[int | None]) -> None:
    assert _page_items(page, last) == expected


def test_page_numbers_skip_with_ellipsis() -> None:
    add_posts(115)

    first = client.get("/").text
    assert page_numbers(first) == ["1", "2", "3", "4", "12"]
    assert current_page(first) == "1"
    assert '<li aria-hidden="true" class="px-1 text-gray-500">…</li>' in first
    assert 'href="/?page=12" hx-get="/?page=12"' in first
    assert "총 115건" in first

    middle = client.get("/?page=6").text
    assert page_numbers(middle) == ["1", "5", "6", "7", "12"]
    assert 'href="/?page=5" hx-get="/?page=5"' in middle
    assert 'href="/?page=7" hx-get="/?page=7"' in middle

    partial = client.get("/?page=12", headers={"HX-Request": "true"}).text
    assert page_numbers(partial) == ["1", "9", "10", "11", "12"]
    assert current_page(partial) == "12"


def test_total_uses_thousands_separator() -> None:
    add_posts(1234)

    assert "총 1,234건" in client.get("/").text


@pytest.mark.parametrize(("size", "rows", "last"), [("20", 20, "2"), ("50", 25, "1")])
def test_page_size_changes_rows_and_is_kept_in_links(size: str, rows: int, last: str) -> None:
    add_posts(25)

    text = client.get(f"/?size={size}").text
    assert row_count(text) == rows
    assert page_numbers(text)[-1] == last
    assert f'<option value="{size}" selected>{size}개씩</option>' in text
    if last == "2":
        assert f'href="/?page=2&amp;size={size}" hx-get="/?page=2&amp;size={size}"' in text


@pytest.mark.parametrize("size", ["15", "abc", "", "1000"])
def test_unknown_page_size_falls_back_to_ten(size: str) -> None:
    add_posts(25)

    text = client.get("/", params={"size": size}).text
    assert row_count(text) == 10
    assert '<option value="10" selected>10개씩</option>' in text
    assert 'href="/?page=2"' in text

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
    assert current_page(response.text) == "2"
    assert "HX-Request" in response.headers["Vary"]


def test_htmx_history_restore_renders_full_page() -> None:
    response = client.get("/", headers={"HX-Request": "true", "HX-History-Restore-Request": "true"})

    assert "<html" in response.text
    assert 'id="post-list"' in response.text


def test_startup_runs_migrations(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(app) as started:
        response = started.get("/")

    assert response.status_code == 200
    head = ScriptDirectory.from_config(Config(BASE_DIR / "alembic.ini")).get_current_head()
    assert f"DB 마이그레이션: 변경 없음: {head}" in caplog.messages
