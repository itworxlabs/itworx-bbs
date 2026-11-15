# itworx-bbs

## 개요

itworx 게시판 서비스다. FastAPI, Jinja2 템플릿과 HTMX(CDN), Tailwind CSS v4, SQLAlchemy 2.x(기본 SQLite)로 구성되어 있으며, 글 목록, 글 상세, 글쓰기, 글 수정, 글 삭제 기능이 있다. 서버를 시작하면 `DATABASE_URL`의 데이터베이스에 테이블을 만든다(마이그레이션 도구는 쓰지 않는다).

## 요구 사항

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

Node.js는 필요 없다. Tailwind CSS는 독립 실행형 CLI를 [pytailwindcss](https://github.com/timonweb/pytailwindcss)(dev 의존성)로 실행한다.

## 로컬 실행

```bash
uv sync
uv run uvicorn app.main:app --reload
```

- 메인 페이지: http://localhost:8000/
- 글쓰기: http://localhost:8000/posts/new
- 글 상세: http://localhost:8000/posts/1
- 글 수정: http://localhost:8000/posts/1/edit
- 글 삭제: http://localhost:8000/posts/1/delete
- 헬스 체크: http://localhost:8000/health

### 샘플 데이터

```bash
uv run python scripts/seed.py
```

샘플 글 25개를 추가한다. 글이 이미 하나라도 있으면 아무것도 하지 않는다. 샘플 글의 비밀번호는 모두 `1234`다.

### 개발 DB 다시 만들기

`posts` 테이블에 `password_hash` 컬럼이 추가되었다. 서버 시작 시 실행되는 `create_all`은 이미 있는 테이블에 컬럼을 추가하지 않으므로, 이전에 만든 `data/bbs.db`에서는 글 등록과 조회가 실패한다. 마이그레이션 도구는 쓰지 않으므로 개발 DB를 지우고 다시 만든다.

```bash
rm data/bbs.db
uv run python scripts/seed.py
```

### .env 사용

```bash
cp .env.example .env
uv run --env-file .env uvicorn app.main:app
```

애플리케이션은 `.env` 파일을 자동으로 읽지 않는다. 위처럼 `uv run --env-file .env`로 로드하거나 환경변수를 직접 지정한다.

## CSS 빌드(Tailwind)

Tailwind CSS 실행 파일 버전은 v4.3.5로 고정한다. 템플릿의 클래스를 바꾸면 아래 명령으로 CSS를 다시 빌드한 뒤 템플릿과 함께 커밋한다.

```bash
TAILWINDCSS_VERSION=v4.3.5 uv run tailwindcss -i static/src/input.css -o static/css/app.css --minify
```

- 입력은 `static/src/input.css`이고 `templates/`만 스캔한다. 출력은 압축된 `static/css/app.css`이며 저장소에 커밋한다.
- 처음 실행할 때 GitHub에서 실행 파일을 내려받아 `.venv` 안에 저장하므로 네트워크가 필요하다. `TAILWINDCSS_VERSION`을 빼면 최신 버전을 받으므로 꼭 붙인다.
- 서버, 테스트, Docker 빌드는 커밋된 `app.css`만 쓰므로 Tailwind 실행 파일이 필요 없다.
- 클래스 이름은 템플릿에 완전한 이름으로 쓴다. 문자열을 조합해 클래스를 만들면 스캔에서 빠진다.
- 테스트가 템플릿의 클래스가 모두 `app.css`에 있는지와 `app.css`의 버전 헤더(`tailwindcss v4.3.5`)를 확인한다.

## 처리 완료 안내

- 등록, 수정, 삭제에 성공하면 이동한 화면 위에 안내 문구(`role="status"`)를 보여 준다. 안내 여부는 쿼리(`?created=1`, `?updated=1`, `?deleted=1`)로 전달한다.
- 값이 정확히 `1`일 때만 서버가 정한 고정 문구를 보여 주고, 쿼리 값 자체는 화면에 출력하지 않는다.
- 안내는 쿼리가 붙은 주소에서 늘 다시 보인다. 새로고침하거나 그 주소를 공유해도 보이고, 목록에서 HTMX로 페이지를 옮겨도 목록 영역만 바뀌므로 안내는 남는다.

## 글 목록

- `/`에서 글을 최신순(작성일 내림차순, 같으면 번호 내림차순)으로 10개씩 보여 준다. `?page=N`으로 페이지를 고른다(기본 1).
- 숫자가 아니거나 1보다 작은 `page`는 첫 페이지, 마지막 페이지보다 큰 값은 마지막 페이지를 보여 준다.
- 작성일은 DB에 UTC로 저장하고, 화면에는 한국 시간(Asia/Seoul, KST)으로 변환해 `YYYY-MM-DD HH:MM` 형식으로 표시한다.
- 이전/다음 링크는 HTMX로 목록 영역만 교체하고 주소를 갱신한다. JavaScript가 없어도 일반 링크로 동작한다.
- 목록 위의 "글쓰기" 링크로 글쓰기 화면에 들어간다.
- 제목을 누르면 글 상세 화면으로 이동한다.

## 글 상세

- `/posts/{id}`에서 제목, 작성자, 작성일(KST), 내용을 보여 준다. 내용의 줄바꿈과 연속 공백은 그대로 표시한다.
- "목록으로", "글쓰기", "수정", "삭제" 링크가 있다.
- 없는 글이나 잘못된 id(숫자가 아니거나 0 이하)는 HTML 404 "글을 찾을 수 없습니다." 화면으로 응답한다. 다른 경로의 404는 기존대로 JSON이다.
- 출력은 자동 이스케이프되어 HTML로 해석되지 않는다.

## 글쓰기

- `/posts/new`에서 제목, 작성자, 내용, 비밀번호를 입력해 등록한다. 일반 HTML form 전송(`POST /posts`)이라 JavaScript가 없어도 동작한다.
- 입력값은 앞뒤 공백을 제거한 뒤 검증한다. 제목은 1~200자, 작성자는 1~50자, 내용은 1~10000자여야 한다.
- 비밀번호는 필수이고 4~64자여야 한다. 앞뒤 공백을 지우지 않고 입력한 그대로 쓴다. 글을 수정하거나 삭제할 때 이 비밀번호가 필요하다. 비밀번호 칸 아래에 "글을 수정·삭제할 때 필요합니다. 4~64자로 입력해 주세요." 안내가 있다.
- 비밀번호는 scrypt로 글마다 다른 salt를 써서 해시로만 저장하며, 어떤 화면에도 출력하지 않는다.
- 검증에 실패하면 400 응답과 함께 항목별 오류를 보여 주고, 입력한 값은 그대로 유지한다. 단, 비밀번호 칸은 비워 둔다.
- 등록에 성공하면 303으로 새 글 상세 화면(`/posts/{id}?created=1`)으로 이동하고 "글이 등록되었습니다." 안내를 보여 준다.
- 작성일은 클라이언트에서 받지 않고 서버가 UTC로 기록한다.
- 입력값은 화면에 출력할 때 자동 이스케이프되어 HTML로 해석되지 않는다.

## 글 수정

- `/posts/{id}/edit`에서 제목과 내용을 고친다. 현재 값이 채워진 폼이 나오고, 작성자는 표시만 하며 바꿀 수 없다.
- 제목과 내용은 글쓰기와 같은 규칙으로 검증한다. 실패하면 400 응답과 항목별 오류를 보여 준다.
- 제목·내용 검증, 빈 비밀번호, 비밀번호 일치 순서로 확인한다. 비밀번호가 비어 있으면 해시를 계산하지 않고 400 응답과 "비밀번호를 입력해 주세요." 오류를, 틀리면 403 응답과 "비밀번호가 일치하지 않습니다." 오류를 보여 준다. 두 경우 모두 입력한 제목과 내용은 유지하고 비밀번호 칸은 비워 둔다.
- 비밀번호 칸 아래에 "글을 쓸 때 정한 비밀번호를 입력해 주세요." 안내가 있다(삭제 화면도 같다).
- 수정에 성공하면 303으로 글 상세 화면(`/posts/{id}?updated=1`)으로 이동하고 "글이 수정되었습니다." 안내를 보여 준다. 작성자와 작성일은 바뀌지 않는다.

## 글 삭제

- `/posts/{id}/delete`에서 삭제할 글의 제목과 확인 문구를 보여 주고 비밀번호를 받는다. "취소"는 글 상세 화면으로 돌아간다.
- 비밀번호가 비어 있으면 해시를 계산하지 않고 400 응답과 "비밀번호를 입력해 주세요." 오류를, 틀리면 403 응답과 "비밀번호가 일치하지 않습니다." 오류를 보여 준다.
- 삭제에 성공하면 303으로 글 목록(`/?deleted=1`)으로 이동하고 목록 위에 "글이 삭제되었습니다." 안내를 보여 준다. 삭제한 글은 되돌릴 수 없다.
- 수정·삭제 화면과 전송 모두, 없는 글이나 잘못된 id는 글 상세와 같은 HTML 404 화면으로 응답한다.

## 테스트

```bash
uv run pytest
```

테스트는 임시 디렉터리의 SQLite DB를 사용하며 `data/bbs.db`를 건드리지 않는다.

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
| `DATABASE_URL` | `sqlite:///./data/bbs.db` | SQLAlchemy 데이터베이스 연결 URL. `app.db`를 import할 때 읽으며, 이때 SQLite 파일 경로의 상위 디렉터리를 만든다. |

## 디렉터리 구조

```
app/
  main.py      # FastAPI 앱, 라우트
  config.py    # 환경변수 설정
  db.py        # SQLAlchemy engine, session
  models.py    # SQLAlchemy 모델 (Post)
  passwords.py # 글 비밀번호 해시(scrypt)와 확인
scripts/
  seed.py      # 개발용 샘플 글 추가
templates/     # Jinja2 템플릿 (base.html, index.html, _post_list.html, post_new.html, post_detail.html, post_edit.html, post_delete.html, post_not_found.html)
static/        # 정적 파일 (/static)
  src/input.css  # Tailwind 입력 CSS
  css/app.css    # 빌드한 CSS (커밋한다)
tests/         # pytest 테스트
Dockerfile
pyproject.toml
uv.lock
```
