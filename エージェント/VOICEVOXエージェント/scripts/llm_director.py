# -*- coding: utf-8 -*-
"""
LLM演出プランナー — Layer 3A: テキストの演出指示を自動生成する

ollama (llama3.2) にテキストを渡し、以下を構造化JSONで取得:
  - 各セグメントの感情/速度/抑揚/ポーズ
  - 強調ワードのインデックス

テキスト分割は呼び出し側で行い、LLMにはパラメータ決定のみ担当させる。
（日本語テキストをLLMに生成させるとエンコーディング問題が出るため）

使い方:
    segments = ["滲み出す、混濁の紋章。", "不遜なる、狂気の器。", ...]
    plan = generate_direction(segments, hint="BLEACHの鬼道詠唱")
    # → DirectionPlan(segments=[SegmentDirection(...), ...])
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# データ構造
# ═══════════════════════════════════════════════


@dataclass
class SegmentDirection:
    """1セグメントの演出指示"""
    text: str                          # セグメントテキスト（元テキスト）
    emotion: str = "neutral"           # 感情 (neutral/joy/anger/sadness/dark/solemn/...)
    speed: float = 1.0                 # 話速 (0.7〜1.3)
    intonation: float = 1.1            # 抑揚 (0.8〜1.5)
    pause_after_sec: float = 0.5       # セグメント後の無音（秒）
    note: str = ""                     # 演出メモ


@dataclass
class DirectionPlan:
    """テキスト全体の演出プラン"""
    segments: List[SegmentDirection] = field(default_factory=list)
    reading_fixes: Dict[str, str] = field(default_factory=dict)  # 漢字→読み
    context: str = ""                  # テキストの文脈説明
    model_used: str = ""               # 使用モデル名

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segments": [
                {
                    "text": s.text,
                    "emotion": s.emotion,
                    "speed": s.speed,
                    "intonation": s.intonation,
                    "pause_after_sec": s.pause_after_sec,
                    "note": s.note,
                }
                for s in self.segments
            ],
            "reading_fixes": self.reading_fixes,
            "context": self.context,
            "model_used": self.model_used,
        }


# ═══════════════════════════════════════════════
# ollamaクライアント
# ═══════════════════════════════════════════════


def _call_ollama(
    prompt: str,
    model: str = "llama3.2",
    *,
    temperature: float = 0.3,
) -> Optional[str]:
    """ollamaにプロンプトを送り、レスポンスを返す"""
    try:
        import ollama as ollama_lib
        response = ollama_lib.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": temperature},
            format="json",
        )
        return response["message"]["content"]
    except ImportError:
        logger.warning("ollamaライブラリ未インストール。pip install ollama")
        return None
    except Exception as e:
        logger.warning(f"ollama呼び出し失敗: {e}")
        return None


# ═══════════════════════════════════════════════
# プロンプト（インデックスベース方式）
# ═══════════════════════════════════════════════

_SYSTEM_PROMPT = """\
You are a TTS (Text-to-Speech) direction planner for Japanese voice synthesis.
You will receive a list of Japanese text segments with their indices.
Your job is to decide the best vocal parameters for each segment.

## Output Format (JSON only, no markdown)
{
  "directions": [
    {
      "index": 0,
      "emotion": "neutral",
      "speed": 1.0,
      "intonation": 1.1,
      "pause_after_sec": 0.5,
      "note": "brief note about direction"
    }
  ],
  "reading_fixes": {"kanji": "katakana_reading"},
  "context": "brief context description"
}

## Parameter Ranges
- speed: 0.7 (slow/dramatic) to 1.3 (fast). Default=1.0
- intonation: 0.8 (monotone) to 1.5 (expressive). Default=1.1
- pause_after_sec: 0.0 to 2.0 seconds. Default=0.5. Last segment=0.0
- emotion: neutral, joy, anger, sadness, surprise, whisper, dark, solemn, command, dramatic

