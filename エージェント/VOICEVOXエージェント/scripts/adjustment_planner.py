# -*- coding: utf-8 -*-
"""
調整プラン策定 — 状況分析結果からVOICEVOXパラメータセットを生成
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .situation_analyzer import Emotion, SpeechStyle, SituationProfile, Tension
from .presets import PresetManager, VoicePreset


@dataclass
class MoraAdjustment:
    """モーラ単位の調整指示"""
    phrase_idx: int
    action: str  # "boost" | "fall" | "rise"
    amount: float = 0.3

    def to_dict(self) -> dict:
        return {
            "phrase_idx": self.phrase_idx,
            "action": self.action,
            "amount": self.amount,
        }


@dataclass
class AdjustmentPlan:
    """調整プラン（Antigravityレビュー対象）"""
    preset_name: str
    speed: float = 1.0
    pitch: float = 0.0
    intonation: float = 1.0
    volume: float = 1.0
    style_name: Optional[str] = None  # VOICEVOX感情スタイル名
    mora_adjustments: List[MoraAdjustment] = field(default_factory=list)
    reasoning: str = ""  # 策定根拠

    def to_dict(self) -> dict:
        return {
            "preset_name": self.preset_name,
            "speed": self.speed,
            "pitch": self.pitch,
            "intonation": self.intonation,
            "volume": self.volume,
            "style_name": self.style_name,
            "mora_adjustments": [m.to_dict() for m in self.mora_adjustments],
            "reasoning": self.reasoning,
        }


# ─── 感情→パラメータ補正マッピング（リサーチv2推奨値） ───
_EMOTION_ADJUSTMENTS: Dict[Emotion, Dict[str, float]] = {
    Emotion.NEUTRAL: {"speed_delta": 0.0, "pitch_delta": 0.0, "intonation_delta": 0.0, "volume_delta": 0.0},
    Emotion.JOY: {"speed_delta": 0.08, "pitch_delta": 0.03, "intonation_delta": 0.15, "volume_delta": 0.03},
    Emotion.ANGER: {"speed_delta": 0.10, "pitch_delta": 0.03, "intonation_delta": 0.20, "volume_delta": 0.08},
    Emotion.SADNESS: {"speed_delta": -0.10, "pitch_delta": -0.03, "intonation_delta": 0.0, "volume_delta": -0.05},
    Emotion.SURPRISE: {"speed_delta": 0.12, "pitch_delta": 0.05, "intonation_delta": 0.20, "volume_delta": 0.05},
    Emotion.WHISPER: {"speed_delta": -0.05, "pitch_delta": -0.04, "intonation_delta": -0.15, "volume_delta": -0.15},
}

# ─── テンション→追加補正（リサーチv2推奨値） ───
_TENSION_ADJUSTMENTS: Dict[Tension, Dict[str, float]] = {
    Tension.HIGH: {"speed_delta": 0.05, "intonation_delta": 0.08, "volume_delta": 0.03},
    Tension.MEDIUM: {"speed_delta": 0.0, "intonation_delta": 0.0, "volume_delta": 0.0},
    Tension.LOW: {"speed_delta": -0.05, "intonation_delta": -0.03, "volume_delta": -0.02},
}

# ─── 感情→VOICEVOXスタイル名マッピング ───
_EMOTION_TO_STYLE: Dict[Emotion, List[str]] = {
    Emotion.JOY: ["あまあま", "喜び", "楽しい", "デレ"],
    Emotion.ANGER: ["怒り", "ツンツン", "鬼"],
    Emotion.SADNESS: ["悲しみ", "しっとり", "セクシー"],
    Emotion.SURPRISE: ["びっくり", "喜び"],
    Emotion.WHISPER: ["ささやき", "ヒソヒソ"],
}


def create_plan(
    profile: SituationProfile,
    preset_manager: PresetManager,
    base_preset: Optional[str] = None,
) -> AdjustmentPlan:
    """
    SituationProfileからAdjustmentPlanを生成。

    Args:
        profile: 状況分析結果
        preset_manager: プリセットマネージャ
        base_preset: 指定されたベースプリセット名（Noneなら自動選定）

    Returns:
        AdjustmentPlan
    """
    # Step 1: ベースプリセット選択
    if base_preset:
        preset = preset_manager.get(base_preset)
    else:
        preset = _auto_select_preset(profile, preset_manager)

    # Step 2: 感情に基づく補正
    emotion_adj = _EMOTION_ADJUSTMENTS.get(profile.emotion, _EMOTION_ADJUSTMENTS[Emotion.NEUTRAL])
    merged = preset_manager.merge_with_adjustments(preset, emotion_adj)

    # Step 3: テンションに基づく追加補正
    tension_adj = _TENSION_ADJUSTMENTS.get(profile.tension, _TENSION_ADJUSTMENTS[Tension.MEDIUM])
    speed = merged.speed + tension_adj.get("speed_delta", 0.0)
    intonation = merged.intonation + tension_adj.get("intonation_delta", 0.0)
    volume_val = merged.volume + tension_adj.get("volume_delta", 0.0)

    # Step 4: パラメータ上下限クランプ
    speed = _clamp(speed, 0.5, 2.0)
    pitch = _clamp(merged.pitch, -0.15, 0.15)
    intonation = _clamp(intonation, 0.5, 2.0)
    volume_val = _clamp(volume_val, 0.5, 2.0)

    # Step 5: モーラ単位調整指示の生成
    mora_adjustments = _build_mora_adjustments(profile)

    # Step 6: 感情スタイル選択
    style_name = _select_style_name(profile.emotion)

    # Step 7: 策定根拠の記録
    reasoning_parts = [
        f"ベースプリセット: {preset.name}",
        f"感情補正: {profile.emotion.value}",
        f"テンション補正: {profile.tension.value}",
    ]
    if style_name:
        reasoning_parts.append(f"感情スタイル候補: {style_name}")
    if mora_adjustments:
        reasoning_parts.append(f"モーラ調整: {len(mora_adjustments)}件")

    return AdjustmentPlan(
        preset_name=preset.name,
        speed=round(speed, 2),
        pitch=round(pitch, 2),
        intonation=round(intonation, 2),
        volume=round(volume_val, 2),
        style_name=style_name,
        mora_adjustments=mora_adjustments,
        reasoning="、".join(reasoning_parts),
    )


def _auto_select_preset(profile: SituationProfile, pm: PresetManager) -> VoicePreset:
    """状況プロファイルからプリセットを自動選択"""
    # 強い感情がある場合
    if profile.emotion == Emotion.ANGER and profile.tension == Tension.HIGH:
        return pm.get("game")  # ハイテンション設定
    if profile.emotion == Emotion.SADNESS:
        return pm.get("story")  # ゆっくり・感情豊か
    if profile.tension == Tension.HIGH:
        return pm.get("youtube")  # テンポよく

    # 文体ベース
    if profile.style == SpeechStyle.STATEMENT and profile.tension == Tension.LOW:
        return pm.get("news")  # 落ち着き

    return pm.get("conversation")  # デフォルト


def _build_mora_adjustments(profile: SituationProfile) -> List[MoraAdjustment]:
    """モーラ単位の調整指示を生成"""
    adjustments = []

    # 疑問文: 末尾フレーズを上昇
    if profile.style == SpeechStyle.QUESTION:
        adjustments.append(MoraAdjustment(
            phrase_idx=-1,  # 最後のフレーズ（-1は適用時に解決）
            action="rise",
            amount=0.5,
        ))

    # 平叙文/独白: 文末下降
    if profile.style in (SpeechStyle.STATEMENT, SpeechStyle.MONOLOGUE):
        adjustments.append(MoraAdjustment(
            phrase_idx=-1,
            action="fall",
            amount=0.3,
        ))

    # 強調語があれば最初のフレーズにブースト
    if profile.emphasis_words:
        adjustments.append(MoraAdjustment(
            phrase_idx=0,  # 強調対象（適用時にフレーズ位置を検索）
            action="boost",
            amount=0.3 if profile.tension != Tension.HIGH else 0.5,
        ))

    return adjustments


def _select_style_name(emotion: Emotion) -> Optional[str]:
    """感情に対応するVOICEVOXスタイル名の候補を返す"""
    candidates = _EMOTION_TO_STYLE.get(emotion)
    if candidates:
        return candidates[0]  # 最優先候補を返す
    return None


def _clamp(value: float, min_val: float, max_val: float) -> float:
    """値を上下限にクランプ"""
    return max(min_val, min(max_val, value))
