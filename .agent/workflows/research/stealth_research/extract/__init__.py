# -*- coding: utf-8 -*-
"""
抽出品質メトリクス — コンテンツの品質を定量評価
v3.0: 多段抽出パイプライン（trafilatura → jusText → HTMLParser）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Tuple


@dataclass
class ContentMetrics:
    """コンテンツ品質メトリクス"""
    raw_length: int = 0          # 生HTMLの長さ
    extracted_length: int = 0    # 抽出テキストの長さ
    extraction_ratio: float = 0.0  # テキスト密度（extracted / raw）
    boilerplate_ratio: float = 0.0  # ボイラープレート推定率
    was_truncated: bool = False    # 上限で切り詰められたか
    quality_grade: str = "unknown"  # "high" | "medium" | "low" | "empty"
    extractor_used: str = ""       # 使用した抽出器


# ボイラープレート検知パターン（ナビゲーション、フッター等）
_BOILERPLATE_PATTERNS = [
    r'(?:copyright|©)\s*\d{4}',
    r'all rights reserved',
    r'privacy policy',
    r'terms of (service|use)',
    r'cookie (policy|settings|consent)',
    r'subscribe to our newsletter',
    r'follow us on',
    r'share (this|on)',
    r'related (articles|posts|stories)',
    r'you may also like',
    r'advertisement|sponsored',
    r'sign (up|in)|log (in|out)',
]
_BOILERPLATE_RE = re.compile(
    '|'.join(_BOILERPLATE_PATTERNS), re.IGNORECASE
)

# 非テキストHTMLタグ（除外対象）
_SKIP_TAGS = {'script', 'style', 'noscript', 'svg', 'head', 'meta', 'link'}


class _TextExtractor(HTMLParser):
    """HTMLからテキストを抽出するパーサー（フォールバック用）"""

    def __init__(self):
        super().__init__()
        self._texts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._texts.append(stripped)

    def get_text(self) -> str:
        return '\n'.join(self._texts)


def _extract_trafilatura(html: str) -> str:
    """trafilaturaによる高品質テキスト抽出"""
    try:
        import trafilatura
        result = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_recall=True,
        )
        return result or ""
    except Exception:
        return ""


def _extract_justext(html: str) -> str:
    """jusTextによるテキスト抽出"""
    try:
        import justext
        paragraphs = justext.justext(
            html.encode("utf-8", errors="replace"),
            justext.get_stoplist("English"),
        )
        # good判定のパラグラフのみ
        texts = [p.text for p in paragraphs if not p.is_boilerplate]
        return "\n\n".join(texts) if texts else ""
    except Exception:
        return ""


def _extract_htmlparser(html: str) -> str:
    """HTMLParserによるフォールバック抽出"""
    if not html:
        return ""
    if '<' not in html:
        return html.strip()
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return re.sub(r'<[^>]+>', ' ', html).strip()
    return parser.get_text()


def extract_text_from_html(html: str) -> str:
    """
    多段パイプラインでHTMLからテキストを抽出。
    trafilatura → jusText → HTMLParser の順でフォールバック。

    Args:
        html: 生HTML文字列

    Returns:
        抽出されたテキスト
    """
    if not html:
        return ""

    # 1. trafilatura（最高品質）
    text = _extract_trafilatura(html)
    if len(text) >= 200:
        return text

    # 2. jusText（中間品質）
    text_jt = _extract_justext(html)
    if len(text_jt) > len(text):
        text = text_jt
    if len(text) >= 200:
        return text

    # 3. HTMLParser（フォールバック）
    text_hp = _extract_htmlparser(html)
    if len(text_hp) > len(text):
        text = text_hp

    return text


def extract_with_metadata(html: str) -> Tuple[str, str]:
    """
    テキスト抽出 + 使用した抽出器名を返す。

    Returns:
        (extracted_text, extractor_name)
    """
    if not html:
        return "", "none"

    # 1. trafilatura
    text = _extract_trafilatura(html)
    if len(text) >= 200:
        return text, "trafilatura"

    # 2. jusText
    text_jt = _extract_justext(html)
    best_text = text
    best_name = "trafilatura"
    if len(text_jt) > len(best_text):
        best_text = text_jt
        best_name = "justext"

    if len(best_text) >= 200:
        return best_text, best_name

    # 3. HTMLParser
    text_hp = _extract_htmlparser(html)
    if len(text_hp) > len(best_text):
        best_text = text_hp
        best_name = "htmlparser"

    if best_text:
        return best_text, best_name
    return "", "none"


def compute_metrics(
    raw_content: str,
    extracted_content: str,
    max_chars: int = 15000,
) -> ContentMetrics:
    """
    生HTMLと抽出テキストの品質メトリクスを計算。
    v3.0: 多段抽出パイプライン使用。

    Args:
        raw_content: 生HTML/テキスト（フェッチ結果そのもの）
        extracted_content: フェッチ後にmax_charsで切り詰められたコンテンツ
        max_chars: 最大文字数（切り詰め上限）
    """
    raw_len = len(raw_content) if raw_content else 0
    ext_len = len(extracted_content) if extracted_content else 0

    if raw_len == 0:
        return ContentMetrics(quality_grade="empty")

    # 多段パイプラインでテキスト抽出 + 使用抽出器を記録
    text_only, extractor_used = extract_with_metadata(extracted_content)
    text_len = len(text_only)

    # テキスト密度 = テキスト文字数 / 生コンテンツ文字数
    extraction_ratio = text_len / ext_len if ext_len > 0 else 0.0

    # ボイラープレート推定
    if text_len > 0:
        lines = text_only.split('\n')
        boiler_lines = sum(
            1 for line in lines
            if _BOILERPLATE_RE.search(line)
        )
        boilerplate_ratio = boiler_lines / len(lines) if lines else 0.0
    else:
        boilerplate_ratio = 0.0

    # 切り詰め判定
    was_truncated = ext_len >= max_chars

    # 品質グレード判定
    # テキスト量の絶対値とボイラープレート率の両方で判定
    useful_text_len = int(text_len * (1 - boilerplate_ratio))
    if useful_text_len >= 3000:
        quality_grade = "high"
    elif useful_text_len >= 500:
        quality_grade = "medium"
    elif useful_text_len > 0:
        quality_grade = "low"
    else:
        quality_grade = "empty"

    return ContentMetrics(
        raw_length=raw_len,
        extracted_length=text_len,
        extraction_ratio=round(extraction_ratio, 4),
        boilerplate_ratio=round(boilerplate_ratio, 4),
        was_truncated=was_truncated,
        quality_grade=quality_grade,
        extractor_used=extractor_used,
    )
