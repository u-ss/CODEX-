"""
house_live_intent.py - 自然文を安全なライブ編集コマンドへ変換
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional


COLOR_MAP = {
    "赤": [0.85, 0.2, 0.2, 1.0],
    "青": [0.2, 0.35, 0.85, 1.0],
    "緑": [0.2, 0.65, 0.3, 1.0],
    "白": [0.9, 0.9, 0.9, 1.0],
    "黒": [0.1, 0.1, 0.1, 1.0],
    "黄": [0.9, 0.8, 0.2, 1.0],
    "グレー": [0.5, 0.5, 0.5, 1.0],
    "灰": [0.5, 0.5, 0.5, 1.0],
    "茶": [0.45, 0.3, 0.2, 1.0],
    "オレンジ": [0.9, 0.55, 0.2, 1.0],
    "ピンク": [0.9, 0.55, 0.7, 1.0],
    "紫": [0.55, 0.35, 0.8, 1.0],
}

QUIT_WORDS = {"quit", "exit", "終了", "おわり", "終わり", "確定して終了"}
HELP_WORDS = {"help", "?", "使い方", "ヘルプ"}


def _distance_to_m(value: str, unit: Optional[str]) -> float:
    v = float(value)
    u = (unit or "m").lower()
    if u == "mm":
        return v / 1000.0
    if u == "cm":
        return v / 100.0
    return v


def _target_from_prefix(text: str, suffix_pattern: str) -> Optional[str]:
    m = re.search(suffix_pattern, text)
    if not m:
        return None
    target = m.group(1).strip()
    return target if target else None


def _build_unknown(message: str) -> Dict[str, Any]:
    return {
        "intent": "unknown",
        "ops": [],
        "safety_level": "safe",
        "requires_confirmation": False,
        "message": message,
    }


def interpret_instruction(text: str) -> Dict[str, Any]:
    """
    自然文指示を安全編集コマンドへ変換する。
    """
    raw = text.strip()
    if not raw:
        return _build_unknown("空の指示です。")

    lower = raw.lower()
    if lower in HELP_WORDS:
        return {
            "intent": "help",
            "ops": [{"op": "help"}],
            "safety_level": "safe",
            "requires_confirmation": False,
            "message": "使い方を表示します。",
        }

    if lower in QUIT_WORDS:
        return {
            "intent": "quit",
            "ops": [{"op": "quit"}],
            "safety_level": "safe",
            "requires_confirmation": False,
            "message": "ライブ編集を終了します。",
        }

    if any(k in raw for k in ("一覧", "list", "オブジェクト")):
        return {
            "intent": "list_objects",
            "ops": [{"op": "list_objects"}],
            "safety_level": "safe",
            "requires_confirmation": False,
            "message": "オブジェクト一覧を取得します。",
        }

    if any(k in raw for k in ("取り消し", "undo", "戻す")):
        return {
            "intent": "undo",
            "ops": [{"op": "undo"}],
            "safety_level": "caution",
            "requires_confirmation": False,
            "message": "1手戻します。",
        }

    if any(k in raw for k in ("やり直し", "redo")):
        return {
            "intent": "redo",
            "ops": [{"op": "redo"}],
            "safety_level": "caution",
            "requires_confirmation": False,
            "message": "1手やり直します。",
        }

    if any(k in raw for k in ("保存", "checkpoint", "チェックポイント")):
        return {
            "intent": "save_checkpoint",
            "ops": [{"op": "save_checkpoint"}],
            "safety_level": "safe",
            "requires_confirmation": False,
            "message": "チェックポイントを保存します。",
        }

    # 削除（危険）
    target = _target_from_prefix(raw, r"(.+?)\s*を\s*(?:削除|消して|消す)")
    if target:
        return {
            "intent": "delete_object",
            "ops": [{"op": "delete_object", "target": target}],
            "safety_level": "danger",
            "requires_confirmation": True,
            "message": f"{target} を削除します。",
        }

    # 複製
    target = _target_from_prefix(raw, r"(.+?)\s*を\s*(?:複製|コピー)")
    if target:
        return {
            "intent": "duplicate_object",
            "ops": [{"op": "duplicate_object", "target": target, "offset": [0.5, 0.0, 0.0]}],
            "safety_level": "caution",
            "requires_confirmation": False,
            "message": f"{target} を複製します。",
        }

    # 色変更
    m = re.search(r"(.+?)\s*を\s*(赤|青|緑|白|黒|黄|グレー|灰|茶|オレンジ|ピンク|紫)(?:色)?(?:に)?", raw)
    if m:
        target = m.group(1).strip()
        cname = m.group(2)
        rgba = COLOR_MAP[cname]
        return {
            "intent": "set_color",
            "ops": [{"op": "set_color", "target": target, "color_rgba": rgba}],
            "safety_level": "caution",
            "requires_confirmation": False,
            "message": f"{target} の色を {cname} に変更します。",
        }

    # 高さ/サイズのニュアンス
    m = re.search(r"(.+?)\s*を\s*少し(低く|高く|大きく|小さく)", raw)
    if m:
        target = m.group(1).strip()
        mode = m.group(2)
        if mode == "低く":
            factor = [1.0, 1.0, 0.95]
        elif mode == "高く":
            factor = [1.0, 1.0, 1.05]
        elif mode == "大きく":
            factor = [1.08, 1.08, 1.08]
        else:
            factor = [0.92, 0.92, 0.92]
        return {
            "intent": "scale_multiply",
            "ops": [{"op": "scale_multiply", "target": target, "factor": factor}],
            "safety_level": "caution",
            "requires_confirmation": False,
            "message": f"{target} のスケールを調整します。",
        }

    # 軸指定移動 (x/y/z)
    m = re.search(r"(.+?)\s*を?\s*([xyzXYZ])(?:方向)?(?:に)?\s*([-+]?\d+(?:\.\d+)?)\s*(mm|cm|m)?", raw)
    if m:
        target = m.group(1).strip()
        axis = m.group(2).lower()
        dist = _distance_to_m(m.group(3), m.group(4))
        delta = [0.0, 0.0, 0.0]
        delta["xyz".index(axis)] = dist
        return {
            "intent": "move_relative",
            "ops": [{"op": "move_relative", "target": target, "delta": delta}],
            "safety_level": "safe",
            "requires_confirmation": False,
            "message": f"{target} を {axis} 方向へ移動します。",
        }

    # 方向語移動
    m = re.search(r"(.+?)\s*を?\s*(上|下|右|左|前|後)(?:に)?\s*([-+]?\d+(?:\.\d+)?)\s*(mm|cm|m)?", raw)
    if m:
        target = m.group(1).strip()
        direction = m.group(2)
        dist = _distance_to_m(m.group(3), m.group(4))
        delta = [0.0, 0.0, 0.0]
        if direction == "上":
            delta = [0.0, 0.0, dist]
        elif direction == "下":
            delta = [0.0, 0.0, -dist]
        elif direction == "右":
            delta = [dist, 0.0, 0.0]
        elif direction == "左":
            delta = [-dist, 0.0, 0.0]
        elif direction == "前":
            delta = [0.0, -dist, 0.0]
        elif direction == "後":
            delta = [0.0, dist, 0.0]
        return {
            "intent": "move_relative",
            "ops": [{"op": "move_relative", "target": target, "delta": delta}],
            "safety_level": "safe",
            "requires_confirmation": False,
            "message": f"{target} を移動します。",
        }

    # 回転（角度指定、軸オプション）
    # パターン: 「〇〇を30度回転」「〇〇をZ軸に45度回す」
    m = re.search(r"(.+?)\s*を?\s*(?:([xyzXYZ])軸(?:に)?\s*)?(-?\d+(?:\.\d+)?)\s*度\s*(?:回転|回す|回して)", raw)
    if m:
        target = m.group(1).strip()
        axis = (m.group(2) or "z").lower()
        degrees = float(m.group(3))
        radians = math.radians(degrees)
        rotation_delta = [0.0, 0.0, 0.0]
        rotation_delta["xyz".index(axis)] = radians
        return {
            "intent": "rotate_relative",
            "ops": [{"op": "rotate_relative", "target": target, "axis": axis, "degrees": degrees, "delta_radians": rotation_delta}],
            "safety_level": "caution",
            "requires_confirmation": False,
            "message": f"{target} を {axis.upper()}軸に {degrees}度 回転します。",
        }

    return _build_unknown(
        "解釈できませんでした。例: `Roofを上に20cm`, `Doorを赤に`, `Windowを複製`, `Crystal_Mainを30度回転`, `一覧`, `戻す`"
    )

