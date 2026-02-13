# -*- coding: utf-8 -*-
"""
プリセット管理 — シチュエーション別パラメータプリセット
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class VoicePreset:
    """音声パラメータプリセット"""
    name: str
    speed: float = 1.0
    pitch: float = 0.0
    intonation: float = 1.0
    volume: float = 1.0
    description: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


# 組込みプリセット（リサーチ成果ベース v2 — 2層チューニング対応）
BUILTIN_PRESETS: Dict[str, VoicePreset] = {
    "news": VoicePreset(
        name="news", speed=1.0, pitch=0.0, intonation=0.95, volume=1.0,
        description="ニュースナレーション — 落ち着き・信頼感",
    ),
    "youtube": VoicePreset(
        name="youtube", speed=1.15, pitch=0.0, intonation=1.25, volume=1.0,
        description="YouTube解説 — テンポよく・抑揚大きめ",
    ),
    "story": VoicePreset(
        name="story", speed=0.90, pitch=0.0, intonation=1.3, volume=0.95,
        description="物語朗読 — ゆっくり・感情豊か",
    ),
    "game": VoicePreset(
        name="game", speed=1.25, pitch=0.03, intonation=1.4, volume=1.05,
        description="ゲーム実況 — ハイテンション",
    ),
    "education": VoicePreset(
        name="education", speed=0.88, pitch=0.0, intonation=1.05, volume=1.0,
        description="教育教材 — 聞き取りやすさ重視",
    ),
    "conversation": VoicePreset(
        name="conversation", speed=1.0, pitch=0.0, intonation=1.15, volume=1.0,
        description="日常会話 — 自然で中庸",
    ),
    "chant": VoicePreset(
        name="chant", speed=0.80, pitch=-0.03, intonation=1.15, volume=1.05,
        description="詠唱・儀式 — 威厳ある低速",
    ),
}


class PresetManager:
    """プリセット管理"""

    def __init__(self, custom_path: Optional[Path] = None):
        self._presets: Dict[str, VoicePreset] = dict(BUILTIN_PRESETS)
        if custom_path and custom_path.exists():
            self._load_custom(custom_path)

    def get(self, name: str) -> VoicePreset:
        """プリセットを取得（見つからなければconversationを返す）"""
        return self._presets.get(name, self._presets["conversation"])

    def list_names(self) -> list:
        """利用可能なプリセット名一覧"""
        return list(self._presets.keys())

    def add(self, preset: VoicePreset) -> None:
        """プリセットを追加"""
        self._presets[preset.name] = preset

    def merge_with_adjustments(
        self, preset: VoicePreset, adjustments: Dict
    ) -> VoicePreset:
        """プリセットに微調整を適用した新プリセットを返す"""
        return VoicePreset(
            name=f"{preset.name}_adjusted",
            speed=preset.speed + adjustments.get("speed_delta", 0.0),
            pitch=preset.pitch + adjustments.get("pitch_delta", 0.0),
            intonation=preset.intonation + adjustments.get("intonation_delta", 0.0),
            volume=preset.volume + adjustments.get("volume_delta", 0.0),
            description=f"{preset.description}（調整済み）",
        )

    def save_custom(self, path: Path) -> None:
        """カスタムプリセットをJSONに保存"""
        # 組込み以外のプリセットを保存
        custom = {
            k: v.to_dict()
            for k, v in self._presets.items()
            if k not in BUILTIN_PRESETS
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(custom, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_custom(self, path: Path) -> None:
        """カスタムプリセットをJSONから読み込み"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for name, values in data.items():
                self._presets[name] = VoicePreset(**values)
        except Exception:
            pass  # カスタムファイルが壊れていても無視
