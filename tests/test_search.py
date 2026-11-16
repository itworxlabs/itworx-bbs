import html
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Post
from tests.test_comments import add_comment, count_list_queries
from tests.test_post_detail import add_post
from tests.test_posts import BASE_TIME, TEST_PASSWORD_HASH, add_posts, row_count

client = TestClient(app)


def add_titled_posts(titles: list[str]) -> None:
    with SessionLocal() as db:
        db.add_all(
            Post(
                title=title,
                content="내용",
                author="작성자",
                password_hash=TEST_PASSWORD_HASH,
                created_at=BASE_TIME + timedelta(minutes=i),
            )
            for i, title in enumerate(titles, start=1)
        )
        db.commit()


def add_found_and_other_posts() -> None:
    # 25 posts, the 12 with even numbers titled so that "찾기" finds them.
    add_titled_posts([f"찾기 {i}" if i % 2 == 0 else f"다른 {i}" for i in range(1, 26)])


def link(page: int, q: str, field: str) -> str:
    return html.escape("/?" + urlencode({"page": page, "q": q, "field": field}))


def titles_found(params: dict[str, str]) -> set[str]:
    text = client.get("/", params=params).text
    return {title for title in ("사과", "포도") if f">{title}</a>" in text}


@pytest.mark.parametrize(
    ("field", "q", "expected"),
    [
        ("title", "사과", {"사과"}),
        ("content", "사과", {"포도"}),
        ("title_content", "사과", {"사과", "포도"}),
        ("author", "영희", {"포도"}),
        ("title", "바나나", set()),
    ],
)
def test_search_by_field_matches_only_that_field(field: str, q: str, expected: set[str]) -> None:
    add_post(title="사과", content="바나나", author="철수")
    add_post(title="포도", content="사과", author="영희")

    assert titles_found({"q": q, "field": field}) == expected


@pytest.mark.parametrize("params", [{"q": "사과"}, {"q": "사과", "field": "nope"}, {"q": "사과", "field": ""}])
def test_default_and_unknown_field_search_title_and_content(params: dict[str, str]) -> None:
    add_post(title="사과", content="바나나", author="철수")
    add_post(title="포도", content="사과", author="영희")

    response = client.get("/", params=params)

    assert response.status_code == 200
    assert titles_found(params) == {"사과", "포도"}
    assert '<option value="title_content" selected>' in response.text


@pytest.mark.parametrize("q", ["", "   "])
def test_blank_query_shows_full_list(q: str) -> None:
    add_posts(25)

    response = client.get("/", params={"q": q})

    assert response.status_code == 200
    assert row_count(response.text) == 10
    assert "1 / 3" in response.text
    assert "검색 결과" not in response.text
    assert 'href="/?page=2"' in response.text


def test_query_is_stripped() -> None:
    add_post(title="사과")
    add_post(title="포도")

    text = client.get("/", params={"q": "  사과  "}).text

    assert "'사과' 검색 결과 1개" in text
    assert row_count(text) == 1
    assert 'value="사과"' in text


def test_search_ignores_ascii_case() -> None:
    add_post(title="Hello")

    assert row_count(client.get("/", params={"q": "hELLO"}).text) == 1


@pytest.mark.parametrize(
    ("q", "matching", "other"),
    [("100%", "100%", "100점"), ("a_b", "a_b", "axb"), ("a\\b", "a\\b", "ab"), ("\\", "역\\슬래시", "슬래시")],
)
def test_wildcards_are_literal(q: str, matching: str, other: str) -> None:
    add_post(title=matching)
    add_post(title=other)

    text = client.get("/", params={"q": q, "field": "title"}).text

    assert row_count(text) == 1
    assert f">{html.escape(matching)}</a>" in text


def test_query_length_limit() -> None:
    add_posts(3)

    assert client.get("/", params={"q": "가" * 100}).status_code == 200

    too_long = "가" * 101
    response = client.get("/", params={"q": too_long})
    assert response.status_code == 400
    assert "검색어는 100자 이하로 입력해 주세요." in response.text
    assert f'value="{too_long}"' in response.text
    assert 'aria-describedby="search-q-error"' in response.text
    assert "검색 결과" not in response.text
    assert row_count(response.text) == 3


