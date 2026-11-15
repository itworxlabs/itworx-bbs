from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import Post
from app.passwords import verify_password
from tests.test_posts import TEST_PASSWORD, add_posts

client = TestClient(app)


def valid_form(**overrides: str) -> dict[str, str]:
    return {"title": "새 글", "author": "홍길동", "content": "본문", "password": TEST_PASSWORD} | overrides


def post_count() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Post)) or 0


def first_row(html: str) -> str:
    start = html.index('class="post-row"')
    return html[start : html.index("</tr>", start)]


def test_new_post_form_renders() -> None:
    response = client.get("/posts/new")

    assert response.status_code == 200
    fragments = ('name="title"', 'name="author"', 'name="content"', "<textarea", 'name="password"', 'type="password"', "등록")
    for fragment in fragments:
        assert fragment in response.text
    assert 'method="post"' in response.text
    assert 'action="/posts"' in response.text
    assert 'href="/"' in response.text
    assert "hx-" not in response.text.split("<form", 1)[1]


def test_index_links_to_new_post() -> None:
    response = client.get("/")

    assert 'href="/posts/new"' in response.text
    assert "글쓰기" in response.text


def test_create_post_redirects_and_lists_first() -> None:
    add_posts(3)

    response = client.post("/posts", data=valid_form(), follow_redirects=False)

    assert response.status_code == 303
    assert post_count() == 4
    assert "새 글" in first_row(client.get("/").text)
    with SessionLocal() as db:
        new_id, stored = db.execute(select(Post.id, Post.created_at).where(Post.title == "새 글")).one()
    assert response.headers["location"] == f"/posts/{new_id}?created=1"
    assert abs(stored - datetime.now(UTC).replace(tzinfo=None)) < timedelta(minutes=1)


def test_create_post_strips_whitespace() -> None:
    client.post("/posts", data=valid_form(title="  제목 \n", author="\t작성자 ", content="\n 본문 내용 \n"))

    with SessionLocal() as db:
        post = db.scalars(select(Post)).one()
    assert (post.title, post.author, post.content) == ("제목", "작성자", "본문 내용")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("title", "", "제목을 입력해 주세요."),
        ("title", "   ", "제목을 입력해 주세요."),
        ("title", "a" * 201, "제목은 200자 이하로 입력해 주세요."),
        ("author", "", "작성자를 입력해 주세요."),
        ("author", " \t ", "작성자를 입력해 주세요."),
        ("author", "a" * 51, "작성자는 50자 이하로 입력해 주세요."),
        ("content", "", "내용을 입력해 주세요."),
        ("content", "\n \n", "내용을 입력해 주세요."),
        ("content", "a" * 10001, "내용은 10000자 이하로 입력해 주세요."),
    ],
)
def test_create_post_rejects_invalid(field: str, value: str, message: str) -> None:
    response = client.post("/posts", data=valid_form(**{field: value}))

    assert response.status_code == 400
    assert message in response.text
    assert "글을 등록하지 못했습니다." in response.text
    assert f'aria-describedby="{field}-error"' in response.text
    assert f'id="{field}-error"' in response.text
    if field != "title":
        assert 'value="새 글"' in response.text
    if field != "author":
        assert 'value="홍길동"' in response.text
    if field != "content":
        assert ">본문</textarea>" in response.text
    if len(value) > 50:
        assert value in response.text
    assert post_count() == 0


def test_create_post_accepts_max_lengths() -> None:
    form = valid_form(title="t" * 200, author="a" * 50, content="c" * 10000, password="p" * 64)

    response = client.post("/posts", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert post_count() == 1


def test_create_post_missing_fields_is_400() -> None:
    response = client.post("/posts")

    assert response.status_code == 400
    for message in ("제목을 입력해 주세요.", "작성자를 입력해 주세요.", "내용을 입력해 주세요.", "비밀번호를 입력해 주세요."):
        assert message in response.text
    assert post_count() == 0


PASSWORD_LENGTH_MESSAGE = "비밀번호는 4자 이상 64자 이하로 입력해 주세요."


def assert_password_input_empty(html: str) -> None:
    start = html.index('<input type="password" id="password"')
    assert "value=" not in html[start : html.index(">", start)]


@pytest.mark.parametrize(
    ("password", "message"),
    [("", "비밀번호를 입력해 주세요."), ("abc", PASSWORD_LENGTH_MESSAGE), ("a" * 65, PASSWORD_LENGTH_MESSAGE)],
)
def test_create_post_rejects_invalid_password(password: str, message: str) -> None:
    response = client.post("/posts", data=valid_form(password=password))

    assert response.status_code == 400
    assert message in response.text
    assert 'id="password-error"' in response.text
    assert 'value="새 글"' in response.text
    assert 'value="홍길동"' in response.text
    assert_password_input_empty(response.text)
    if password:
        assert password not in response.text
    assert post_count() == 0


def test_create_post_keeps_password_spaces() -> None:
    response = client.post("/posts", data=valid_form(password=" ab "), follow_redirects=False)

    assert response.status_code == 303
    with SessionLocal() as db:
        stored = db.scalars(select(Post.password_hash)).one()
    assert verify_password(" ab ", stored)
    assert not verify_password("ab", stored)


def test_create_post_stores_salted_hash() -> None:
    client.post("/posts", data=valid_form(title="하나"))
    client.post("/posts", data=valid_form(title="둘"))

    with SessionLocal() as db:
        first, second = db.scalars(select(Post.password_hash)).all()
    assert TEST_PASSWORD not in (first, second)
    assert first != second
    assert verify_password(TEST_PASSWORD, first)
    assert verify_password(TEST_PASSWORD, second)


def test_invalid_form_redisplay_omits_password() -> None:
    response = client.post("/posts", data=valid_form(title=""))

    assert response.status_code == 400
    assert TEST_PASSWORD not in response.text
    assert_password_input_empty(response.text)


def test_script_title_is_escaped_in_list() -> None:
    client.post("/posts", data=valid_form(title="<script>alert(1)</script>", author="<b>x</b>"))

    text = client.get("/").text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert(1)</script>" not in text
    assert "<b>x</b>" not in text


def test_invalid_form_redisplay_is_escaped() -> None:
    response = client.post("/posts", data=valid_form(title="", content="</textarea><script>alert(1)</script>"))

    assert response.status_code == 400
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;/textarea&gt;" in response.text


def test_new_post_form_explains_password() -> None:
    text = client.get("/posts/new").text

    assert 'id="password-help"' in text
    assert "글을 수정·삭제할 때 필요합니다. 4~64자로 입력해 주세요." in text
    assert 'aria-describedby="password-help"' in text


def test_empty_password_error_keeps_help_described() -> None:
    response = client.post("/posts", data=valid_form(password=""))

    assert response.status_code == 400
    assert 'aria-describedby="password-error password-help"' in response.text


def test_invalid_form_redisplays_input_as_typed() -> None:
    form = valid_form(title="  제목  ", author=" 작성자 ", content=" 본문 ", password="")

    response = client.post("/posts", data=form)

    assert response.status_code == 400
    assert 'value="  제목  "' in response.text
    assert 'value=" 작성자 "' in response.text
    assert "> 본문 </textarea>" in response.text
    assert post_count() == 0


def test_create_success_shows_notice() -> None:
    response = client.post("/posts", data=valid_form())

    assert response.status_code == 200
    assert 'role="status"' in response.text
    assert "글이 등록되었습니다." in response.text
