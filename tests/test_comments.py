from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.db import SessionLocal, engine
from app.main import app
from app.models import Comment
from app.passwords import verify_password
from tests.test_post_detail import add_post
from tests.test_post_edit_delete import MISMATCH, REQUIRED, assert_no_secret, refuse_hashing, stored
from tests.test_posts import TEST_PASSWORD, TEST_PASSWORD_HASH

client = TestClient(app)

COMMENT_TIME = datetime(2026, 1, 1, 0, 25)
PASSWORD_LENGTH_MESSAGE = "비밀번호는 4자 이상 64자 이하로 입력해 주세요."
COMMENT_NOT_FOUND = "댓글을 찾을 수 없습니다."


def add_comment(
    post_id: int, author: str = "방문자", content: str = "댓글 내용", created_at: datetime = COMMENT_TIME
) -> int:
    with SessionLocal() as db:
        comment = Comment(
            post_id=post_id,
            author=author,
            content=content,
            password_hash=TEST_PASSWORD_HASH,
            created_at=created_at,
        )
        db.add(comment)
        db.commit()
        return comment.id


def comment_form(**overrides: str) -> dict[str, str]:
    return {"author": "방문자", "content": "댓글 내용", "password": TEST_PASSWORD} | overrides


def stored_comments(post_id: int | None = None) -> list[Comment]:
    query = select(Comment).order_by(Comment.id)
    if post_id is not None:
        query = query.where(Comment.post_id == post_id)
    with SessionLocal() as db:
        return list(db.scalars(query))


def assert_input_empty(html: str, input_id: str) -> None:
    start = html.index(f'id="{input_id}"')
    assert "value=" not in html[html.rindex("<input", 0, start) : html.index(">", start)]


def test_create_comment_redirects_and_shows_it() -> None:
    post_id = add_post()

    response = client.post(
        f"/posts/{post_id}/comments",
        data=comment_form(author="  홍길동 ", content="\n첫 댓글 \n", password=" ab "),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/posts/{post_id}?commented=1#comments"
    [comment] = stored_comments(post_id)
    assert (comment.author, comment.content) == ("홍길동", "첫 댓글")
    assert verify_password(" ab ", comment.password_hash)
    assert not verify_password("ab", comment.password_hash)
    assert abs(comment.created_at - datetime.now(UTC).replace(tzinfo=None)) < timedelta(minutes=1)
    text = client.get(response.headers["location"]).text
    assert 'role="status"' in text
    assert text.index('id="comments"') < text.index("댓글이 등록되었습니다.")
    assert "댓글 1개" in text
    assert "홍길동 · " in text
    assert ">첫 댓글</div>" in text


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("author", "", "작성자를 입력해 주세요."),
        ("author", "   ", "작성자를 입력해 주세요."),
        ("author", "a" * 51, "작성자는 50자 이하로 입력해 주세요."),
        ("content", "", "내용을 입력해 주세요."),
        ("content", " \n ", "내용을 입력해 주세요."),
        ("content", "a" * 1001, "내용은 1000자 이하로 입력해 주세요."),
        ("password", "", "비밀번호를 입력해 주세요."),
        ("password", "abc", PASSWORD_LENGTH_MESSAGE),
        ("password", "a" * 65, PASSWORD_LENGTH_MESSAGE),
    ],
)
def test_invalid_comment_is_400_and_keeps_input(field: str, value: str, message: str) -> None:
    post_id = add_post(title="댓글 달 글")
    typed = {"author": " 입력한 작성자 ", "content": " 입력한 내용 "}

    response = client.post(f"/posts/{post_id}/comments", data=comment_form(**typed) | {field: value})

    assert response.status_code == 400
    assert message in response.text
    assert f'id="comment-{field}-error"' in response.text
    assert "댓글을 등록하지 못했습니다. 아래 항목을 확인해 주세요." in response.text
    assert ">댓글 달 글</h1>" in response.text
    if field != "author":
        assert 'value=" 입력한 작성자 "' in response.text
    if field != "content":
        assert "> 입력한 내용 </textarea>" in response.text
    assert_input_empty(response.text, "comment-password")
    assert stored_comments() == []


