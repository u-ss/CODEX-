# -*- coding: utf-8 -*-
"""
テキスト前処理 — 長文分割・伸ばし音補完・読点挿入
"""
from __future__ import annotations

import re
from typing import List


# 伸ばし音補完の対象パターン（カタカナ語で長音が省略されがちなもの）
_ELONGATION_PATTERNS = [
    # (正規表現パターン, 置換後)
    (r"インフィニティ(?!ー)", "インフィニティー"),
    (r"エネルギ(?!ー)", "エネルギー"),
    (r"カテゴリ(?!ー)", "カテゴリー"),
    (r"メモリ(?!ー)", "メモリー"),
    (r"バッテリ(?!ー)", "バッテリー"),
    (r"アクセサリ(?!ー)", "アクセサリー"),
    (r"ストーリ(?!ー)", "ストーリー"),
    (r"セキュリティ(?!ー)", "セキュリティー"),
]


def split_long_text(text: str, max_chars: int = 80) -> List[str]:
    """
    長文を自然な位置で分割。

    分割優先順位:
    1. 改行
    2. 句点「。」
    3. 読点「、」
    4. max_chars超過時は強制分割
    """
    # まず改行で分割
    lines = text.strip().split("\n")
    result = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if len(line) <= max_chars:
            result.append(line)
            continue

        # 句点で分割
        segments = _split_by_delimiter(line, "。", max_chars)
        for seg in segments:
            if len(seg) <= max_chars:
                result.append(seg)
            else:
                # 読点で分割
                sub_segments = _split_by_delimiter(seg, "、", max_chars)
                result.extend(sub_segments)

    return result


def _split_by_delimiter(text: str, delimiter: str, max_chars: int) -> List[str]:
    """デリミタで分割（デリミタは前のチャンクに含める）"""
    parts = text.split(delimiter)
    result = []
    current = ""

    for i, part in enumerate(parts):
        candidate = current + part + (delimiter if i < len(parts) - 1 else "")
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            if current:
                result.append(current.strip())
            current = part + (delimiter if i < len(parts) - 1 else "")

    if current.strip():
        result.append(current.strip())

    return result


def fix_elongation(text: str) -> str:
    """カタカナ語の伸ばし音を補完"""
    result = text
    for pattern, replacement in _ELONGATION_PATTERNS:
        result = re.sub(pattern, replacement, result)
    return result


def insert_breath_points(text: str, interval: int = 40) -> str:
    """
    長い文に読点（呼吸ポイント）を自動挿入。

    interval文字ごとに、助詞（は/が/を/に/で/と/も/の）の後に読点を入れる。
    既に読点がある場合はスキップ。
    """
    if len(text) <= interval:
        return text

    # 助詞パターン（読点がまだない位置）
    # 「助詞 + 非読点文字」を「助詞 + 読点 + 非読点文字」に
    particles = r"([はがをにでともへ])([^\s、。？！」』）\)）])"

    result = text
    last_comma_pos = 0

    # intervalごとにチェック
    for i in range(interval, len(result), interval):
        # i付近（±10文字）で助詞を探す
        search_start = max(last_comma_pos + 5, i - 10)
        search_end = min(len(result), i + 10)
        segment = result[search_start:search_end]

        match = re.search(particles, segment)
        if match:
            insert_pos = search_start + match.start() + 1
            # すでに直後に読点がないか確認
            if insert_pos < len(result) and result[insert_pos] != "、":
                result = result[:insert_pos] + "、" + result[insert_pos:]
                last_comma_pos = insert_pos

    return result


def preprocess(text: str, max_chars: int = 80) -> List[str]:
    """
    テキスト前処理パイプライン。

    1. 伸ばし音補完
    2. 呼吸ポイント挿入
    3. 長文分割

    Returns:
        分割されたテキストのリスト
    """
    # Step 1: 伸ばし音補完
    text = fix_elongation(text)

    # Step 2: 呼吸ポイント挿入（分割前に全体に適用）
    text = insert_breath_points(text)

    # Step 3: 長文分割
    segments = split_long_text(text, max_chars)

    return segments
