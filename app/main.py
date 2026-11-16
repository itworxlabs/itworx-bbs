import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.migrate import run_migrations
from app.models import Comment, Post
from app.passwords import hash_password, verify_password

BASE_DIR = Path(__file__).resolve().parent.parent
PAGE_SIZE = 10
SEARCH_MAX = 100
SEARCH_FIELD_DEFAULT = "title_content"
SEARCH_FIELDS = {
    "title_content": ("제목+내용", (Post.title, Post.content)),
    "title": ("제목", (Post.title,)),
    "content": ("내용", (Post.content,)),
    "author": ("작성자", (Post.author,)),
}
TITLE_MAX = Post.__table__.c.title.type.length
AUTHOR_MAX = Post.__table__.c.author.type.length
CONTENT_MAX = 10000
COMMENT_AUTHOR_MAX = Comment.__table__.c.author.type.length
COMMENT_CONTENT_MAX = 1000
PASSWORD_MIN = 4
PASSWORD_MAX = 64
PASSWORD_REQUIRED = "비밀번호를 입력해 주세요."
PASSWORD_MISMATCH = "비밀번호가 일치하지 않습니다."
# SQLite cannot bind a larger integer; sqlite3 raises OverflowError instead of finding no row.
SQLITE_INT_MAX = 2**63 - 1
KST = ZoneInfo("Asia/Seoul")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # uvicorn configures only its own loggers, so app messages go through uvicorn.error to show up.
    logging.getLogger("uvicorn.error").info("DB 마이그레이션: %s", run_migrations())
    yield


def format_kst(value: datetime) -> str:
    # The DateTime column stores naive values that are UTC by convention.
    return value.replace(tzinfo=UTC).astimezone(KST).strftime("%Y-%m-%d %H:%M")


