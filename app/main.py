import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import engine, get_db
from app.models import Base, Post

BASE_DIR = Path(__file__).resolve().parent.parent
PAGE_SIZE = 10
TITLE_MAX = Post.__table__.c.title.type.length
AUTHOR_MAX = Post.__table__.c.author.type.length
CONTENT_MAX = 10000
# SQLite cannot bind a larger integer; sqlite3 raises OverflowError instead of finding no row.
SQLITE_INT_MAX = 2**63 - 1
KST = ZoneInfo("Asia/Seoul")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
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


def _parse_post_id(raw: str) -> int | None:
    # Stricter than int(): "+1", " 1" and non-ASCII digits are not post ids.
    if not (raw.isascii() and raw.isdigit()):
        return None
    pk = int(raw)
    return pk if 1 <= pk <= SQLITE_INT_MAX else None


def _validate_post(title: str, author: str, content: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not title:
        errors["title"] = "제목을 입력해 주세요."
    elif len(title) > TITLE_MAX:
        errors["title"] = f"제목은 {TITLE_MAX}자 이하로 입력해 주세요."
    if not author:
        errors["author"] = "작성자를 입력해 주세요."
    elif len(author) > AUTHOR_MAX:
        errors["author"] = f"작성자는 {AUTHOR_MAX}자 이하로 입력해 주세요."
    if not content:
        errors["content"] = "내용을 입력해 주세요."
    elif len(content) > CONTENT_MAX:
        errors["content"] = f"내용은 {CONTENT_MAX}자 이하로 입력해 주세요."
    return errors


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, page: str | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    total = db.scalar(select(func.count()).select_from(Post)) or 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    current = min(max(_parse_page(page), 1), total_pages)
    posts = db.scalars(
        select(Post)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset((current - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()

    # htmx sends HX-Request on history restores too, but expects a full page for them.
    is_partial = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    response = templates.TemplateResponse(
        request,
        "_post_list.html" if is_partial else "index.html",
        {"posts": posts, "page": current, "total_pages": total_pages},
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
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    cleaned = {"title": title.strip(), "author": author.strip(), "content": content.strip()}
    errors = _validate_post(**cleaned)
    if errors:
        return templates.TemplateResponse(
            request,
            "post_new.html",
            {"form": {"title": title, "author": author, "content": content}, "errors": errors},
            status_code=400,
        )
    post = Post(**cleaned)
    db.add(post)
    db.commit()
    return RedirectResponse(f"/posts/{post.id}", status_code=303)


# Declared after /posts/new so "new" is not captured as an id. post_id is a str so that a
# non-numeric id gets this HTML 404 instead of FastAPI's 422, without a global handler.
@app.get("/posts/{post_id}", response_class=HTMLResponse)
def post_detail(request: Request, post_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    pk = _parse_post_id(post_id)
    post = db.get(Post, pk) if pk is not None else None
    if post is None:
        return templates.TemplateResponse(request, "post_not_found.html", {}, status_code=404)
    return templates.TemplateResponse(request, "post_detail.html", {"post": post})
