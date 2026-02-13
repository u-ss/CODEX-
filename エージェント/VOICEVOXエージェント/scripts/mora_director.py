# -*- coding: utf-8 -*-
"""
モーラレベル演出プランナー — gpt-oss:20bで文脈に基づく演出指示を生成

テキストとコンテキストをLLMに渡し、モーラ単位の演出指示を取得:
  - 母音を伸ばす箇所
  - ポーズ（間）を入れる箇所
  - 強調する箇所
  - 速度を変える箇所

使い方:
    directions = generate_mora_directions(segments, context="...")
    query = apply_mora_directions(query, directions, segment_index=0)
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
class MoraDirection:
    """1つのモーラ演出指示"""
    segment_index: int          # セグメント番号
    word: str                   # 対象ワード
    mora_text: str = ""         # 対象モーラ（空=ワード全体）
    stretch: float = 1.0        # 母音伸縮率 (0.5〜2.0, 1.0=変更なし)
    pitch_shift: float = 0.0    # pitch変化量 (-0.3〜0.3)
    pause_before: float = 0.0   # ワード前のポーズ（秒）
    pause_after: float = 0.0    # ワード後のポーズ（秒）
    reason: str = ""            # 演出理由


@dataclass
class PunctuationDirection:
    """句読点ポーズ＋テンポ指示"""
    segment_index: int
    comma_pause: float = 0.3     # 読点のポーズ（秒）
    period_pause: float = 0.5    # 句点のポーズ（秒）
    speed: float = 0.95          # テンポ（0.7〜1.1, 低い=ゆっくり）
    reason: str = ""


@dataclass
class MoraDirectionPlan:
    """モーラ演出プラン全体"""
    directions: List[MoraDirection] = field(default_factory=list)
    punctuation: List[PunctuationDirection] = field(default_factory=list)
    context_summary: str = ""
    model_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "directions": [
                {
                    "segment_index": d.segment_index,
                    "word": d.word,
                    "mora_text": d.mora_text,
                    "stretch": d.stretch,
                    "pitch_shift": d.pitch_shift,
                    "pause_before": d.pause_before,
                    "pause_after": d.pause_after,
                    "reason": d.reason,
                }
                for d in self.directions
            ],
            "punctuation": [
                {
                    "segment_index": p.segment_index,
                    "comma_pause": p.comma_pause,
                    "period_pause": p.period_pause,
                    "speed": p.speed,
                    "reason": p.reason,
                }
                for p in self.punctuation
            ],
            "context_summary": self.context_summary,
            "model_used": self.model_used,
        }


# ═══════════════════════════════════════════════
# ollama呼び出し
# ═══════════════════════════════════════════════

def _call_ollama(
    prompt: str,
    model: str = "gpt-oss:20b",
    *,
    temperature: float = 0.3,
) -> Optional[str]:
    """ollamaにプロンプトを送信し応答を返す"""
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
        logger.warning("ollamaライブラリ未インストール")
        return None
    except Exception as e:
        logger.warning(f"ollama呼び出し失敗: {e}")
        return None


# ═══════════════════════════════════════════════
# プロンプト
# ═══════════════════════════════════════════════

_PROMPT_TEMPLATE = """\
You are a Japanese voice acting director. Your job is to add expressive nuance to TTS speech.

Given the text segments and context below, decide WHERE to add vocal expression at the word/mora level.

## What you can control

### Word-level expression
1. **stretch** (0.7-1.8): Elongate a vowel for emphasis or emotion. 1.0=normal. Use 1.3-1.5 for gentle emphasis, 1.5-1.8 for dramatic.
2. **pitch_shift** (-0.2 to 0.2): Slightly raise or lower pitch. Use sparingly. Positive=brighter, Negative=darker.
3. **pause_before** (0-0.5s): Add a pause BEFORE a word for dramatic effect or scene change.
4. **pause_after** (0-0.5s): Add a pause AFTER a word for emphasis or to let meaning sink in.

### Segment-level pacing (punctuation & tempo)
For EACH segment, you control:
- **comma_pause** (、): How long to pause at commas. Range: 0.2-0.8s. Default: 0.35s.
  - Casual speech: 0.2-0.3s. Narrative: 0.3-0.5s. Dramatic/contemplative: 0.4-0.8s.
