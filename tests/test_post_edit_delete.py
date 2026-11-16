from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Post
from tests.test_post_create import assert_password_input_empty
from tests.test_post_detail import add_post
from tests.test_posts import TEST_PASSWORD, TEST_PASSWORD_HASH

client = TestClient(app)

MISMATCH = "비밀번호가 일치하지 않습니다."
# The edit and delete help text ends with the same words, so match the whole error element.
REQUIRED = ">비밀번호를 입력해 주세요.</p>"


def stored(post_id: int) -> Post | None:
    with SessionLocal() as db:
        return db.get(Post, post_id)


def edit_form(**overrides: str) -> dict[str, str]:
    return {"title": "바뀐 제목", "content": "바뀐 내용", "password": TEST_PASSWORD} | overrides


def assert_no_secret(html: str) -> None:
    for secret in (TEST_PASSWORD, TEST_PASSWORD_HASH, *TEST_PASSWORD_HASH.split("$")):
        assert secret not in html


def test_edit_form_is_prefilled() -> None:
    post_id = add_post(title="원래 제목", author="홍길동", content="원래 내용")

    response = client.get(f"/posts/{post_id}/edit")

    assert response.status_code == 200
    assert 'value="원래 제목"' in response.text
    assert ">원래 내용</textarea>" in response.text
    assert "작성자: 홍길동" in response.text
    assert 'name="author"' not in response.text
    assert 'name="password"' in response.text
    assert f'action="/posts/{post_id}/edit"' in response.text
    assert f'href="/posts/{post_id}"' in response.text
    assert_password_input_empty(response.text)