app = FastAPI(title="itworx-bbs", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["kst"] = format_kst


def _parse_page(raw: str | None) -> int:
    try:
        return int(raw) if raw is not None else 1
    except ValueError:
        return 1


# Backslash goes first so the escapes added for % and _ are not escaped again.
def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _list_url(page: int, query: str, field: str) -> str:
    if not query:
        return f"/?page={page}"
    return "/?" + urlencode({"page": page, "q": query, "field": field})


def _parse_id(raw: str) -> int | None:
    # Stricter than int(): "+1", " 1" and non-ASCII digits are not ids.
    if not (raw.isascii() and raw.isdigit()):
        return None
    # int() refuses strings over 4300 digits, leading zeros included, so convert only
    # the zero-stripped digits after checking their length ("" means the id was all zeros).
    digits = raw.lstrip("0")
    if not digits or len(digits) > len(str(SQLITE_INT_MAX)):
        return None
    pk = int(digits)
    return pk if pk <= SQLITE_INT_MAX else None


def _find_post(db: Session, raw_id: str) -> Post | None:
    pk = _parse_id(raw_id)
    return db.get(Post, pk) if pk is not None else None


def _find_comment(db: Session, post: Post, raw_id: str) -> Comment | None:
    pk = _parse_id(raw_id)
    comment = db.get(Comment, pk) if pk is not None else None
    return comment if comment is not None and comment.post_id == post.id else None


def _post_not_found(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "post_not_found.html", {}, status_code=404)


def _comment_not_found(request: Request, post: Post) -> HTMLResponse:
    return templates.TemplateResponse(request, "comment_not_found.html", {"post": post}, status_code=404)


def _validate_title_and_content(title: str, content: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not title:
        errors["title"] = "제목을 입력해 주세요."
    elif len(title) > TITLE_MAX:
        errors["title"] = f"제목은 {TITLE_MAX}자 이하로 입력해 주세요."
    if not content:
        errors["content"] = "내용을 입력해 주세요."
    elif len(content) > CONTENT_MAX:
        errors["content"] = f"내용은 {CONTENT_MAX}자 이하로 입력해 주세요."
    return errors


def _validate_author_and_password(author: str, password: str, author_max: int) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not author:
        errors["author"] = "작성자를 입력해 주세요."
    elif len(author) > author_max:
        errors["author"] = f"작성자는 {author_max}자 이하로 입력해 주세요."
    # The password is checked as typed: surrounding spaces are part of it.
    if not password:
        errors["password"] = PASSWORD_REQUIRED
    elif not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        errors["password"] = f"비밀번호는 {PASSWORD_MIN}자 이상 {PASSWORD_MAX}자 이하로 입력해 주세요."
    return errors


def _validate_post(title: str, author: str, content: str, password: str) -> dict[str, str]:
    return _validate_title_and_content(title, content) | _validate_author_and_password(author, password, AUTHOR_MAX)


def _validate_comment(author: str, content: str, password: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not content:
        errors["content"] = "내용을 입력해 주세요."
    elif len(content) > COMMENT_CONTENT_MAX:
        errors["content"] = f"내용은 {COMMENT_CONTENT_MAX}자 이하로 입력해 주세요."
    return errors | _validate_author_and_password(author, password, COMMENT_AUTHOR_MAX)


def _render_detail(
    request: Request,
    post: Post,
    *,
    notice: str | None = None,
    comment_notice: str | None = None,
    comment_form: dict[str, str] | None = None,
    comment_errors: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "post_detail.html",
        {
            "post": post,
            "notice": notice,
            "comment_notice": comment_notice,
            "comment_form": comment_form or {"author": "", "content": ""},
            "comment_errors": comment_errors or {},
        },
        status_code=status_code,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    page: str | None = None,
    deleted: str | None = None,
    q: str | None = None,
    field: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = (q or "").strip()
    field = field if field in SEARCH_FIELDS else SEARCH_FIELD_DEFAULT
    search_error = f"검색어는 {SEARCH_MAX}자 이하로 입력해 주세요." if len(query) > SEARCH_MAX else None
    searching = bool(query) and search_error is None
    conditions = []
    if searching:
        pattern = _like_pattern(query)
        conditions.append(or_(*(column.like(pattern, escape="\\") for column in SEARCH_FIELDS[field][1])))

    total = db.scalar(select(func.count()).select_from(Post).where(*conditions)) or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    current = min(max(_parse_page(page), 1), total_pages)
    comment_count = (
        select(func.count(Comment.id)).where(Comment.post_id == Post.id).correlate(Post).scalar_subquery()
    )
    posts = db.execute(
        select(Post, comment_count)
        .where(*conditions)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset((current - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    link_query = query if searching else ""

    # htmx sends HX-Request on history restores too, but expects a full page for them.
    is_partial = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    response = templates.TemplateResponse(
        request,
        "_post_list.html" if is_partial else "index.html",
        {
            "posts": posts,
            "page": current,
            "total_pages": total_pages,
            "total": total,
            "q": query,
            "field": field,
            "searching": searching,
            "search_error": search_error,
            "search_fields": [(value, label) for value, (label, _) in SEARCH_FIELDS.items()],
            "search_max": SEARCH_MAX,
            "prev_url": _list_url(current - 1, link_query, field) if current > 1 else None,
            "next_url": _list_url(current + 1, link_query, field) if current < total_pages else None,
            "notice": "글이 삭제되었습니다." if deleted == "1" else None,
        },
        status_code=400 if search_error else 200,
    )
    response.headers["Vary"] = "HX-Request"
    return response


@app.get("/posts/new", response_class=HTMLResponse)
def new_post(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "post_new.html", {"form": {"title": "", "author": "", "content": ""}, "errors": {}}
    )


# Form fields default to "" so a missing field reaches our validation (400) instead of FastAPI's 422.
@app.post("/posts", response_model=None)
def create_post(
    request: Request,
    title: Annotated[str, Form()] = "",
    author: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    cleaned = {"title": title.strip(), "author": author.strip(), "content": content.strip()}
    errors = _validate_post(**cleaned, password=password)
    if errors:
        return templates.TemplateResponse(
            request,
            "post_new.html",
            {"form": {"title": title, "author": author, "content": content}, "errors": errors},
            status_code=400,
        )
    post = Post(**cleaned, password_hash=hash_password(password))
    db.add(post)
    db.commit()
    return RedirectResponse(f"/posts/{post.id}?created=1", status_code=303)


# Declared after /posts/new so "new" is not captured as an id. post_id is a str so that a
# non-numeric id gets this HTML 404 instead of FastAPI's 422, without a global handler.
@app.get("/posts/{post_id}", response_class=HTMLResponse)
def post_detail(
    request: Request,
    post_id: str,
    created: str | None = None,
    updated: str | None = None,
    commented: str | None = None,
    comment_deleted: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    post = _find_post(db, post_id)
    if post is None:
        return _post_not_found(request)
    if created == "1":
        notice = "글이 등록되었습니다."
    elif updated == "1":
        notice = "글이 수정되었습니다."
    else:
        notice = None
    # Shown inside the comments section, since these redirects scroll to #comments past the page-top notice.
    if commented == "1":
        comment_notice = "댓글이 등록되었습니다."
    elif comment_deleted == "1":
        comment_notice = "댓글이 삭제되었습니다."
    else:
        comment_notice = None
    return _render_detail(request, post, notice=notice, comment_notice=comment_notice)


@app.get("/posts/{post_id}/edit", response_class=HTMLResponse)
def edit_post_form(request: Request, post_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    post = _find_post(db, post_id)
    if post is None:
        return _post_not_found(request)
    return templates.TemplateResponse(
        request,
        "post_edit.html",
        {"post": post, "form": {"title": post.title, "content": post.content}, "errors": {}},
    )


# Title and content are validated before the password is checked, so invalid input is a 400
# whatever password was sent. An empty password is a 400 answered without computing a hash. The author is not a form field and cannot be changed.
@app.post("/posts/{post_id}/edit", response_model=None)
def update_post(
    request: Request,
    post_id: str,
    title: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    post = _find_post(db, post_id)
    if post is None:
        return _post_not_found(request)
    cleaned = {"title": title.strip(), "content": content.strip()}
    errors = _validate_title_and_content(**cleaned)
    status_code = 400
    if not errors and not password:
        errors = {"password": PASSWORD_REQUIRED}
    elif not errors and not verify_password(password, post.password_hash):
        errors = {"password": PASSWORD_MISMATCH}
        status_code = 403
    if errors:
        return templates.TemplateResponse(
            request,
            "post_edit.html",
            {"post": post, "form": {"title": title, "content": content}, "errors": errors},
            status_code=status_code,
        )
    post.title = cleaned["title"]
    post.content = cleaned["content"]
    db.commit()
    return RedirectResponse(f"/posts/{post.id}?updated=1", status_code=303)


@app.get("/posts/{post_id}/delete", response_class=HTMLResponse)
def delete_post_form(request: Request, post_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    post = _find_post(db, post_id)
    if post is None:
        return _post_not_found(request)
    return templates.TemplateResponse(request, "post_delete.html", {"post": post, "errors": {}})


@app.post("/posts/{post_id}/delete", response_model=None)
def delete_post(
    request: Request,
    post_id: str,
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    post = _find_post(db, post_id)
    if post is None:
        return _post_not_found(request)
    if not password:
        return templates.TemplateResponse(
            request, "post_delete.html", {"post": post, "errors": {"password": PASSWORD_REQUIRED}}, status_code=400
        )
    if not verify_password(password, post.password_hash):
        return templates.TemplateResponse(
            request, "post_delete.html", {"post": post, "errors": {"password": PASSWORD_MISMATCH}}, status_code=403
        )
    # SQLite does not enforce foreign keys here, so the post's comments are deleted with it.
    # Deleting them first takes the write lock, so a comment committed meanwhile goes too.
    db.execute(delete(Comment).where(Comment.post_id == post.id))
    db.delete(post)
    db.commit()
    return RedirectResponse("/?deleted=1", status_code=303)


@app.post("/posts/{post_id}/comments", response_model=None)
def create_comment(
    request: Request,
    post_id: str,
    author: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    post = _find_post(db, post_id)
    if post is None:
        return _post_not_found(request)
    cleaned = {"author": author.strip(), "content": content.strip()}
    errors = _validate_comment(**cleaned, password=password)
    if errors:
        return _render_detail(
            request,
            post,
            comment_form={"author": author, "content": content},
            comment_errors=errors,
            status_code=400,
        )
    db.add(Comment(post_id=post.id, **cleaned, password_hash=hash_password(password)))
    db.flush()
    # The post may have been deleted while the password was hashed; checking after the
    # insert holds the write lock, so delete_post cannot remove it before this commit.
    if db.scalar(select(Post.id).where(Post.id == post.id)) is None:
        db.rollback()
        return _post_not_found(request)
    db.commit()
    return RedirectResponse(f"/posts/{post.id}?commented=1#comments", status_code=303)


@app.get("/posts/{post_id}/comments/{comment_id}/delete", response_class=HTMLResponse)
def delete_comment_form(
    request: Request, post_id: str, comment_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    post = _find_post(db, post_id)
    if post is None:
        return _post_not_found(request)
    comment = _find_comment(db, post, comment_id)
    if comment is None:
        return _comment_not_found(request, post)
    return templates.TemplateResponse(
        request, "comment_delete.html", {"post": post, "comment": comment, "errors": {}}
    )


@app.post("/posts/{post_id}/comments/{comment_id}/delete", response_model=None)
def delete_comment(
    request: Request,
    post_id: str,
    comment_id: str,
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    post = _find_post(db, post_id)
    if post is None:
        return _post_not_found(request)
    comment = _find_comment(db, post, comment_id)
    if comment is None:
        return _comment_not_found(request, post)
    if not password:
        return templates.TemplateResponse(
            request,
            "comment_delete.html",
            {"post": post, "comment": comment, "errors": {"password": PASSWORD_REQUIRED}},
            status_code=400,
        )
    if not verify_password(password, comment.password_hash):
        return templates.TemplateResponse(
            request,
            "comment_delete.html",
            {"post": post, "comment": comment, "errors": {"password": PASSWORD_MISMATCH}},
            status_code=403,
        )
    db.delete(comment)
    db.commit()
    return RedirectResponse(f"/posts/{post.id}?comment_deleted=1#comments", status_code=303)
