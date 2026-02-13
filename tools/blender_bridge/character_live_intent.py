"""
character_live_intent.py - キャラ向け自然文をライブ編集コマンドへ変換
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from house_live_intent import interpret_instruction as base_interpret_instruction


TARGET_MAP: Dict[str, List[str]] = {
    "髪": ["hair"],
    "髪の毛": ["hair"],
    "前髪": ["hair"],
    "頭": ["head"],
    "顔": ["head"],
    "目": ["eye_l", "eye_r"],
    "左目": ["eye_l"],
    "右目": ["eye_r"],
    "鼻": ["nose"],
    "口": ["mouth"],
    "胴体": ["torso"],
    "体幹": ["torso"],
    "腰": ["pelvis"],
    "腕": ["arm_upper_l", "arm_lower_l", "hand_l", "arm_upper_r", "arm_lower_r", "hand_r"],
    "左腕": ["arm_upper_l", "arm_lower_l", "hand_l"],
    "右腕": ["arm_upper_r", "arm_lower_r", "hand_r"],
    "足": ["leg_upper_l", "leg_lower_l", "foot_l", "leg_upper_r", "leg_lower_r", "foot_r"],
    "脚": ["leg_upper_l", "leg_lower_l", "foot_l", "leg_upper_r", "leg_lower_r", "foot_r"],
    "左足": ["leg_upper_l", "leg_lower_l", "foot_l"],
    "右足": ["leg_upper_r", "leg_lower_r", "foot_r"],
}

COLOR_MAP = {
    "赤": [0.85, 0.2, 0.2, 1.0],
    "青": [0.2, 0.35, 0.85, 1.0],
    "緑": [0.2, 0.65, 0.3, 1.0],
    "白": [0.9, 0.9, 0.9, 1.0],
    "黒": [0.1, 0.1, 0.1, 1.0],
    "黄": [0.9, 0.8, 0.2, 1.0],
    "グレー": [0.5, 0.5, 0.5, 1.0],
    "茶": [0.45, 0.3, 0.2, 1.0],
}


def _build_ops(targets: List[str], factor: List[float], message: str) -> Dict[str, Any]:
    return {
        "intent": "scale_multiply",
        "ops": [{"op": "scale_multiply", "target": target, "factor": factor} for target in targets],
        "safety_level": "caution",
        "requires_confirmation": False,
        "message": message,
    }


def _parse_character_scale(raw: str) -> Dict[str, Any]:
    m = re.search(
        r"(髪の毛|前髪|髪|顔|頭|目|左目|右目|鼻|口|胴体|体幹|腰|腕|左腕|右腕|足|脚|左足|右足)\s*を\s*(少し)?(大きく|小さく|長く|短く)",
        raw,
    )
    if not m:
        return {}
    token = m.group(1)
    mode = m.group(3)
    targets = TARGET_MAP.get(token, [])
    if not targets:
        return {}

    if mode == "大きく":
        factor = [1.08, 1.08, 1.08]
    elif mode == "小さく":
        factor = [0.92, 0.92, 0.92]
    elif mode == "長く":
        factor = [1.0, 1.0, 1.1]
    else:
        factor = [1.0, 1.0, 0.9]
    return _build_ops(targets, factor, f"{token} を {mode} 調整します。")


def _parse_character_color(raw: str) -> Dict[str, Any]:
    m = re.search(
        r"(髪の毛|前髪|髪|顔|頭|目|左目|右目|鼻|口|胴体|体幹|腰|腕|左腕|右腕|足|脚|左足|右足)\s*を\s*(赤|青|緑|白|黒|黄|グレー|茶)(?:色)?(?:に)?",
        raw,
    )
    if not m:
        return {}
    token = m.group(1)
    color_name = m.group(2)
    targets = TARGET_MAP.get(token, [])
    rgba = COLOR_MAP.get(color_name)
    if not targets or rgba is None:
        return {}

    return {
        "intent": "set_color",
        "ops": [{"op": "set_color", "target": target, "color_rgba": rgba} for target in targets],
        "safety_level": "caution",
        "requires_confirmation": False,
        "message": f"{token} の色を {color_name} に変更します。",
    }


def interpret_instruction(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return base_interpret_instruction(text)

    parsed = _parse_character_scale(raw)
    if parsed:
        return parsed

    parsed = _parse_character_color(raw)
    if parsed:
        return parsed

    return base_interpret_instruction(text)
