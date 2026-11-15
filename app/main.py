import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import engine, get_db
from app.models import Base, Post

BASE_DIR = Path(__file__).resolve().parent.parent
PAGE_SIZE = 10
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
