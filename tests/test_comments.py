from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select

from app.db import SessionLocal, engine
from app.main import app
from app.models import Comment, Post
from app.passwords import verify_password
from tests.test_post_detail import add_post
from tests.test_post_edit_delete import MISMATCH, REQUIRED, assert_no_secret, refuse_hashing
from tests.test_posts import TEST_PASSWORD, TEST_PASSWORD_HASH

client = TestClient(app)

PASSWORD_LENGTH_MESSAGE = "비밀번호는 4자 이상 64자 이하로 입력해 주세요."


def add_comment(
    post_id: int, author: str = "댓글러", content: str = "댓글 내용", created_at: datetime = datetime(2026, 1, 1, 0, 25)
) -> int:
    with SessionLocal() as db:
        comment = Comment(
            post_id=post_id, author=author, content=content, password_hash=TEST_PASSWORD_HASH, created_at=created_at
        )
        db.add(comment)
        db.commit()
        return comment.id


def comment_form(**overrides: str) -> dict[str, str]:
    return {"author": "댓글러", "content": "댓글 내용", "password": TEST_PASSWORD} | overrides


def stored_comments(post_id: int | None = None) -> list[Comment]:
    query = select(Comment).order_by(Comment.id)
    if post_id is not None:
        query = query.where(Comment.post_id == post_id)
    with SessionLocal() as db:
        return list(db.scalars(query))


def assert_input_empty(html: str, input_id: str) -> None:
    start = html.index(f'<input type="password" id="{input_id}"')
    assert "value=" not in html[start : html.index(">", start)]


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
    assert abs(comment.created_at - datetime.now(UTC).replace(tzinfo=None)) < timedelta(minutes=1)
    detail = client.get(response.headers["location"]).text
    comments_section = detail.split('id="comments"')[1]
    assert 'role="status"' in comments_section
    assert "댓글이 등록되었습니다." in comments_section
    assert "댓글 1개" in detail
    assert "홍길동" in detail
    assert "첫 댓글" in detail


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("author", "", "작성자를 입력해 주세요."),
        ("author", "   ", "작성자를 입력해 주세요."),
        ("author", "a" * 51, "작성자는 50자 이하로 입력해 주세요."),
        ("content", "", "내용을 입력해 주세요."),
        ("content", "   ", "내용을 입력해 주세요."),
        ("content", "a" * 1001, "내용은 1000자 이하로 입력해 주세요."),
        ("password", "", "비밀번호를 입력해 주세요."),
        ("password", "abc", PASSWORD_LENGTH_MESSAGE),
        ("password", "a" * 65, PASSWORD_LENGTH_MESSAGE),
    ],
)
def test_create_comment_rejects_invalid(field: str, value: str, message: str) -> None:
    post_id = add_post(title="댓글 달 글")
    typed = {"author": " 입력한 작성자 ", "content": "입력한\n내용"}

    response = client.post(f"/posts/{post_id}/comments", data=comment_form(**typed) | {field: value})

    assert response.status_code == 400
    text = response.text
    assert message in text
    assert f'id="comment-{field}-error"' in text
    assert "댓글을 등록하지 못했습니다. 아래 항목을 확인해 주세요." in text
    assert ">댓글 달 글</h1>" in text
    if field != "author":
        assert 'value=" 입력한 작성자 "' in text
    if field != "content":
        assert ">입력한\n내용</textarea>" in text
    assert_input_empty(text, "comment-password")
    assert stored_comments() == []


def test_create_comment_accepts_max_lengths() -> None:
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
    add_post()

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
    assert "댓글을 삭제할 때 필요합니다. 4~64자로 입력해 주세요." in text


def test_detail_lists_comments_oldest_first_in_kst() -> None:
    post_id = add_post()
    ids = [
        add_comment(post_id, content=f"순서 {i}", created_at=datetime(2026, 1, 1, 0, 25) + timedelta(minutes=i))
        for i in (3, 2, 1)
    ]

    text = client.get(f"/posts/{post_id}").text

    assert "댓글 3개" in text
    assert "아직 댓글이 없습니다." not in text
    assert text.count('class="comment py-3"') == 3
    assert text.index("순서 1") < text.index("순서 2") < text.index("순서 3")
    assert "2026-01-01 09:26" in text
    assert "2026-01-01 00:26" not in text
    for comment_id in ids:
        assert f'href="/posts/{post_id}/comments/{comment_id}/delete">삭제</a>' in text


def test_detail_preserves_comment_newlines() -> None:
    content = "첫 줄\n둘째  줄\n\n넷째 줄"
    post_id = add_post()
    add_comment(post_id, content=content)

    text = client.get(f"/posts/{post_id}").text

    assert f'class="mt-1 whitespace-pre-wrap wrap-anywhere">{content}</div>' in text


def test_delete_comment_form_asks_for_confirmation() -> None:
    post_id = add_post()
    comment_id = add_comment(post_id, author="지울 사람", content="가" * 60)

    response = client.get(f"/posts/{post_id}/comments/{comment_id}/delete")

    assert response.status_code == 200
    text = response.text
    assert "이 댓글을 삭제하시겠습니까?" in text
    assert "작성자: 지울 사람" in text
    assert "가" * 47 + "..." in text
    assert "가" * 60 not in text
    assert f'action="/posts/{post_id}/comments/{comment_id}/delete"' in text
    assert f'href="/posts/{post_id}#comments">취소</a>' in text
    assert "댓글을 쓸 때 정한 비밀번호를 입력해 주세요." in text
    assert_input_empty(text, "password")


