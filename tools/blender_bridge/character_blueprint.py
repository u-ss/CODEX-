"""
character_blueprint.py - キャラクター設計図（blueprint）生成

責務:
- character_spec から build/validate 共通で使う設計図を生成
- 参照画像の存在・ビュー方向・サイズをローカル解析
- 生成順・左右対称ペア・プロポーションガイドを確定
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _infer_view(path: Path) -> str:
    name = path.name.lower()
    if any(key in name for key in ("front", "frontal", "正面", "前")):
        return "front"
    if any(key in name for key in ("side", "profile", "横", "側面")):
        return "side"
    if any(key in name for key in ("back", "rear", "後", "背面")):
        return "back"
    if any(key in name for key in ("oblique", "angle", "斜め")):
        return "oblique"
    return "detail"


def _probe_png_size(path: Path) -> Optional[Tuple[int, int]]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = struct.unpack(">I", data[16:20])[0]
    height = struct.unpack(">I", data[20:24])[0]
    return int(width), int(height)


def _probe_jpeg_size(path: Path) -> Optional[Tuple[int, int]]:
    data = path.read_bytes()
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None

    idx = 2
    while idx + 9 < len(data):
        if data[idx] != 0xFF:
            idx += 1
            continue
        marker = data[idx + 1]
        idx += 2
        if marker in (0xD8, 0xD9):  # SOI / EOI
            continue
        if idx + 2 > len(data):
            break
        length = struct.unpack(">H", data[idx : idx + 2])[0]
        if length < 2 or idx + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if idx + 7 >= len(data):
                break
            height = struct.unpack(">H", data[idx + 3 : idx + 5])[0]
            width = struct.unpack(">H", data[idx + 5 : idx + 7])[0]
            return int(width), int(height)
        idx += length
    return None


def _probe_image_size(path: Path) -> Optional[Tuple[int, int]]:
    ext = path.suffix.lower()
    try:
        if ext == ".png":
            return _probe_png_size(path)
        if ext in (".jpg", ".jpeg"):
            return _probe_jpeg_size(path)
    except Exception:
        return None
    return None


def _normalize_reference_images(value: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                token = item.strip()
                if token:
                    items.append({"path": token})
            elif isinstance(item, dict):
                path = str(item.get("path", "")).strip()
                if path:
                    payload = {"path": path}
                    if item.get("view"):
                        payload["view"] = str(item["view"]).strip().lower()
                    items.append(payload)
    elif isinstance(value, str):
        for token in [x.strip() for x in value.split(",") if x.strip()]:
            items.append({"path": token})

    normalized: List[Dict[str, Any]] = []
    for item in items:
        path = Path(str(item.get("path", "")).strip()).expanduser()
        exists = path.exists()
        view = str(item.get("view", "")).strip().lower() or _infer_view(path)
        entry: Dict[str, Any] = {"path": str(path), "view": view, "exists": bool(exists)}
        if exists and path.is_file():
            size = _probe_image_size(path)
            if size:
                entry["width_px"] = int(size[0])
                entry["height_px"] = int(size[1])
        normalized.append(entry)
    return normalized


def _build_symmetry_pairs(parts: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    names = {str(part.get("name", "")).strip() for part in parts}
    pairs: List[Dict[str, str]] = []
    for name in sorted(names):
        if not name.endswith("_l"):
            continue
        right = f"{name[:-2]}_r"
        if right in names:
            pairs.append({"left": name, "right": right})
    return pairs


def _part_order_key(name: str) -> int:
    order = [
        "pelvis",
        "torso",
        "neck",
        "head",
        "eye_l",
        "eye_r",
        "nose",
        "mouth",
        "hair",
        "arm_upper_l",
        "arm_upper_r",
        "arm_lower_l",
        "arm_lower_r",
        "hand_l",
        "hand_r",
        "leg_upper_l",
        "leg_upper_r",
        "leg_lower_l",
        "leg_lower_r",
        "foot_l",
        "foot_r",
    ]
    try:
        return order.index(name)
    except ValueError:
        return len(order) + 100


def build_character_blueprint(spec: Dict[str, Any]) -> Dict[str, Any]:
    composition = spec.get("composition", {}) if isinstance(spec.get("composition"), dict) else {}
    parts = composition.get("parts", []) if isinstance(composition.get("parts"), list) else []
    parts = [p for p in parts if isinstance(p, dict)]

    # 生成順を固定化して反復のブレを抑える
    parts = sorted(parts, key=lambda p: _part_order_key(str(p.get("name", ""))))

    target_parts = spec.get("target_parts", []) if isinstance(spec.get("target_parts"), list) else []
    active_parts = [str(p.get("name", "")).strip() for p in parts if str(p.get("name", "")).strip()]
    generation_mode = "partial" if target_parts else "full"

    part_map: Dict[str, Dict[str, Any]] = {}
    for part in parts:
        name = str(part.get("name", "")).strip()
        if name:
            part_map[name] = json.loads(json.dumps(part))

    symmetry_pairs = _build_symmetry_pairs(parts)
    refs = _normalize_reference_images(spec.get("reference_images", []))
    views = {entry.get("view"): True for entry in refs if entry.get("exists")}
    assumptions: List[str] = []

    for required in ("front", "side", "back"):
        if not views.get(required):
            assumptions.append(f"reference image '{required}' が不足")

    dims = spec.get("dimensions_m") if isinstance(spec.get("dimensions_m"), dict) else {}
    height = float(dims.get("height", 1.72) or 1.72)
    width = float(dims.get("width", 0.7) or 0.7)
    depth = float(dims.get("depth", 0.45) or 0.45)

    guides = {
        "height_m": height,
        "shoulder_width_m": round(max(0.2, width * 0.62), 4),
        "hip_width_m": round(max(0.16, width * 0.45), 4),
        "eye_level_z_m": round(height * 0.93, 4),
        "chin_level_z_m": round(height * 0.86, 4),
        "knee_level_z_m": round(height * 0.29, 4),
    }

    view_hints = {
        "front": {"camera_location": [0.0, -max(2.4, depth * 4.5), max(1.1, height * 0.95)]},
        "oblique": {"camera_location": [max(1.8, width * 3.0), -max(2.0, depth * 3.5), max(1.1, height * 0.95)]},
        "bird": {"camera_location": [0.0, -0.001, max(3.0, height * 2.8)]},
    }

    return {
        "character_type": spec.get("character_type", "humanoid"),
        "style_preset": spec.get("style_preset", "HUMANOID_STANDARD_V1"),
        "quality_mode": spec.get("quality_mode", "balanced"),
        "generation_mode": generation_mode,
        "active_parts": active_parts,
        "part_plan": parts,
        "part_map": part_map,
        "symmetry_pairs": symmetry_pairs,
        "reference_images": refs,
        "reference_coverage": {"front": bool(views.get("front")), "side": bool(views.get("side")), "back": bool(views.get("back"))},
        "proportion_guides": guides,
        "view_hints": view_hints,
        "assumptions": assumptions,
    }