- **period_pause** (。): How long to pause at sentence end. Range: 0.5-1.5s. Default: 1.0s.
  - Casual: 0.5-0.7s. Narrative: 0.8-1.0s. Dramatic/contemplative: 1.0-1.5s.
- **speed** (0.7-1.1): Reading tempo for this segment. Default: 0.95.
  - Fast/excited: 1.0-1.1. Normal: 0.9-1.0. Slow/contemplative: 0.8-0.9. Very slow/dramatic: 0.7-0.8.

IMPORTANT: Pauses are actual silence duration in seconds. Err on the generous side.
Period pauses (。) should be AT LEAST 0.8s for any narration. 1.0s is the sweet spot for most narrative speech.
Vary tempo and pauses across segments - don't use the same values for every segment.

## Rules
- Only add directions where they genuinely improve expression. Less is more.
- Max 2-4 word directions per segment. Don't over-decorate.
- Use the context to inform your choices (sad scene = slower, stretched vowels; tense scene = shorter, clipped)
- "word" must be an EXACT substring from the original text
- "mora_text" is optional - specify a katakana mora to target a specific sound within the word
- Write "reason" in English to avoid encoding issues
- MUST include punctuation entry for EVERY segment - vary values based on content

## Output JSON format
{{
  "directions": [
    {{
      "segment_index": 0,
      "word": "exact word from text",
      "mora_text": "",
      "stretch": 1.0,
      "pitch_shift": 0.0,
      "pause_before": 0.0,
      "pause_after": 0.0,
      "reason": "why this direction"
    }}
  ],
  "punctuation": [
    {{
      "segment_index": 0,
      "comma_pause": 0.4,
      "period_pause": 1.0,
      "speed": 0.90,
      "reason": "why this pacing"
    }}
  ],
  "context_summary": "brief english summary of the overall mood"
}}

## Context
{context}

## Text Segments
{segments}

Output the direction JSON. Remember: subtle, tasteful, and purposeful.
"""


def _build_prompt(segments: List[str], context: str) -> str:
    seg_text = "\n".join(f"[{i}] {s}" for i, s in enumerate(segments))
    return _PROMPT_TEMPLATE.format(context=context, segments=seg_text)


# ═══════════════════════════════════════════════
# JSON解析
# ═══════════════════════════════════════════════

def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
    """LLM出力からJSONを抽出"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    logger.warning(f"JSONパース失敗: {raw[:200]}")
    return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _json_to_plan(data: Dict[str, Any], model: str) -> MoraDirectionPlan:
    """JSONをMoraDirectionPlanに変換"""
    plan = MoraDirectionPlan(
        context_summary=data.get("context_summary", ""),
        model_used=model,
    )
    for d in data.get("directions", []):
        plan.directions.append(MoraDirection(
            segment_index=d.get("segment_index", 0),
            word=d.get("word", ""),
            mora_text=d.get("mora_text", ""),
            stretch=_clamp(d.get("stretch", 1.0), 0.5, 2.0),
            pitch_shift=_clamp(d.get("pitch_shift", 0.0), -0.3, 0.3),
            pause_before=_clamp(d.get("pause_before", 0.0), 0.0, 1.0),
            pause_after=_clamp(d.get("pause_after", 0.0), 0.0, 1.0),
            reason=d.get("reason", ""),
        ))
    # 句読点ポーズ＋テンポの解析
    for p in data.get("punctuation", []):
        plan.punctuation.append(PunctuationDirection(
            segment_index=p.get("segment_index", 0),
            comma_pause=_clamp(p.get("comma_pause", 0.35), 0.1, 1.0),
            period_pause=_clamp(p.get("period_pause", 0.6), 0.2, 1.5),
            speed=_clamp(p.get("speed", 0.95), 0.7, 1.1),
            reason=p.get("reason", ""),
        ))
    return plan


# ═══════════════════════════════════════════════
# メインAPI
# ═══════════════════════════════════════════════