def test_comment_accepts_max_lengths() -> None:
    post_id = add_post()

    response = client.post(
        f"/posts/{post_id}/comments",
        data=comment_form(author="a" * 50, content="b" * 1000, password="c" * 64),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert len(stored_comments(post_id)) == 1


@pytest.mark.parametrize("raw_id", ["999", "abc", "0"])
def test_comment_on_missing_post_is_404(raw_id: str) -> None:
    response = client.post(f"/posts/{raw_id}/comments", data=comment_form())

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "글을 찾을 수 없습니다." in response.text
    assert stored_comments() == []


def test_detail_without_comments() -> None:
    post_id = add_post()

    text = client.get(f"/posts/{post_id}").text

    assert 'id="comments"' in text
    assert "댓글 0개" in text
    assert "아직 댓글이 없습니다." in text
    assert f'action="/posts/{post_id}/comments#comments"' in text
    assert 'id="comment-password-help"' in text
    assert_input_empty(text, "comment-password")


def test_detail_lists_comments_oldest_first_in_kst() -> None:
    post_id = add_post()
    ids = [
        add_comment(post_id, content=f"댓글 {n}", created_at=COMMENT_TIME + timedelta(minutes=n)) for n in (3, 2, 1)
    ]

    text = client.get(f"/posts/{post_id}").text

    assert "댓글 3개" in text
    assert text.index("댓글 1<") < text.index("댓글 2<") < text.index("댓글 3<")
    assert "2026-01-01 09:26" in text
    for comment_id in ids:
        assert f'href="/posts/{post_id}/comments/{comment_id}/delete"' in text


def test_comment_content_keeps_newlines() -> None:
    post_id = add_post()
    add_comment(post_id, content="첫 줄\n  둘째 줄")

    text = client.get(f"/posts/{post_id}").text

    assert '<div class="mt-1 whitespace-pre-wrap wrap-anywhere">첫 줄\n  둘째 줄</div>' in text


def test_delete_form_asks_for_confirmation() -> None:
    post_id = add_post()
    comment_id = add_comment(post_id, author="홍길동", content="가" * 60)

    response = client.get(f"/posts/{post_id}/comments/{comment_id}/delete")

    assert response.status_code == 200
    assert "이 댓글을 삭제하시겠습니까?" in response.text
    assert "작성자: 홍길동" in response.text
    assert "가" * 47 + "..." in response.text
    assert "가" * 60 not in response.text
    assert f'action="/posts/{post_id}/comments/{comment_id}/delete"' in response.text
    assert f'href="/posts/{post_id}#comments"' in response.text
    assert "댓글을 쓸 때 정한 비밀번호를 입력해 주세요." in response.text
    assert_input_empty(response.text, "password")


def test_delete_removes_only_that_comment() -> None:
    post_id = add_post()
    comment_id = add_comment(post_id, content="지울 댓글")
    kept_id = add_comment(post_id, content="남는 댓글")

    response = client.post(
        f"/posts/{post_id}/comments/{comment_id}/delete", data={"password": TEST_PASSWORD}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/posts/{post_id}?comment_deleted=1#comments"
    assert [comment.id for comment in stored_comments()] == [kept_id]
    text = client.get(response.headers["location"]).text
    assert text.index('id="comments"') < text.index("댓글이 삭제되었습니다.")
    assert "지울 댓글" not in text
    assert "남는 댓글" in text


def test_delete_with_empty_password_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    post_id = add_post()
    comment_id = add_comment(post_id)
    monkeypatch.setattr("app.main.verify_password", refuse_hashing)

    response = client.post(f"/posts/{post_id}/comments/{comment_id}/delete", data={"password": ""})

    assert response.status_code == 400
    assert REQUIRED in response.text
    assert MISMATCH not in response.text
    assert 'aria-describedby="password-error password-help"' in response.text
    assert len(stored_comments()) == 1


@pytest.mark.parametrize("password", ["wrong-password", "   "])
def test_delete_with_wrong_password_is_403(password: str) -> None:
    post_id = add_post()
    comment_id = add_comment(post_id)

    response = client.post(f"/posts/{post_id}/comments/{comment_id}/delete", data={"password": password})

    assert response.status_code == 403
    assert MISMATCH in response.text
    assert REQUIRED not in response.text
    assert 'id="password-error"' in response.text
    assert len(stored_comments()) == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("raw_id", ["999", "abc", "0"])
def test_delete_on_missing_post_is_404(method: str, raw_id: str) -> None:
    comment_id = add_comment(add_post())

    response = client.request(method, f"/posts/{raw_id}/comments/{comment_id}/delete", data={"password": TEST_PASSWORD})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "글을 찾을 수 없습니다." in response.text
    assert len(stored_comments()) == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("raw_id", ["999", "abc", "0", "other"])
def test_missing_comment_is_404(method: str, raw_id: str) -> None:
    post_id = add_post()
    other_comment_id = add_comment(add_post())
    comment_id = other_comment_id if raw_id == "other" else raw_id

    response = client.request(method, f"/posts/{post_id}/comments/{comment_id}/delete", data={"password": TEST_PASSWORD})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert COMMENT_NOT_FOUND in response.text
    assert f'href="/posts/{post_id}#comments"' in response.text
    assert [comment.id for comment in stored_comments()] == [other_comment_id]


def test_deleting_post_deletes_its_comments() -> None:
    post_id = add_post()
    add_comment(post_id)
    add_comment(post_id)
    other_post_id = add_post()
    kept_id = add_comment(other_post_id)

    response = client.post(f"/posts/{post_id}/delete", data={"password": TEST_PASSWORD}, follow_redirects=False)

    assert response.status_code == 303
    assert stored(post_id) is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Comment.id)).where(Comment.post_id == post_id)) == 0
    assert [comment.id for comment in stored_comments()] == [kept_id]


