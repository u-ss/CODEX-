from __future__ import annotations

import re


PUNCTUATION_PATTERN = re.compile(r"([、。！？!?])")


def split_subtitle_lines(text: str, max_chars: int, max_lines: int = 2) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    chunks = _split_by_punctuation(raw)
    lines: list[str] = []
    current = ""
    for chunk in chunks:
        candidate = f"{current}{chunk}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = chunk
        else:
            lines.extend(_hard_wrap(chunk, max_chars))
            current = ""
    if current:
        lines.append(current)
    lines = _enforce_kinsoku(lines, max_chars=max_chars)
    if len(lines) <= max_lines:
        return lines
    merged = lines[: max_lines - 1]
    merged.append("".join(lines[max_lines - 1 :]))
    if len(merged[-1]) > max_chars:
        merged[-1] = merged[-1][: max_chars]
    return _enforce_kinsoku(merged, max_chars=max_chars)


def _split_by_punctuation(text: str) -> list[str]:
    parts = PUNCTUATION_PATTERN.split(text)
    merged: list[str] = []
    for item in parts:
        if not item:
            continue
        if item in {"、", "。", "！", "？", "!", "?"} and merged:
            merged[-1] += item
        else:
            merged.append(item)
    return merged


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    return [text[idx : idx + max_chars] for idx in range(0, len(text), max_chars)]


def _enforce_kinsoku(lines: list[str], *, max_chars: int) -> list[str]:
    if not lines:
        return lines
    fixed = [lines[0]]
    for line in lines[1:]:
        if line and line[0] in "、。！？!?":
            prev = fixed[-1]
            if len(prev) < max_chars:
                fixed[-1] = prev + line[0]
                line = line[1:]
        if line:
            fixed.append(line)
    return fixed
