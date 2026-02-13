# -*- coding: utf-8 -*-
"""
Local web tools for /research CLI.

LLMを使わずに `search_web` / `read_url_content` を提供する最小アダプタ。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AntigravityResearch/1.0"


class LocalWebTools:
    """Bing RSS + page fetch の軽量実装。"""

    def __init__(self, *, timeout_sec: int = 20, max_chars: int = 8000):
        self.timeout_sec = timeout_sec
        self.max_chars = max_chars

    def search_web(self, query: str) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "format": "rss"},
            timeout=self.timeout_sec,
            headers={"User-Agent": _UA},
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []

        rows: List[Dict[str, Any]] = []
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            pub = (item.findtext("pubDate") or "").strip() or None
            if not url:
                continue
            rows.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": desc,
                    "published_at": pub,
                }
            )
        return rows

    def read_url_content(self, url: str) -> str:
        url = (url or "").strip()
        if not url:
            return ""
        resp = requests.get(
            url,
            timeout=self.timeout_sec,
            headers={"User-Agent": _UA},
        )
        resp.raise_for_status()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        text = resp.text or ""
        if "text/html" not in ctype:
            return text[: self.max_chars]

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe"]):
            tag.decompose()

        chunks: List[str] = []
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        if title:
            chunks.append(title)
        for node in soup.select("h1,h2,h3,p,li"):
            t = node.get_text(" ", strip=True)
            if t:
                chunks.append(t)

        merged = "\n".join(chunks)
        merged = re.sub(r"[ \t]+", " ", merged)
        merged = re.sub(r"\n{3,}", "\n\n", merged).strip()
        return merged[: self.max_chars]