def generate_mora_directions(
    segments: List[str],
    context: str = "",
    *,
    model: str = "gpt-oss:20b",
    temperature: float = 0.3,
) -> Optional[MoraDirectionPlan]:
    """
    テキストセグメントからモーラレベルの演出プランを生成する。

    Args:
        segments: 分割済みテキストリスト
        context: 場面の説明（日本語OK、英語推奨）
        model: ollamaモデル名
    """
    prompt = _build_prompt(segments, context)

    logger.info(f"モーラ演出プランナー: {model} に送信中...")
    raw = _call_ollama(prompt, model, temperature=temperature)

    if raw is None:
        logger.warning("ollama応答なし")
        return None

    logger.info(f"応答受信 ({len(raw)} chars)")

    data = _parse_json(raw)
    if data is None:
        return None

    plan = _json_to_plan(data, model)
    logger.info(f"モーラ演出プラン: {len(plan.directions)}件の指示")
    return plan


# ═══════════════════════════════════════════════
# audio_queryへの適用
# ═══════════════════════════════════════════════

def apply_mora_directions(
    query: Dict[str, Any],
    plan: MoraDirectionPlan,
    segment_index: int,
) -> Dict[str, Any]:
    """
    モーラ演出指示をaudio_queryに適用する。

    指定セグメントに該当するdirectionのみ適用。
    """
    accent_phrases = query.get("accent_phrases", [])
    if not accent_phrases:
        return query

    # このセグメントの指示を抽出
    dirs = [d for d in plan.directions if d.segment_index == segment_index]

    for d in dirs:
        applied = False
        for pi, phrase in enumerate(accent_phrases):
            moras = phrase.get("moras", [])

            # ワードをモーラ列から探す（テキストマッチ）
            phrase_text = "".join(m.get("text", "") for m in moras)

            # ワードがこのフレーズに含まれるか
            if d.word and d.word not in _mora_text_to_original(phrase_text, query):
                continue

            for mi, mora in enumerate(moras):
                # 特定モーラ指定があればそれにマッチ
                if d.mora_text and mora.get("text", "") != d.mora_text:
                    continue

                # モーラ指定がなければワード内の全モーラに適用
                if not d.mora_text:
                    # stretch適用
                    if d.stretch != 1.0:
                        vl = mora.get("vowel_length", 0.15)
                        mora["vowel_length"] = vl * d.stretch

                    # pitch_shift適用
                    if d.pitch_shift != 0.0:
                        p = mora.get("pitch", 5.5)
                        if p > 0:
                            mora["pitch"] = p + d.pitch_shift

                    applied = True
                    break  # ワード全体なら最初のモーラだけに適用して次へ
                else:
                    # 特定モーラに適用
                    if d.stretch != 1.0:
                        vl = mora.get("vowel_length", 0.15)
                        mora["vowel_length"] = vl * d.stretch

                    if d.pitch_shift != 0.0:
                        p = mora.get("pitch", 5.5)
                        if p > 0:
                            mora["pitch"] = p + d.pitch_shift

                    applied = True

            # pause_before: このフレーズの直前にポーズ挿入
            if d.pause_before > 0 and applied and pi > 0:
                prev = accent_phrases[pi - 1]
                if prev.get("pause_mora"):
                    prev["pause_mora"]["vowel_length"] = max(
                        prev["pause_mora"].get("vowel_length", 0),
                        d.pause_before,
                    )

            # pause_after: このフレーズの直後にポーズ
            if d.pause_after > 0 and applied:
                pau = phrase.get("pause_mora")
                if pau:
                    pau["vowel_length"] = max(
                        pau.get("vowel_length", 0),
                        d.pause_after,
                    )

            if applied:
                break

    # ── 句読点ポーズ適用 ──
    # セグメント内のpause_moraは全て「、」（読点）のポーズ
    # 「。」（句点）のポーズはセグメント間の無音として外部で制御
    punct_dirs = [p for p in plan.punctuation if p.segment_index == segment_index]
    if punct_dirs:
        pd = punct_dirs[0]
        for phrase in accent_phrases:
            pau = phrase.get("pause_mora")
            if pau and pau.get("vowel") == "pau":
                pau["vowel_length"] = pd.comma_pause

    return query


def get_period_pause(plan: MoraDirectionPlan, segment_index: int) -> float:
    """指定セグメントの句点（。）ポーズ秒数を返す"""
    for p in plan.punctuation:
        if p.segment_index == segment_index:
            return p.period_pause
    return 1.0  # デフォルト


def _mora_text_to_original(mora_text: str, query: Dict[str, Any]) -> str:
    """カタカナモーラテキストから元テキストへの大まかなマッチ用"""
    # query全体のテキストからマッチを試みる
    # 簡易実装: kanaを元テキストとして使う
    return mora_text
