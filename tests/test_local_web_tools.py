from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT / ".agent" / "workflows" / "research"
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from lib.local_web_tools import LocalWebTools  # noqa: E402


class _Resp:
    def __init__(self, text: str, *, status_code: int = 200, ctype: str = "text/html"):
        self.text = text
        self.status_code = status_code
        self.headers = {"Content-Type": ctype}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_local_web_tools_search_web_parses_rss(monkeypatch) -> None:
    rss = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item><title>T1</title><link>https://example.com/a</link><description>S1</description><pubDate>P1</pubDate></item>
<item><title>T2</title><link>https://example.com/b</link><description>S2</description></item>
</channel></rss>
"""

    def _fake_get(url, params=None, timeout=None, headers=None):  # noqa: ARG001
        return _Resp(rss, ctype="application/rss+xml")

    monkeypatch.setattr("lib.local_web_tools.requests.get", _fake_get)
    tools = LocalWebTools()
    rows = tools.search_web("unity mcp")

    assert len(rows) == 2
    assert rows[0]["url"] == "https://example.com/a"
    assert rows[0]["published_at"] == "P1"
    assert rows[1]["published_at"] is None


def test_local_web_tools_read_url_content_extracts_text(monkeypatch) -> None:
    html = """<html><head><title>Title</title></head>
<body><h1>H1</h1><p>Para one.</p><script>ignored()</script><li>Item</li></body></html>"""

    def _fake_get(url, timeout=None, headers=None):  # noqa: ARG001
        return _Resp(html, ctype="text/html; charset=utf-8")

    monkeypatch.setattr("lib.local_web_tools.requests.get", _fake_get)
    tools = LocalWebTools(max_chars=200)
    text = tools.read_url_content("https://example.com")

    assert "Title" in text
    assert "H1" in text
    assert "Para one." in text
    assert "ignored()" not in text