def test_list_shows_comment_count() -> None:
    commented_id = add_post(title="댓글 있는 글")
    for _ in range(3):
        add_comment(commented_id)
    quiet_id = add_post(title="댓글 없는 글")

    text = client.get("/").text

    def title_cell(post_id: int) -> str:
        return text[text.index(f'href="/posts/{post_id}"') :].split("</td>", 1)[0]

    assert f'href="/posts/{commented_id}">댓글 있는 글</a>' in text
    assert "[3]" in title_cell(commented_id)
    assert "[" not in title_cell(quiet_id)


def statements_for_index() -> int:
    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get("/").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(statements)


def test_list_counts_comments_without_query_per_post() -> None:
    add_comment(add_post())
    few = statements_for_index()

    for _ in range(9):
        post_id = add_post()
        add_comment(post_id)
        add_comment(post_id)

    assert statements_for_index() == few


def test_comments_are_escaped() -> None:
    script = "<script>alert(1)</script>"
    escaped = "&lt;script&gt;alert(1)&lt;/script&gt;"
    post_id = add_post()
    comment_id = add_comment(post_id, author=script, content=script)

    pages = [
        client.get(f"/posts/{post_id}"),
        client.get(f"/posts/{post_id}/comments/{comment_id}/delete"),
        client.post(f"/posts/{post_id}/comments", data=comment_form(author=script, content=script, password="")),
    ]

    for page in pages:
        assert escaped in page.text
        assert script not in page.text


def test_comment_password_and_hash_are_never_shown() -> None:
    post_id = add_post()
    comment_id = add_comment(post_id)
    delete_path = f"/posts/{post_id}/comments/{comment_id}/delete"
    pages = [
        client.get(f"/posts/{post_id}"),
        client.post(f"/posts/{post_id}/comments", data=comment_form(author="")),
        client.post(f"/posts/{post_id}/comments", data=comment_form(content="")),
        client.post(f"/posts/{post_id}/comments", data=comment_form(password="a" * 65)),
        client.get(delete_path),
        client.post(delete_path, data={"password": ""}),
        client.post(delete_path, data={"password": "wrong-password"}),
        client.get("/"),
    ]

    for page in pages:
        assert_no_secret(page.text)
