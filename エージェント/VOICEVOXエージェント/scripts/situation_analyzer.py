# -*- coding: utf-8 -*-
"""
ルールベース状況分析器 — テキストから感情・テンション・文体を自動判定
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class Emotion(str, Enum):
    """感情タイプ"""
    NEUTRAL = "NEUTRAL"
    JOY = "JOY"
    ANGER = "ANGER"
    SADNESS = "SADNESS"
    SURPRISE = "SURPRISE"
    WHISPER = "WHISPER"


class Tension(str, Enum):
    """テンションレベル"""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SpeechStyle(str, Enum):
    """文体"""
    STATEMENT = "STATEMENT"
    QUESTION = "QUESTION"
    COMMAND = "COMMAND"
    MONOLOGUE = "MONOLOGUE"


@dataclass
class SituationProfile:
    """状況分析結果"""
    emotion: Emotion = Emotion.NEUTRAL
    tension: Tension = Tension.MEDIUM
    style: SpeechStyle = SpeechStyle.STATEMENT
    emphasis_words: List[str] = field(default_factory=list)
    confidence: float = 0.5  # 判定の確信度（0.0〜1.0）
    reasoning: str = ""  # 判定根拠

    def to_dict(self) -> dict:
        return {
            "emotion": self.emotion.value,
            "tension": self.tension.value,
            "style": self.style.value,
            "emphasis_words": self.emphasis_words,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


# ─── 感情キーワード辞書 ───
_JOY_KEYWORDS = [
    "やった", "嬉しい", "うれしい", "最高", "素晴らしい", "すごい",
    "楽しい", "たのしい", "ありがとう", "よかった", "幸せ", "しあわせ",
    "わーい", "やったー", "いいね", "素敵", "すてき", "大好き",
    "おめでとう", "最高", "完璧", "かんぺき",
]

_ANGER_KEYWORDS = [
    "ふざけるな", "ふざけんな", "何だと", "なんだと", "バカ", "ばか",
    "アホ", "あほ", "うるさい", "黙れ", "だまれ", "許さない", "ゆるさない",
    "怒り", "いかり", "ふてくされ", "ちくしょう", "くそ", "クソ",
    "いい加減にしろ", "やめろ", "殺す", "ころす", "死ね", "しね",
]

_SADNESS_KEYWORDS = [
    "残念", "ざんねん", "悲しい", "かなしい", "辛い", "つらい",
    "寂しい", "さみしい", "泣きそう", "涙", "なみだ", "切ない", "せつない",
    "帰りたい", "やめたい", "もうだめ", "無理", "むり", "ごめん",
    "申し訳", "もうしわけ", "すまない",
]

_SURPRISE_KEYWORDS = [
    "えっ", "えー", "うそ", "嘘", "まさか", "信じられない",
    "しんじられない", "驚き", "おどろき", "びっくり", "なんと",
    "何", "はっ", "おお",
]

_COMMAND_KEYWORDS = [
    "しろ", "やれ", "行け", "いけ", "来い", "こい",
    "やめろ", "逃げろ", "にげろ", "急げ", "いそげ",
    "ください", "なさい", "してくれ",
]


def analyze(text: str, context: str = "") -> SituationProfile:
    """
    テキストからSituationProfileを生成。

    Args:
        text: 分析対象テキスト
        context: 追加コンテキスト（シーン説明など）

    Returns:
        SituationProfile
    """
    emotion = _detect_emotion(text, context)
    tension = _detect_tension(text)
    style = _detect_style(text)
    emphasis = _detect_emphasis_words(text)
    confidence = _calc_confidence(text, emotion)

    reasons = []
    if emotion != Emotion.NEUTRAL:
        reasons.append(f"感情={emotion.value}")
    reasons.append(f"テンション={tension.value}")
    reasons.append(f"文体={style.value}")
    if emphasis:
        reasons.append(f"強調語={emphasis}")

    return SituationProfile(
        emotion=emotion,
        tension=tension,
        style=style,
        emphasis_words=emphasis,
        confidence=confidence,
        reasoning="、".join(reasons),
    )


def _detect_emotion(text: str, context: str = "") -> Emotion:
    """感情を検出"""
    combined = text + " " + context

    # スコアリング
    scores = {
        Emotion.JOY: sum(1 for kw in _JOY_KEYWORDS if kw in combined),
        Emotion.ANGER: sum(1 for kw in _ANGER_KEYWORDS if kw in combined),
        Emotion.SADNESS: sum(1 for kw in _SADNESS_KEYWORDS if kw in combined),
        Emotion.SURPRISE: sum(1 for kw in _SURPRISE_KEYWORDS if kw in combined),
    }

    # 記号による補強
    if text.count("！") >= 2 or text.count("!") >= 2:
        scores[Emotion.ANGER] += 1
        scores[Emotion.SURPRISE] += 1
    if "…" in text or "。。。" in text:
        scores[Emotion.SADNESS] += 1
    if "♪" in text or "♡" in text or "☆" in text:
        scores[Emotion.JOY] += 1

    # ささやき検出
    if re.search(r"[（(].{0,10}ささやき[）)]", combined) or "（小声）" in combined:
        return Emotion.WHISPER

    # 最大スコアの感情を返す
    max_score = max(scores.values())
    if max_score == 0:
        return Emotion.NEUTRAL

    for emotion, score in scores.items():
        if score == max_score:
            return emotion

    return Emotion.NEUTRAL


def _detect_tension(text: str) -> Tension:
    """テンションを検出"""
    exclamation_count = text.count("！") + text.count("!")
    text_len = len(text)

    # 高テンション条件
    high_signals = 0
    if exclamation_count >= 2:
        high_signals += 2
    elif exclamation_count >= 1:
        high_signals += 1
    if text_len <= 10:
        high_signals += 1
    if any(kw in text for kw in ["すごい", "やった", "最高", "行くぞ"]):
        high_signals += 1

    # 低テンション条件
    low_signals = 0
    if "…" in text or "――" in text:
        low_signals += 1
    if text_len >= 40 and text.count("、") >= 2:
        low_signals += 1
    if any(kw in text for kw in ["しかし", "ところで", "なお", "ただし"]):
        low_signals += 1

    if high_signals >= 2:
        return Tension.HIGH
    if low_signals >= 2:
        return Tension.LOW
    return Tension.MEDIUM


def _detect_style(text: str) -> SpeechStyle:
    """文体を検出"""
    stripped = text.rstrip()

    # 疑問文
    if stripped.endswith("？") or stripped.endswith("?"):
        return SpeechStyle.QUESTION
    if re.search(r"(ですか|ますか|だろうか|のか|かな)[\s？?]*$", stripped):
        return SpeechStyle.QUESTION

    # 命令文
    if any(kw in text for kw in _COMMAND_KEYWORDS):
        return SpeechStyle.COMMAND
    if stripped.endswith("！") or stripped.endswith("!"):
        if any(kw in text for kw in _COMMAND_KEYWORDS[:6]):
            return SpeechStyle.COMMAND

    # 独白
    if re.search(r"(だな|かな|だろう|かもしれない|のだが|ないな|ものだ|よな)[\s。…]*$", stripped):
        return SpeechStyle.MONOLOGUE
    # 「〜な」で終わる独白（助詞「な」）
    if re.search(r"[いうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん]な[\s。…]*$", stripped):
        return SpeechStyle.MONOLOGUE

    return SpeechStyle.STATEMENT


def _detect_emphasis_words(text: str) -> List[str]:
    """強調すべき単語を検出"""
    emphasis = []

    # カッコで囲まれた単語（「」内は強調対象の可能性）
    quoted = re.findall(r"「(.+?)」", text)
    emphasis.extend(quoted)

    # 片仮名で目立つ単語（3文字以上のカタカナ語）
    katakana = re.findall(r"[ァ-ヴー]{3,}", text)
    emphasis.extend(katakana)

    # 繰り返し・強調の「ー」付き
    stretched = re.findall(r"\w+ー{2,}", text)
    emphasis.extend(stretched)

    return emphasis[:5]  # 最大5語まで


def _calc_confidence(text: str, emotion: Emotion) -> float:
    """判定の確信度を計算"""
    if emotion == Emotion.NEUTRAL:
        return 0.3  # ニュートラルは確信度低め

    # シグナルの数で確信度を上げる
    signals = 0
    if emotion == Emotion.ANGER:
        signals = sum(1 for kw in _ANGER_KEYWORDS if kw in text)
    elif emotion == Emotion.JOY:
        signals = sum(1 for kw in _JOY_KEYWORDS if kw in text)
    elif emotion == Emotion.SADNESS:
        signals = sum(1 for kw in _SADNESS_KEYWORDS if kw in text)
    elif emotion == Emotion.SURPRISE:
        signals = sum(1 for kw in _SURPRISE_KEYWORDS if kw in text)

    # シグナル1つ=0.5、2つ=0.7、3つ以上=0.9
    if signals >= 3:
        return 0.9
    if signals >= 2:
        return 0.7
    if signals >= 1:
        return 0.5
    return 0.3
