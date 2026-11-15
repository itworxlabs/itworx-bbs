from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Post
from tests.test_posts import TEST_PASSWORD, TEST_PASSWORD_HASH

client = TestClient(app)


def add_post(title: str = "제목", author: str = "작성자", content: str = "내용") -> int:
    with SessionLocal() as db:
        post = Post(
            title=title,
            author=author,
            content=content,
            password_hash=TEST_PASSWORD_HASH,
            created_at=datetime(2026, 1, 1, 0, 25),
        )
        db.add(post)
        db.commit()
        return post.id


def test_detail_shows_post() -> None:
    post_id = add_post(title="상세 제목", author="홍길동", content="상세 내용")

    response = client.get(f"/posts/{post_id}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    for fragment in ("<h1>상세 제목</h1>", "홍길동", "상세 내용", "2026-01-01 09:25"):
        assert fragment in response.text
    assert "2026-01-01 00:25" not in response.text
    assert "<title>상세 제목 - itworx-bbs</title>" in response.text
    assert '<a href="/">목록으로</a>' in response.text
    assert '<a href="/posts/new">글쓰기</a>' in response.text
    assert f'<a href="/posts/{post_id}/edit">수정</a>' in response.text
    assert f'<a href="/posts/{post_id}/delete">삭제</a>' in response.text


def test_detail_preserves_newlines_with_pre_wrap() -> None:
    content = "첫 줄\n둘째  줄\n\n넷째 줄"
    post_id = add_post(content=content)

    text = client.get(f"/posts/{post_id}").text

    assert f'<div class="post-content" style="white-space: pre-wrap; overflow-wrap: anywhere">{content}</div>' in text
    assert "<br" not in text


def test_detail_escapes_html() -> None:
    script = "<script>alert(1)</script>"
    post_id = add_post(title=script, content=script)

    text = client.get(f"/posts/{post_id}").text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert script not in text


@pytest.mark.parametrize(
    "raw_id",
    ["999", "abc", "0", "-1", "99999999999999999999999999", "1" * 4301, "0" * 4301, "0" * 5001 + "5"],
)
def test_detail_not_found(raw_id: str) -> None:
    add_post()

    response = client.get(f"/posts/{raw_id}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "글을 찾을 수 없습니다." in response.text
    assert '<a href="/">목록으로</a>' in response.text
    assert "htmx.org" in response.text


def test_detail_accepts_leading_zeros() -> None:
    post_id = add_post(title="앞자리 0")

    response = client.get(f"/posts/{post_id:04d}")

    assert response.status_code == 200
    assert "<h1>앞자리 0</h1>" in response.text


def test_other_404_unchanged() -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_list_title_links_to_detail() -> None:
    post_id = add_post(title="링크 제목")

    assert f'<a href="/posts/{post_id}">링크 제목</a>' in client.get("/").text


def test_create_redirects_to_detail() -> None:
    shown = {"title": "등록 제목", "author": "등록자", "content": "등록 내용"}

    response = client.post("/posts", data=shown | {"password": TEST_PASSWORD}, follow_redirects=False)

    with SessionLocal() as db:
        new_id = db.scalars(select(Post.id).where(Post.title == "등록 제목")).one()
    assert response.status_code == 303
    assert response.headers["location"] == f"/posts/{new_id}"
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    for value in shown.values():
        assert value in detail.text
    assert TEST_PASSWORD not in detail.text