def test_search_counts_and_pages_matching_posts_only() -> None:
    add_found_and_other_posts()

    first = client.get("/", params={"q": "찾기"}).text
    assert "검색 결과 12개" in first
    assert "1 / 2" in first
    assert row_count(first) == 10
    assert "다른" not in first

    second = client.get("/", params={"q": "찾기", "page": "2"}).text
    assert row_count(second) == 2
    assert "다른" not in second


@pytest.mark.parametrize(("page", "shown"), [("99", "2 / 2"), ("0", "1 / 2")])
def test_search_page_is_clamped_to_results(page: str, shown: str) -> None:
    add_found_and_other_posts()

    text = client.get("/", params={"q": "찾기", "page": page}).text

    assert shown in text
    assert row_count(text) == (2 if page == "99" else 10)


def test_page_links_keep_search_encoded() -> None:
    q = "한글 a&b#c"
    add_titled_posts([f"{q} {i}" for i in range(1, 13)])

    first = client.get("/", params={"q": q, "field": "title"}).text
    next_link = link(2, q, "title")
    assert next_link == "/?page=2&amp;q=%ED%95%9C%EA%B8%80+a%26b%23c&amp;field=title"
    assert f'href="{next_link}" hx-get="{next_link}"' in first

    prev_link = link(1, q, "title")
    second = client.get("/", params={"q": q, "field": "title", "page": "2"}).text
    assert f'href="{prev_link}" hx-get="{prev_link}"' in second


def test_htmx_partial_keeps_search() -> None:
    add_found_and_other_posts()

    response = client.get("/", params={"q": "찾기", "field": "title", "page": "2"}, headers={"HX-Request": "true"})

    assert "<html" not in response.text
    assert 'id="post-list"' in response.text
    assert "검색 결과 12개" in response.text
    assert row_count(response.text) == 2
    assert f'href="{link(1, "찾기", "title")}"' in response.text


def test_no_results_message_and_full_list_link() -> None:
    add_posts(3)

    text = client.get("/", params={"q": "없는 단어"}).text

    assert "검색 결과가 없습니다." in text
    assert "등록된 글이 없습니다." not in text
    assert "검색 결과 0개" in text
    assert '<a class="text-blue-700 underline hover:text-blue-900" href="/">전체 목록</a>' in text


def test_form_is_filled_with_current_search() -> None:
    text = client.get("/", params={"q": "사과", "field": "author"}).text

    assert '<form method="get" action="/" role="search"' in text
    assert '<option value="author" selected>작성자</option>' in text
    assert '<option value="title_content">제목+내용</option>' in text
    assert '<label for="search-q" class="sr-only">검색어</label>' in text
    assert '<input type="search" id="search-q" name="q" value="사과" maxlength="100"' in text
    assert '<label for="search-field" class="sr-only">검색 대상</label>' in text
    assert '<select id="search-field" name="field"' in text


def test_search_query_is_escaped() -> None:
    q = "<script>alert(1)</script>"
    add_titled_posts([f"{q} {i}" for i in range(1, 12)])

    text = client.get("/", params={"q": q}).text

    assert q not in text
    assert "'&lt;script&gt;alert(1)&lt;/script&gt;' 검색 결과 11개" in text
    assert 'value="&lt;script&gt;alert(1)&lt;/script&gt;"' in text
    assert "%3Cscript%3E" in text


def test_search_does_not_match_comment_content() -> None:
    add_comment(add_post(title="제목", content="내용"), content="댓글에만 있는 단어")

    assert "검색 결과가 없습니다." in client.get("/", params={"q": "댓글에만"}).text


def test_search_keeps_comment_count() -> None:
    post_id = add_post(title="사과")
    for _ in range(3):
        add_comment(post_id)

    assert '<span class="text-sm text-gray-600">[3]</span>' in client.get("/", params={"q": "사과"}).text


def test_search_list_queries_do_not_grow_with_posts() -> None:
    add_comment(add_post(title="글"))
    one_match = count_list_queries("/?q=글")

    for _ in range(9):
        add_comment(add_post(title="글"))

    assert count_list_queries("/?q=글") == one_match