def test_delete_comment_removes_only_it() -> None:
    post_id = add_post()
    kept = add_comment(post_id, content="남는 댓글")
    comment_id = add_comment(post_id, content="지울 댓글")

    response = client.post(
        f"/posts/{post_id}/comments/{comment_id}/delete", data={"password": TEST_PASSWORD}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/posts/{post_id}?comment_deleted=1#comments"
    assert [comment.id for comment in stored_comments()] == [kept]
    detail = client.get(response.headers["location"]).text
    assert "댓글이 삭제되었습니다." in detail.split('id="comments"')[1]
    assert "지울 댓글" not in detail
    assert "남는 댓글" in detail


def test_delete_comment_with_empty_password_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
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
def test_delete_comment_with_wrong_password_is_403(password: str) -> None:
    post_id = add_post()
    comment_id = add_comment(post_id)

    response = client.post(f"/posts/{post_id}/comments/{comment_id}/delete", data={"password": password})

    assert response.status_code == 403
    assert MISMATCH in response.text
    assert REQUIRED not in response.text
    assert 'id="password-error"' in response.text
    assert len(stored_comments()) == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("raw_post_id", ["999", "abc", "0"])
def test_comment_delete_on_missing_post_is_404(method: str, raw_post_id: str) -> None:
    comment_id = add_comment(add_post())

    response = client.request(
        method, f"/posts/{raw_post_id}/comments/{comment_id}/delete", data={"password": TEST_PASSWORD}
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "글을 찾을 수 없습니다." in response.text
    assert len(stored_comments()) == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("raw_comment_id", ["999", "abc", "0", "other"])
def test_missing_comment_is_404(method: str, raw_comment_id: str) -> None:
    post_id = add_post()
    other_comment = add_comment(add_post(title="다른 글"))
    if raw_comment_id == "other":
        raw_comment_id = str(other_comment)

    response = client.request(
        method, f"/posts/{post_id}/comments/{raw_comment_id}/delete", data={"password": TEST_PASSWORD}
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "댓글을 찾을 수 없습니다." in response.text
    assert f'href="/posts/{post_id}#comments">글로 돌아가기</a>' in response.text
    assert len(stored_comments()) == 1


def test_deleting_post_deletes_its_comments() -> None:
    post_a = add_post(title="A")
    post_b = add_post(title="B")
    add_comment(post_a)
    add_comment(post_a)
    kept = add_comment(post_b)

    response = client.post(f"/posts/{post_a}/delete", data={"password": TEST_PASSWORD}, follow_redirects=False)

    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Comment).where(Comment.post_id == post_a)) == 0
    assert [comment.id for comment in stored_comments()] == [kept]


def test_comment_on_post_deleted_while_hashing_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    post_id = add_post()

    def delete_post_then_hash(password: str) -> str:
        with SessionLocal() as db:
            db.execute(delete(Post).where(Post.id == post_id))
            db.commit()
        return TEST_PASSWORD_HASH

    monkeypatch.setattr("app.main.hash_password", delete_post_then_hash)
    response = client.post(f"/posts/{post_id}/comments", data=comment_form(), follow_redirects=False)

    assert response.status_code == 404
    assert stored_comments() == []


def test_comment_added_while_post_is_deleted_is_deleted_too() -> None:
    post_id = add_post()

    added: list[int] = []

    def comment_before_first_delete(conn: object, cursor: object, statement: str, *args: object) -> None:
        if statement.startswith("DELETE") and not added:
            added.append(add_comment(post_id))

    event.listen(engine, "before_cursor_execute", comment_before_first_delete)
    try:
        response = client.post(f"/posts/{post_id}/delete", data={"password": TEST_PASSWORD}, follow_redirects=False)
    finally:
        event.remove(engine, "before_cursor_execute", comment_before_first_delete)

    assert response.status_code == 303
    assert added
    assert stored_comments() == []


def test_list_shows_comment_count() -> None:
    commented = add_post(title="댓글 많은 글")
    add_post(title="댓글 없는 글")
    for _ in range(3):
        add_comment(commented)

    text = client.get("/").text
    rows = {row.split("</tr>")[0] for row in text.split('<tr class="post-row">')[1:]}

    [commented_row] = [row for row in rows if "댓글 많은 글" in row]
    [plain_row] = [row for row in rows if "댓글 없는 글" in row]
    assert f'href="/posts/{commented}">댓글 많은 글</a>' in commented_row
    assert "[3]" in commented_row
    assert "[" not in plain_row


def count_list_queries() -> int:
    statements: list[str] = []

    def record(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get("/").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(statements)


def test_list_comment_counts_take_no_extra_queries() -> None:
    add_comment(add_post())
    one_post = count_list_queries()

    for _ in range(9):
        post_id = add_post()
        add_comment(post_id)
        add_comment(post_id)

    assert count_list_queries() == one_post


def test_comment_input_is_escaped() -> None:
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
    pages = [
        client.get(f"/posts/{post_id}"),
        client.get(f"/posts/{post_id}/comments/{comment_id}/delete"),
        client.post(f"/posts/{post_id}/comments/{comment_id}/delete", data={"password": "wrong-password"}),
        client.post(f"/posts/{post_id}/comments/{comment_id}/delete", data={"password": ""}),
        *(
            client.post(f"/posts/{post_id}/comments", data=comment_form(**{field: ""}))
            for field in ("author", "content", "password")
        ),
        client.get("/"),
    ]

    for page in pages:
        assert_no_secret(page.text)