def test_edit_updates_title_and_content_only() -> None:
    post_id = add_post(author="홍길동")

    response = client.post(
        f"/posts/{post_id}/edit",
        data=edit_form(title="  바뀐 제목 ", content="\n바뀐 내용 \n", author="다른사람"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/posts/{post_id}?updated=1"
    post = stored(post_id)
    assert post is not None
    assert (post.title, post.content, post.author) == ("바뀐 제목", "바뀐 내용", "홍길동")
    assert post.created_at == datetime(2026, 1, 1, 0, 25)
    assert ">바뀐 제목</h1>" in client.get(response.headers["location"]).text


@pytest.mark.parametrize("password", ["wrong-password", "   "])
def test_edit_with_wrong_password_is_403(password: str) -> None:
    post_id = add_post(title="원래 제목", content="원래 내용")

    response = client.post(f"/posts/{post_id}/edit", data=edit_form(password=password))

    assert response.status_code == 403
    assert MISMATCH in response.text
    assert REQUIRED not in response.text
    assert 'id="password-error"' in response.text
    assert 'value="바뀐 제목"' in response.text
    assert ">바뀐 내용</textarea>" in response.text
    post = stored(post_id)
    assert post is not None
    assert (post.title, post.content) == ("원래 제목", "원래 내용")


@pytest.mark.parametrize("password", [TEST_PASSWORD, "wrong-password", ""])
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [("title", "", "제목을 입력해 주세요."), ("content", "a" * 10001, "내용은 10000자 이하로 입력해 주세요.")],
)
def test_edit_with_invalid_input_is_400(field: str, value: str, message: str, password: str) -> None:
    post_id = add_post(title="원래 제목", content="원래 내용")

    response = client.post(f"/posts/{post_id}/edit", data=edit_form(**{field: value}, password=password))

    assert response.status_code == 400
    assert message in response.text
    assert MISMATCH not in response.text
    assert REQUIRED not in response.text
    assert "글을 수정하지 못했습니다." in response.text
    assert f'id="{field}-error"' in response.text
    assert_password_input_empty(response.text)
    post = stored(post_id)
    assert post is not None
    assert (post.title, post.content) == ("원래 제목", "원래 내용")


def test_delete_form_asks_for_confirmation() -> None:
    post_id = add_post(title="지울 글")

    response = client.get(f"/posts/{post_id}/delete")

    assert response.status_code == 200
    assert "이 글을 삭제하시겠습니까?" in response.text
    assert "지울 글" in response.text
    assert 'name="password"' in response.text
    assert f'action="/posts/{post_id}/delete"' in response.text
    assert f'href="/posts/{post_id}"' in response.text


def test_delete_removes_post() -> None:
    post_id = add_post(title="지울 글")

    response = client.post(f"/posts/{post_id}/delete", data={"password": TEST_PASSWORD}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?deleted=1"
    assert stored(post_id) is None
    assert "지울 글" not in client.get("/").text
    assert client.get(f"/posts/{post_id}").status_code == 404


@pytest.mark.parametrize("password", ["wrong-password", "   "])
def test_delete_with_wrong_password_is_403(password: str) -> None:
    post_id = add_post()

    response = client.post(f"/posts/{post_id}/delete", data={"password": password})

    assert response.status_code == 403
    assert MISMATCH in response.text
    assert REQUIRED not in response.text
    assert 'id="password-error"' in response.text
    assert stored(post_id) is not None


@pytest.mark.parametrize("action", ["edit", "delete"])
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("raw_id", ["999", "abc", "0"])
def test_missing_post_is_html_404(action: str, method: str, raw_id: str) -> None:
    post_id = add_post(title="남는 글")

    response = client.request(method, f"/posts/{raw_id}/{action}", data=edit_form() if method == "POST" else None)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "글을 찾을 수 없습니다." in response.text
    post = stored(post_id)
    assert post is not None
    assert post.title == "남는 글"


def test_password_and_hash_are_never_shown() -> None:
    post_id = add_post()
    pages = [
        client.get(f"/posts/{post_id}/edit"),
        client.post(f"/posts/{post_id}/edit", data=edit_form(password="wrong-password")),
        client.post(f"/posts/{post_id}/edit", data=edit_form(title="")),
        client.get(f"/posts/{post_id}/delete"),
        client.post(f"/posts/{post_id}/edit", data=edit_form(password="")),
        client.post(f"/posts/{post_id}/delete", data={"password": "wrong-password"}),
        client.post(f"/posts/{post_id}/delete", data={"password": ""}),
        client.get(f"/posts/{post_id}"),
        client.get("/"),
    ]

    for page in pages:
        assert_no_secret(page.text)


def test_edit_and_delete_forms_escape_html() -> None:
    script = "<script>alert(1)</script>"
    post_id = add_post(title=script)

    for path in (f"/posts/{post_id}/edit", f"/posts/{post_id}/delete"):
        text = client.get(path).text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
        assert script not in text


def refuse_hashing(password: str, stored_hash: str) -> bool:
    raise AssertionError("an empty password must not be hashed")


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_edit_and_delete_forms_explain_password(action: str) -> None:
    post_id = add_post()

    text = client.get(f"/posts/{post_id}/{action}").text

    assert 'id="password-help"' in text
    assert "글을 쓸 때 정한 비밀번호를 입력해 주세요." in text
    assert 'aria-describedby="password-help"' in text


def test_edit_with_empty_password_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    post_id = add_post(title="원래 제목", content="원래 내용")
    monkeypatch.setattr("app.main.verify_password", refuse_hashing)

    response = client.post(f"/posts/{post_id}/edit", data=edit_form(password=""))

    assert response.status_code == 400
    assert REQUIRED in response.text
    assert MISMATCH not in response.text
    assert 'aria-describedby="password-error password-help"' in response.text
    assert 'value="바뀐 제목"' in response.text
    assert ">바뀐 내용</textarea>" in response.text
    post = stored(post_id)
    assert post is not None
    assert (post.title, post.content) == ("원래 제목", "원래 내용")


def test_delete_with_empty_password_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    post_id = add_post()
    monkeypatch.setattr("app.main.verify_password", refuse_hashing)

    response = client.post(f"/posts/{post_id}/delete", data={"password": ""})

    assert response.status_code == 400
    assert REQUIRED in response.text
    assert MISMATCH not in response.text
    assert 'aria-describedby="password-error password-help"' in response.text
    assert stored(post_id) is not None


def test_edit_success_shows_notice() -> None:
    post_id = add_post()

    response = client.post(f"/posts/{post_id}/edit", data=edit_form())

    assert response.status_code == 200
    assert 'role="status"' in response.text
    assert "글이 수정되었습니다." in response.text


def test_delete_success_shows_notice() -> None:
    post_id = add_post()

    response = client.post(f"/posts/{post_id}/delete", data={"password": TEST_PASSWORD})

    assert response.status_code == 200
    assert 'role="status"' in response.text
    assert "글이 삭제되었습니다." in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/?deleted=<script>alert(1)</script>",
        "/?deleted=true",
        "/posts/{id}?created=2",
        "/posts/{id}?updated=<script>alert(1)</script>",
        "/posts/{id}?commented=2",
        "/posts/{id}?comment_deleted=<script>alert(1)</script>",
    ],
)
def test_notice_needs_exact_flag(path: str) -> None:
    post_id = add_post()

    text = client.get(path.format(id=post_id)).text

    assert 'role="status"' not in text
    notices = ("글이 등록되었습니다.", "글이 수정되었습니다.", "글이 삭제되었습니다.", "댓글이 등록되었습니다.", "댓글이 삭제되었습니다.")
    for notice in notices:
        assert notice not in text
    assert "alert(1)" not in text
