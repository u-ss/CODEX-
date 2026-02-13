# -*- coding: utf-8 -*-
"""
Research Agent - Evidence Locator

目的:
Evidence を「URL + 見出し + 段落 + 引用範囲」で追跡できるようにし、
監査可能（どこに書いてあるか）を機械的に担保する。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any, Optional


@dataclass(frozen=True)
class Locator:
    url: str
    heading: str
    paragraph_index: int
    char_start: Optional[int]  # paragraph-local start (preferred)
    char_end: Optional[int]    # paragraph-local end (preferred)
    match_type: str  # "exact" | "normalized" | "unknown"
    quote_hash: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "url": self.url,
                "heading": self.heading,
                "paragraph_index": int(self.paragraph_index),
                "char_start": self.char_start,
                "char_end": self.char_end,
                "match_type": self.match_type,
                "quote_hash": self.quote_hash,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _quote_hash(text: str) -> str:
    return sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").split())

def _looks_like_html(content: str) -> bool:
    s = (content or "").lower()
    return "<html" in s or "<body" in s or "<p" in s or "</" in s


class _BlockHTMLParser(HTMLParser):
    """
    最小依存で HTML を「見出しコンテキスト付きの段落」に変換する。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_heading: str = ""
        self._heading_buf: list[str] = []
        self._para_buf: list[str] = []
        self._in_heading = False
        self._in_para = False
        self.blocks: list[dict[str, Any]] = []  # {"heading": str, "text": str}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_para()
            self._in_heading = True
            self._heading_buf = []
        elif t in ("p", "li"):
            self._flush_para()
            self._in_para = True
            self._para_buf = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = False
            heading = _normalize_ws("".join(self._heading_buf))
            if heading:
                self.current_heading = heading
            self._heading_buf = []
        elif t in ("p", "li"):
            self._in_para = False
            self._flush_para()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self._in_heading:
            self._heading_buf.append(data)
        elif self._in_para:
            self._para_buf.append(data)

    def _flush_para(self) -> None:
        txt = _normalize_ws("".join(self._para_buf))
        self._para_buf = []
        if not txt:
            return
        self.blocks.append({"heading": self.current_heading, "text": txt})


def _extract_blocks(content: str) -> list[dict[str, Any]]:
    """
    content を blocks（heading+paragraph text）に分解。
    - HTMLらしければ HTMLParser で <h*> と <p>/<li> を抽出
    - それ以外は空行区切りの段落、Markdown見出し(#)をheadingとして扱う
    """
    if _looks_like_html(content):
        parser = _BlockHTMLParser()
        try:
            parser.feed(content)
            parser.close()
        except Exception:
            return [{"heading": "", "text": _normalize_ws(content)}] if content.strip() else []
        if parser.blocks:
            return parser.blocks
        # fallback: strip to single block
        return [{"heading": "", "text": _normalize_ws(content)}] if content.strip() else []

    blocks: list[dict[str, Any]] = []
    heading = ""
    para_buf: list[str] = []
    for line in (content or "").splitlines():
        l = line.rstrip()
        if l.strip().startswith("#"):
            # flush current paragraph
            if para_buf:
                txt = _normalize_ws("\n".join(para_buf))
                if txt:
                    blocks.append({"heading": heading, "text": txt})
                para_buf = []
            heading = _normalize_ws(l.lstrip("#").strip())
            continue
        if not l.strip():
            if para_buf:
                txt = _normalize_ws("\n".join(para_buf))
                if txt:
                    blocks.append({"heading": heading, "text": txt})
                para_buf = []
            continue
        para_buf.append(l)

    if para_buf:
        txt = _normalize_ws("\n".join(para_buf))
        if txt:
            blocks.append({"heading": heading, "text": txt})
    if not blocks and (content or "").strip():
        blocks.append({"heading": "", "text": _normalize_ws(content)})
    return blocks


def build_locator(*, url: str, content: str, quote: str) -> Optional[str]:
    """
    content と quote から Locator を生成。
    - exact match が最優先
    - 失敗したら whitespace 正規化後に normalized match を試す
    """
    if not url or not (quote or "").strip() or not (content or "").strip():
        return None

    qh = _quote_hash(quote)

    blocks = _extract_blocks(content)

    def find_in_text(text: str, needle: str) -> Optional[tuple[int, int]]:
        idx = text.find(needle)
        if idx < 0:
            return None
        return idx, idx + len(needle)

    # 1) exact match within paragraph blocks
    for i, b in enumerate(blocks):
        txt = str(b.get("text") or "")
        found = find_in_text(txt, quote)
        if found:
            start, end = found
            return Locator(
                url=url,
                heading=str(b.get("heading") or ""),
                paragraph_index=i,
                char_start=int(start),
                char_end=int(end),
                match_type="exact",
                quote_hash=qh,
            ).to_json()

    # 2) normalized whitespace match within blocks
    norm_quote = _normalize_ws(quote)
    for i, b in enumerate(blocks):
        txt = _normalize_ws(str(b.get("text") or ""))
        found2 = find_in_text(txt, norm_quote)
        if found2:
            start, end = found2
            return Locator(
                url=url,
                heading=str(b.get("heading") or ""),
                paragraph_index=i,
                char_start=int(start),
                char_end=int(end),
                match_type="normalized",
                quote_hash=qh,
            ).to_json()

    return None


def parse_locator(locator: Any) -> Optional[dict[str, Any]]:
    if not locator:
        return None
    if isinstance(locator, dict):
        return locator
    if not isinstance(locator, str):
        return None
    s = locator.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def is_strong_locator(locator: Any) -> bool:
    """
    “採用可能”なlocatorか（最小要件）。
    """
    obj = parse_locator(locator)
    if not obj:
        return False
    if not str(obj.get("url") or "").strip():
        return False
    if obj.get("char_start") is None or obj.get("char_end") is None:
        return False
    if int(obj["char_end"]) <= int(obj["char_start"]):
        return False
    return True
