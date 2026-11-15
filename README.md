# itworx-bbs

## 개요

itworx 게시판 서비스의 프로젝트 뼈대다. FastAPI, Jinja2 템플릿과 HTMX(CDN), SQLAlchemy 2.x(기본 SQLite)로 구성되어 있으며, 아직 게시판 기능은 없다.

## 요구 사항

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## 로컬 실행

```bash
uv sync
uv run uvicorn app.main:app --reload
```

- 메인 페이지: http://localhost:8000/
- 헬스 체크: http://localhost:8000/health

### .env 사용

```bash
cp .env.example .env
uv run --env-file .env uvicorn app.main:app
```

애플리케이션은 `.env` 파일을 자동으로 읽지 않는다. 위처럼 `uv run --env-file .env`로 로드하거나 환경변수를 직접 지정한다.

## 테스트

```bash
uv run pytest
```

## Docker

```bash
docker build -t itworx-bbs .
docker run --rm -p 8000:8000 itworx-bbs
```

데이터베이스 URL 지정:

```bash
docker run --rm -p 8000:8000 -e DATABASE_URL=sqlite:///./data/bbs.db itworx-bbs
```

SQLite 데이터를 컨테이너 밖에 보존하려면 `data/` 디렉터리를 마운트한다.

```bash
docker run --rm -p 8000:8000 -v "$(pwd)/data:/app/data" itworx-bbs
```

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/bbs.db` | SQLAlchemy 데이터베이스 연결 URL. SQLite 파일 경로의 상위 디렉터리는 자동으로 만들어진다. |

## 디렉터리 구조

```
app/
  main.py      # FastAPI 앱, 라우트
  config.py    # 환경변수 설정
  db.py        # SQLAlchemy engine, session
templates/     # Jinja2 템플릿 (base.html, index.html)
static/        # 정적 파일 (/static)
tests/         # pytest 테스트
Dockerfile
pyproject.toml
uv.lock
```
