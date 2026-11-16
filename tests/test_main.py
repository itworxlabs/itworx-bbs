import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_renders_base_layout_with_htmx() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "htmx.org" in response.text
    assert 'id="main"' in response.text
    assert "<title>itworx-bbs</title>" in response.text


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = sorted((ROOT / "templates").glob("*.html"))
APP_CSS = ROOT / "static" / "css" / "app.css"
# Mark list rows, comments and new-post marks for tests; they have no style of their own.
UNSTYLED_CLASSES = {"post-row", "comment", "new-mark"}


def test_index_loads_built_stylesheet() -> None:
    assert 'href="/static/css/app.css"' in client.get("/").text

    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_templates_have_no_style_blocks() -> None:
    for template in TEMPLATES:
        assert "<style" not in template.read_text(), template.name


def test_css_is_built_with_pinned_tailwind() -> None:
    assert "tailwindcss v4.3.5" in APP_CSS.read_text()


def css_selector(token: str) -> re.Pattern[str]:
    escaped = "." + re.sub(r"([^A-Za-z0-9_-])", r"\\\1", token)
    # The selector must end there: `.flex` alone is also a prefix of `.flex-wrap`.
    return re.compile(re.escape(escaped) + r"(?![A-Za-z0-9_\\-])")


def test_css_has_every_template_class() -> None:
    css = APP_CSS.read_text()
    tokens = {
        token
        for template in TEMPLATES
        for attr in re.findall(r'class="([^"]*)"', template.read_text())
        for token in attr.split()
    }

    missing = sorted(token for token in tokens - UNSTYLED_CLASSES if not css_selector(token).search(css))

    assert not missing, f"app.css에 없는 클래스: {missing}. README의 빌드 명령으로 CSS를 다시 빌드하세요."