## Rules
- For dramatic/solemn texts: use slower speed (0.75-0.85) and higher intonation (1.2-1.5)
- Commands/orders: slightly faster (0.9), high intonation (1.3)
- Climax/final segment: slowest speed, highest intonation, longest pause before it
- reading_fixes: list kanji that TTS might misread, with correct katakana. Key=kanji, value=katakana
- Use English for "note" and "context" to avoid encoding issues
"""


def _build_prompt(segments: List[str], *, hint: str = "") -> str:
    """LLMに送るプロンプトを構築"""
    prompt = _SYSTEM_PROMPT + "\n\n## Input Segments\n\n"
    for i, seg in enumerate(segments):
        prompt += f"[{i}] {seg}\n"
    if hint:
        prompt += f"\n## Context Hint\n{hint}\n"
    prompt += "\nOutput the direction JSON for the above segments."
    return prompt


# ═══════════════════════════════════════════════
# JSON解析
# ═══════════════════════════════════════════════


def _parse_direction_json(raw: str) -> Optional[Dict[str, Any]]:
    """LLMの出力からJSONを抽出・パースする"""
    # 直接パース
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # コードブロックから
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # {...} を探す
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning(f"JSONパース失敗: {raw[:200]}")
    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _json_to_plan(
    data: Dict[str, Any],
    original_segments: List[str],
    model: str,
) -> DirectionPlan:
    """パース済みJSONとオリジナルテキストからDirectionPlanを構築"""
    plan = DirectionPlan(
        reading_fixes=data.get("reading_fixes", {}),
        context=data.get("context", ""),
        model_used=model,
    )

    # LLMの出力をインデックスでマッピング
    directions = {
        d["index"]: d
        for d in data.get("directions", [])
        if "index" in d
    }

    for i, text in enumerate(original_segments):
        d = directions.get(i, {})
        plan.segments.append(SegmentDirection(
            text=text,  # 元テキストをそのまま使う
            emotion=d.get("emotion", "neutral"),
            speed=_clamp(d.get("speed", 1.0), 0.7, 1.3),
            intonation=_clamp(d.get("intonation", 1.1), 0.8, 1.5),
            pause_after_sec=_clamp(d.get("pause_after_sec", 0.5), 0.0, 2.0),
            note=d.get("note", ""),
        ))

    return plan


# ═══════════════════════════════════════════════
# テキスト自動分割
# ═══════════════════════════════════════════════


def split_text_to_segments(text: str) -> List[str]:
    """
    テキストを意味の区切りで自動分割する。
    「。」「！」「？」で分割し、空文を除外。
    """
    # まず句点で分割
    segments = re.split(r'(?<=[。！？])', text)
    segments = [s.strip() for s in segments if s.strip()]

    # 長いセグメントはさらに分割（30文字超のものを中黒「・」やスペースで分割）
    result = []
    for seg in segments:
        if len(seg) > 30 and ('・' in seg or ' ' in seg or '　' in seg):
            # 中黒やスペースで分割
            parts = re.split(r'[・ 　]', seg)
            for part in parts:
                part = part.strip()
                if part:
                    result.append(part)
        else:
            result.append(seg)

    return result if result else [text]


# ═══════════════════════════════════════════════
# メインAPI
# ═══════════════════════════════════════════════


def generate_direction(
    segments: List[str],
    *,
    model: str = "llama3.2",
    hint: str = "",
    temperature: float = 0.3,
) -> Optional[DirectionPlan]:
    """
    セグメントリストからLLMで演出プランを生成する。

    Args:
        segments: 分割済みテキストリスト
        model: ollamaモデル名
        hint: コンテキストヒント（英語推奨）
        temperature: 生成温度（低い=安定）

    Returns:
        DirectionPlan、失敗時はNone
    """
    prompt = _build_prompt(segments, hint=hint)

    logger.info(f"LLM演出プランナー: {model} に送信中... ({len(segments)}セグメント)")
    raw = _call_ollama(prompt, model, temperature=temperature)

    if raw is None:
        logger.warning("ollama応答なし。LLM演出スキップ。")
        return None

    logger.info(f"LLM応答受信 ({len(raw)} chars)")

    data = _parse_direction_json(raw)
    if data is None:
        return None

    plan = _json_to_plan(data, segments, model)
    logger.info(
        f"演出プラン生成完了: {len(plan.segments)}セグメント, "
        f"{len(plan.reading_fixes)}読み修正"
    )
    return plan
