import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The project is not installed as a package, so `uv run python scripts/seed.py` only sees scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base, Post  # noqa: E402

SAMPLE_COUNT = 25


def main() -> None:
    Base.metadata.create_all(engine)
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
                created_at=now - timedelta(minutes=SAMPLE_COUNT - i),
            )
            for i in range(1, SAMPLE_COUNT + 1)
        )
        db.commit()
    print(f"샘플 글 {SAMPLE_COUNT}개를 추가했습니다.")


if __name__ == "__main__":
    main()
