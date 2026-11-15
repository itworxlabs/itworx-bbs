import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The project is not installed as a package, so `uv run python scripts/seed.py` only sees scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.migrate import run_migrations  # noqa: E402
from app.models import Comment, Post  # noqa: E402
from app.passwords import hash_password  # noqa: E402

SAMPLE_COUNT = 25
SAMPLE_PASSWORD = "1234"
# Sample post number -> how many sample comments it gets.
SAMPLE_COMMENTS = {25: 3, 24: 2, 23: 1}


def main() -> None:
    print(f"DB 마이그레이션: {run_migrations()}")
    add_sample_posts()
    add_sample_comments()


def add_sample_posts() -> None:
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Post)):
            print("이미 글이 있어 샘플 글을 추가하지 않습니다.")
            return
        now = datetime.now(UTC)
        db.add_all(
            Post(
                title=f"샘플 글 {i}",
                content=f"샘플 글 {i}의 내용입니다.",
                author="관리자",
                password_hash=hash_password(SAMPLE_PASSWORD),
                created_at=now - timedelta(minutes=SAMPLE_COUNT - i),
            )
            for i in range(1, SAMPLE_COUNT + 1)
        )
        db.commit()
    print(f"샘플 글 {SAMPLE_COUNT}개를 추가했습니다.")


def add_sample_comments() -> None:
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Comment)):
            print("이미 댓글이 있어 샘플 댓글을 추가하지 않습니다.")
            return
        # Chosen by sample title so that posts a user wrote never get sample comments.
        titles = {f"샘플 글 {i}": count for i, count in SAMPLE_COMMENTS.items()}
        # Capped at now: the newest sample post is dated now, and a later comment must never sort above
        # these. Stored times are read back without tzinfo, so now is compared the same way.
        now = datetime.now(UTC).replace(tzinfo=None)
        comments = [
            Comment(
                post_id=post.id,
                author=f"방문자{j}",
                content=f"샘플 댓글 {j}입니다.",
                password_hash=hash_password(SAMPLE_PASSWORD),
                created_at=min(post.created_at + timedelta(minutes=j), now),
            )
            for post in db.scalars(select(Post).where(Post.title.in_(titles)))
            for j in range(1, titles[post.title] + 1)
        ]
        db.add_all(comments)
        db.commit()
    print(f"샘플 댓글 {len(comments)}개를 추가했습니다.")


if __name__ == "__main__":
    main()
