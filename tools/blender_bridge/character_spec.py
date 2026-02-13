"""
character_spec.py - キャラクター生成仕様の正規化と修正ロジック

責務:
- prompt + フォーム入力から character_spec を正規化
- 部位ターゲット（例: 髪だけ、目だけ）を canonical 名へ展開
- 仕様の軽量バリデーション
- validator が返す repair_actions の適用
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Set, Tuple


ALLOWED_QUALITY = ("draft", "balanced", "high")
ALLOWED_CHARACTER_TYPES = ("humanoid",)
ALLOWED_SHAPES = ("cube", "sphere", "cylinder", "cone", "torus", "plane")

_NUMBER_UNIT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z\u3040-\u30ff\u4e00-\u9fff0-9/%]*)")

PART_ALIAS: Dict[str, List[str]] = {
    "hair": ["hair", "髪", "髪の毛", "前髪", "後ろ髪"],
    "head": ["head", "頭", "顔", "フェイス"],
    "neck": ["neck", "首"],
    "eye_l": ["eye_l", "左目"],
    "eye_r": ["eye_r", "右目"],
    "eyes": ["eyes", "目", "両目", "アイ"],
    "nose": ["nose", "鼻"],
    "mouth": ["mouth", "口"],
    "torso": ["torso", "body", "胴", "胴体", "体幹"],
    "pelvis": ["pelvis", "腰"],
    "arm_l": ["arm_l", "左腕"],
    "arm_r": ["arm_r", "右腕"],
    "arms": ["arms", "腕", "両腕"],
    "leg_l": ["leg_l", "左足", "左脚"],
    "leg_r": ["leg_r", "右足", "右脚"],
    "legs": ["legs", "足", "脚", "両足", "両脚"],
}

TARGET_EXPANSION: Dict[str, List[str]] = {
    "hair": ["hair"],
    "head": ["head", "neck", "nose", "mouth", "eye_l", "eye_r"],
    "neck": ["neck"],
    "eye_l": ["eye_l"],
    "eye_r": ["eye_r"],
    "eyes": ["eye_l", "eye_r"],
    "nose": ["nose"],
    "mouth": ["mouth"],
    "torso": ["torso", "pelvis"],
    "pelvis": ["pelvis"],
    "arm_l": ["arm_upper_l", "arm_lower_l", "hand_l"],
    "arm_r": ["arm_upper_r", "arm_lower_r", "hand_r"],
    "arms": ["arm_upper_l", "arm_lower_l", "hand_l", "arm_upper_r", "arm_lower_r", "hand_r"],
    "leg_l": ["leg_upper_l", "leg_lower_l", "foot_l"],
    "leg_r": ["leg_upper_r", "leg_lower_r", "foot_r"],
    "legs": ["leg_upper_l", "leg_lower_l", "foot_l", "leg_upper_r", "leg_lower_r", "foot_r"],
}

PART_ANCHORS: Dict[str, List[str]] = {
    "hair": ["head"],
    "eye_l": ["head"],
    "eye_r": ["head"],
    "nose": ["head"],
    "mouth": ["head"],
    "hand_l": ["arm_lower_l"],
    "hand_r": ["arm_lower_r"],
    "foot_l": ["leg_lower_l"],
    "foot_r": ["leg_lower_r"],
}

STYLE_KEYWORDS = {
    "anime": ("アニメ", "anime", "toon", "cartoon"),
    "realistic": ("リアル", "写実", "realistic", "photoreal"),
    "stylized": ("stylized", "スタイライズ", "トゥーン"),
    "scifi": ("sf", "sci-fi", "近未来", "サイバー"),
    "fantasy": ("ファンタジー", "fantasy"),
}


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_length_m(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        return None

    m = _NUMBER_UNIT_RE.search(text)
    if not m:
        return None

    num = float(m.group(1))
    unit = m.group(2)
    if unit in ("", "m", "meter", "meters", "メートル"):
        return num
    if unit in ("cm", "centimeter", "centimeters", "センチ"):
        return num / 100.0
    if unit in ("mm", "millimeter", "millimeters", "ミリ"):
        return num / 1000.0
    return num


def _pick(source: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _clamp(value: float, lo: float, hi: float, key: str, assumptions: List[str]) -> float:
    if value < lo:
        assumptions.append(f"{key} を最小値 {lo} に補正")
        return lo
    if value > hi:
        assumptions.append(f"{key} を最大値 {hi} に補正")
        return hi
    return value


def _normalize_part_name(token: str) -> Optional[str]:
    lower = token.strip().lower()
    if not lower:
        return None
    for canonical, aliases in PART_ALIAS.items():
        if lower == canonical:
            return canonical
        for alias in aliases:
            if alias.lower() == lower:
                return canonical
    return None


def _extract_style_tags(prompt: str) -> List[str]:
    out: List[str] = []
    lower = prompt.lower()
    for tag, keys in STYLE_KEYWORDS.items():
        if any((key in prompt) or (key in lower) for key in keys):
            out.append(tag)
    return out


def _extract_quality(prompt: str) -> Optional[str]:
    lower = prompt.lower()
    if any(k in prompt or k in lower for k in ("ラフ", "高速", "draft", "quick", "lowpoly")):
        return "draft"
    if any(k in prompt or k in lower for k in ("高品質", "high", "写実", "realistic", "photoreal")):
        return "high"
    if any(k in prompt or k in lower for k in ("標準", "balanced", "normal")):
        return "balanced"
    return None


def _extract_height(prompt: str) -> Optional[float]:
    m = re.search(r"(?:身長|height)\s*[:=]?\s*([0-9.]+\s*(?:mm|cm|m)?)", prompt.lower())
    if m:
        return parse_length_m(m.group(1))
    m2 = re.search(r"([0-9.]+)\s*(mm|cm|m)\s*(?:の)?\s*(?:人物|キャラ|character|humanoid)", prompt.lower())
    if m2:
        return parse_length_m(f"{m2.group(1)}{m2.group(2)}")
    return None


def _extract_target_parts(prompt: str) -> List[str]:
    markers = ("だけ", "のみ", "only", "part")
    if not any(marker in prompt.lower() or marker in prompt for marker in markers):
        return []

    found: List[str] = []
    lower = prompt.lower()
    for canonical, aliases in PART_ALIAS.items():
        if canonical in lower:
            found.append(canonical)
            continue
        for alias in aliases:
            if alias in prompt or alias.lower() in lower:
                found.append(canonical)
                break
    return found


def _parse_prompt(prompt: str) -> Dict[str, Any]:
    text = prompt or ""
    return {
        "quality_mode": _extract_quality(text),
        "height_m": _extract_height(text),
        "style_tags": _extract_style_tags(text),
        "target_parts": _extract_target_parts(text),
    }


def _normalize_reference_images(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    out: List[Dict[str, Any]] = []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        for token in parts:
            out.append({"path": token})
        return out
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                token = item.strip()
                if token:
                    out.append({"path": token})
                continue
            if isinstance(item, dict):
                path = str(item.get("path", "")).strip()
                if not path:
                    continue
                payload = {"path": path}
                view = str(item.get("view", "")).strip().lower()
                if view:
                    payload["view"] = view
                out.append(payload)
    return out


def _normalized_parts(parts: Any) -> List[Dict[str, Any]]:
    if not isinstance(parts, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(parts):
        if not isinstance(item, dict):
            continue
        shape = str(item.get("shape", "cube")).lower()
        if shape not in ALLOWED_SHAPES:
            shape = "cube"

        size = item.get("size") if isinstance(item.get("size"), list) else [1.0, 1.0, 1.0]
        location = item.get("location") if isinstance(item.get("location"), list) else [0.0, 0.0, 0.0]
        rotation_deg = item.get("rotation_deg") if isinstance(item.get("rotation_deg"), list) else [0.0, 0.0, 0.0]
        color = item.get("color") if isinstance(item.get("color"), list) else [0.8, 0.8, 0.8, 1.0]

        if len(size) != 3:
            size = [1.0, 1.0, 1.0]
        if len(location) != 3:
            location = [0.0, 0.0, 0.0]
        if len(rotation_deg) != 3:
            rotation_deg = [0.0, 0.0, 0.0]
        if len(color) != 4:
            color = [0.8, 0.8, 0.8, 1.0]

        normalized.append(
            {
                "name": str(item.get("name") or f"part_{idx:02d}"),
                "shape": shape,
                "size": [max(0.01, float(_to_float(v) or 0.01)) for v in size],
                "location": [float(_to_float(v) or 0.0) for v in location],
                "rotation_deg": [float(_to_float(v) or 0.0) for v in rotation_deg],
                "color": [max(0.0, min(1.0, float(_to_float(v) or 0.0))) for v in color],
            }
        )
    return normalized


def _default_parts(height_m: float, style_tags: List[str]) -> List[Dict[str, Any]]:
    scale = max(0.5, min(2.5, height_m / 1.72))
    stylized = "anime" in style_tags or "stylized" in style_tags
    head_scale = 1.15 if stylized else 1.0

    def _s(x: float) -> float:
        return round(x * scale, 4)

    return [
        {"name": "pelvis", "shape": "cube", "size": [_s(0.23), _s(0.18), _s(0.16)], "location": [0.0, 0.0, _s(0.88)], "color": [0.74, 0.58, 0.48, 1.0]},
        {"name": "torso", "shape": "cube", "size": [_s(0.28), _s(0.2), _s(0.42)], "location": [0.0, 0.0, _s(1.16)], "color": [0.8, 0.65, 0.55, 1.0]},
        {"name": "neck", "shape": "cylinder", "size": [_s(0.06), _s(0.06), _s(0.1)], "location": [0.0, 0.0, _s(1.43)], "color": [0.8, 0.65, 0.55, 1.0]},
        {"name": "head", "shape": "sphere", "size": [_s(0.12 * head_scale), _s(0.11 * head_scale), _s(0.13 * head_scale)], "location": [0.0, _s(-0.02), _s(1.62)], "color": [0.84, 0.7, 0.6, 1.0]},
        {"name": "eye_l", "shape": "sphere", "size": [_s(0.022), _s(0.018), _s(0.018)], "location": [_s(0.045), _s(-0.12), _s(1.64)], "color": [0.1, 0.12, 0.15, 1.0]},
        {"name": "eye_r", "shape": "sphere", "size": [_s(0.022), _s(0.018), _s(0.018)], "location": [_s(-0.045), _s(-0.12), _s(1.64)], "color": [0.1, 0.12, 0.15, 1.0]},
        {"name": "nose", "shape": "cone", "size": [_s(0.02), _s(0.02), _s(0.05)], "location": [0.0, _s(-0.14), _s(1.59)], "rotation_deg": [90.0, 0.0, 0.0], "color": [0.84, 0.7, 0.6, 1.0]},
        {"name": "mouth", "shape": "cube", "size": [_s(0.05), _s(0.01), _s(0.01)], "location": [0.0, _s(-0.135), _s(1.54)], "color": [0.6, 0.2, 0.25, 1.0]},
        {"name": "hair", "shape": "cone", "size": [_s(0.16 * head_scale), _s(0.16 * head_scale), _s(0.22 * head_scale)], "location": [0.0, _s(0.0), _s(1.82)], "rotation_deg": [0.0, 0.0, 180.0], "color": [0.09, 0.08, 0.07, 1.0]},
        {"name": "arm_upper_l", "shape": "cylinder", "size": [_s(0.055), _s(0.055), _s(0.26)], "location": [_s(0.21), 0.0, _s(1.19)], "rotation_deg": [0.0, 0.0, 8.0], "color": [0.8, 0.65, 0.55, 1.0]},
        {"name": "arm_upper_r", "shape": "cylinder", "size": [_s(0.055), _s(0.055), _s(0.26)], "location": [_s(-0.21), 0.0, _s(1.19)], "rotation_deg": [0.0, 0.0, -8.0], "color": [0.8, 0.65, 0.55, 1.0]},
        {"name": "arm_lower_l", "shape": "cylinder", "size": [_s(0.05), _s(0.05), _s(0.24)], "location": [_s(0.3), 0.0, _s(0.98)], "rotation_deg": [0.0, 0.0, 10.0], "color": [0.8, 0.65, 0.55, 1.0]},
        {"name": "arm_lower_r", "shape": "cylinder", "size": [_s(0.05), _s(0.05), _s(0.24)], "location": [_s(-0.3), 0.0, _s(0.98)], "rotation_deg": [0.0, 0.0, -10.0], "color": [0.8, 0.65, 0.55, 1.0]},
        {"name": "hand_l", "shape": "sphere", "size": [_s(0.05), _s(0.04), _s(0.03)], "location": [_s(0.36), 0.0, _s(0.83)], "color": [0.8, 0.65, 0.55, 1.0]},
        {"name": "hand_r", "shape": "sphere", "size": [_s(0.05), _s(0.04), _s(0.03)], "location": [_s(-0.36), 0.0, _s(0.83)], "color": [0.8, 0.65, 0.55, 1.0]},
        {"name": "leg_upper_l", "shape": "cylinder", "size": [_s(0.07), _s(0.07), _s(0.36)], "location": [_s(0.1), 0.0, _s(0.58)], "color": [0.2, 0.22, 0.24, 1.0]},
        {"name": "leg_upper_r", "shape": "cylinder", "size": [_s(0.07), _s(0.07), _s(0.36)], "location": [_s(-0.1), 0.0, _s(0.58)], "color": [0.2, 0.22, 0.24, 1.0]},
        {"name": "leg_lower_l", "shape": "cylinder", "size": [_s(0.06), _s(0.06), _s(0.36)], "location": [_s(0.1), 0.0, _s(0.2)], "color": [0.18, 0.2, 0.22, 1.0]},
        {"name": "leg_lower_r", "shape": "cylinder", "size": [_s(0.06), _s(0.06), _s(0.36)], "location": [_s(-0.1), 0.0, _s(0.2)], "color": [0.18, 0.2, 0.22, 1.0]},
        {"name": "foot_l", "shape": "cube", "size": [_s(0.09), _s(0.2), _s(0.04)], "location": [_s(0.1), _s(-0.06), _s(-0.01)], "color": [0.15, 0.15, 0.15, 1.0]},
        {"name": "foot_r", "shape": "cube", "size": [_s(0.09), _s(0.2), _s(0.04)], "location": [_s(-0.1), _s(-0.06), _s(-0.01)], "color": [0.15, 0.15, 0.15, 1.0]},
    ]


def _estimate_dimensions_from_parts(parts: List[Dict[str, Any]]) -> Dict[str, float]:
    if not parts:
        return {"width": 0.7, "depth": 0.45, "height": 1.72}

    min_x, min_y, min_z = 10**9, 10**9, 10**9
    max_x, max_y, max_z = -(10**9), -(10**9), -(10**9)
    for part in parts:
        size = part.get("size", [0.2, 0.2, 0.2]) if isinstance(part.get("size"), list) else [0.2, 0.2, 0.2]
        loc = part.get("location", [0.0, 0.0, 0.0]) if isinstance(part.get("location"), list) else [0.0, 0.0, 0.0]
        if len(size) != 3 or len(loc) != 3:
            continue
        hx = max(0.005, float(size[0]) * 0.5)
        hy = max(0.005, float(size[1]) * 0.5)
        hz = max(0.005, float(size[2]) * 0.5)
        cx, cy, cz = float(loc[0]), float(loc[1]), float(loc[2])
        min_x = min(min_x, cx - hx)
        max_x = max(max_x, cx + hx)
        min_y = min(min_y, cy - hy)
        max_y = max(max_y, cy + hy)
        min_z = min(min_z, cz - hz)
        max_z = max(max_z, cz + hz)

    width = max(0.1, max_x - min_x)
    depth = max(0.1, max_y - min_y)
    height = max(0.1, max_z - min_z)
    return {"width": width, "depth": depth, "height": height}


def _canonicalize_targets(values: List[str], assumptions: List[str]) -> List[str]:
    expanded: List[str] = []
    for raw in values:
        name = _normalize_part_name(str(raw))
        if name is None:
            assumptions.append(f"target_parts の未知値 '{raw}' は無視")
            continue
        expanded.extend(TARGET_EXPANSION.get(name, [name]))

    dedup: List[str] = []
    seen: Set[str] = set()
    for item in expanded:
        token = item.strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        dedup.append(token)
    return dedup


def _default_spec() -> Dict[str, Any]:
    return {
        "domain": "character",
        "style_preset": "HUMANOID_STANDARD_V1",
        "character_type": "humanoid",
        "intent": "Generate humanoid character",
        "quality_mode": "balanced",
        "dimensions_m": {"width": 0.7, "depth": 0.45, "height": 1.72},
        "reference_images": [],
        "target_parts": [],
        "composition": {"style_tags": [], "parts": [], "selected_assets": []},
        "target_constraints": {
            "poly_budget": 450000,
            "min_objects": 12,
            "symmetry_tolerance_m": 0.02,
            "require_rig": True,
        },
        "required_outputs": {"blend": True, "renders": 3, "validation_json": True},
    }


def normalize_character_spec(prompt: str, form_data: Optional[Dict[str, Any]], preset: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    form_data = form_data or {}
    parsed = _parse_prompt(prompt)
    assumptions: List[str] = []

    spec = _default_spec()
    if isinstance(preset, dict):
        merged = copy.deepcopy(spec)
        for key, value in preset.items():
            merged[key] = copy.deepcopy(value)
        spec = merged

    spec["domain"] = "character"
    spec["intent"] = str(_pick(form_data, ["intent", "title"]) or prompt or "Generate humanoid character")

    ctype = str(_pick(form_data, ["character_type", "type"]) or spec.get("character_type", "humanoid")).lower().strip()
    if ctype not in ALLOWED_CHARACTER_TYPES:
        assumptions.append(f"character_type '{ctype}' は未対応のため humanoid を使用")
        ctype = "humanoid"
    spec["character_type"] = ctype

    quality = str(
        _pick(form_data, ["quality_mode", "quality", "qualityMode"]) or parsed.get("quality_mode") or spec.get("quality_mode", "balanced")
    ).lower()
    if quality not in ALLOWED_QUALITY:
        assumptions.append("quality_mode が未知値のため balanced を使用")
        quality = "balanced"
    spec["quality_mode"] = quality

    dims = dict(spec.get("dimensions_m", {}))
    height = parse_length_m(_pick(form_data, ["height_m", "height", "身長"])) or parsed.get("height_m")
    if height is None:
        height = _to_float(dims.get("height")) or 1.72
        assumptions.append("height 未指定のため既定値を使用")
    height = _clamp(float(height), 0.1, 3.0, "dimensions_m.height", assumptions)
    dims["height"] = height

    width = parse_length_m(_pick(form_data, ["width_m", "width"])) or _to_float(dims.get("width")) or max(0.4, height * 0.38)
    depth = parse_length_m(_pick(form_data, ["depth_m", "depth"])) or _to_float(dims.get("depth")) or max(0.2, height * 0.24)
    dims["width"] = _clamp(float(width), 0.1, 2.0, "dimensions_m.width", assumptions)
    dims["depth"] = _clamp(float(depth), 0.1, 2.0, "dimensions_m.depth", assumptions)
    spec["dimensions_m"] = dims

    style_tags = list(spec.get("composition", {}).get("style_tags", [])) if isinstance(spec.get("composition"), dict) else []
    style_tags.extend(parsed.get("style_tags") or [])
    style_tags.extend(form_data.get("style_tags") if isinstance(form_data.get("style_tags"), list) else [])
    style_tags = [str(x).strip().lower() for x in style_tags if str(x).strip()]
    style_tags = list(dict.fromkeys(style_tags))

    parts = _normalized_parts(_pick(form_data, ["parts"]))
    if not parts:
        parts = _default_parts(height_m=height, style_tags=style_tags)
        assumptions.append("parts 未指定のため humanoid 既定パーツを使用")

    target_parts_raw = form_data.get("target_parts") if isinstance(form_data.get("target_parts"), list) else []
    if not target_parts_raw:
        target_parts_raw = parsed.get("target_parts") or []
    target_parts = _canonicalize_targets([str(x) for x in target_parts_raw], assumptions)

    if target_parts:
        keep_set: Set[str] = set(target_parts)
        for token in list(target_parts):
            for anchor in PART_ANCHORS.get(token, []):
                keep_set.add(anchor)
        filtered = [part for part in parts if str(part.get("name", "")).strip().lower() in keep_set]
        if filtered:
            parts = filtered
            assumptions.append("target_parts 指定のため部分生成モードを使用")
            estimated = _estimate_dimensions_from_parts(parts)
            dims["width"] = _clamp(float(estimated["width"] * 1.08), 0.1, 2.0, "dimensions_m.width", assumptions)
            dims["depth"] = _clamp(float(estimated["depth"] * 1.08), 0.1, 2.0, "dimensions_m.depth", assumptions)
            dims["height"] = _clamp(float(estimated["height"] * 1.08), 0.1, 3.0, "dimensions_m.height", assumptions)
            assumptions.append("部分生成モードのため dimensions_m を対象部位の推定外形へ再設定")
        else:
            assumptions.append("target_parts に一致するパーツが無かったため全身生成にフォールバック")
            target_parts = []

    composition = spec.get("composition") if isinstance(spec.get("composition"), dict) else {}
    composition["style_tags"] = style_tags
    composition["parts"] = parts
    selected_assets = form_data.get("selected_assets") if isinstance(form_data.get("selected_assets"), list) else []
    composition["selected_assets"] = selected_assets
    spec["composition"] = composition

    spec["target_parts"] = target_parts
    spec["reference_images"] = _normalize_reference_images(_pick(form_data, ["reference_images", "refs", "images"]))

    constraints = spec.get("target_constraints") if isinstance(spec.get("target_constraints"), dict) else {}
    quality_poly_budget = {"draft": 180000, "balanced": 450000, "high": 1200000}

    poly_budget = _to_float(_pick(form_data, ["poly_budget", "target_constraints.poly_budget"]))
    if poly_budget is None:
        poly_budget = float(constraints.get("poly_budget", quality_poly_budget[quality]))
    constraints["poly_budget"] = int(_clamp(float(poly_budget), 1000.0, 50000000.0, "target_constraints.poly_budget", assumptions))

    min_objects = _to_float(_pick(form_data, ["min_objects", "target_constraints.min_objects"]))
    if min_objects is None:
        min_objects = float(max(1, len(parts)))
    constraints["min_objects"] = int(_clamp(float(min_objects), 1.0, 10000.0, "target_constraints.min_objects", assumptions))

    sym_tol = _to_float(_pick(form_data, ["symmetry_tolerance_m", "target_constraints.symmetry_tolerance_m"]))
    if sym_tol is None:
        sym_tol = float(constraints.get("symmetry_tolerance_m", 0.02))
    constraints["symmetry_tolerance_m"] = float(
        _clamp(float(sym_tol), 0.001, 0.2, "target_constraints.symmetry_tolerance_m", assumptions)
    )

    require_rig = _pick(form_data, ["require_rig", "target_constraints.require_rig"])
    if require_rig is None:
        require_rig = bool(constraints.get("require_rig", quality != "draft"))
    constraints["require_rig"] = bool(require_rig)
    spec["target_constraints"] = constraints

    required_outputs = spec.get("required_outputs") if isinstance(spec.get("required_outputs"), dict) else {}
    required_outputs.setdefault("blend", True)
    required_outputs.setdefault("renders", 3)
    required_outputs.setdefault("validation_json", True)
    renders = _to_float(required_outputs.get("renders"))
    required_outputs["renders"] = int(_clamp(float(renders or 3), 1.0, 10.0, "required_outputs.renders", assumptions))
    spec["required_outputs"] = required_outputs

    spec["assumptions"] = assumptions
    spec["source_prompt"] = prompt
    return spec


def validate_character_spec(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    required = [
        "domain",
        "style_preset",
        "character_type",
        "intent",
        "quality_mode",
        "dimensions_m",
        "composition",
        "target_constraints",
        "required_outputs",
    ]
    for key in required:
        if key not in spec:
            errors.append(f"required key missing: {key}")

    if str(spec.get("domain", "")) != "character":
        errors.append("domain must be 'character'")
    if str(spec.get("character_type", "")) not in ALLOWED_CHARACTER_TYPES:
        errors.append("character_type is invalid")
    if str(spec.get("quality_mode", "")) not in ALLOWED_QUALITY:
        errors.append("quality_mode is invalid")

    dims = spec.get("dimensions_m")
    if not isinstance(dims, dict):
        errors.append("dimensions_m must be object")
    else:
        for key, lo, hi in (("width", 0.1, 2.0), ("depth", 0.1, 2.0), ("height", 0.1, 3.0)):
            value = _to_float(dims.get(key))
            if value is None:
                errors.append(f"dimensions_m.{key} is required")
            elif value < lo or value > hi:
                errors.append(f"dimensions_m.{key} out of range [{lo}, {hi}]")

    composition = spec.get("composition")
    if not isinstance(composition, dict):
        errors.append("composition must be object")
    else:
        parts = composition.get("parts")
        if not isinstance(parts, list) or not parts:
            errors.append("composition.parts must be non-empty array")
        else:
            for idx, part in enumerate(parts):
                if not isinstance(part, dict):
                    errors.append(f"composition.parts[{idx}] must be object")
                    continue
                shape = str(part.get("shape", "")).lower()
                if shape not in ALLOWED_SHAPES:
                    errors.append(f"composition.parts[{idx}].shape is invalid")

    constraints = spec.get("target_constraints")
    if not isinstance(constraints, dict):
        errors.append("target_constraints must be object")
    else:
        poly = _to_float(constraints.get("poly_budget"))
        if poly is None or poly < 1000 or poly > 50000000:
            errors.append("target_constraints.poly_budget out of range")
        min_objects = _to_float(constraints.get("min_objects"))
        if min_objects is None or min_objects < 1 or min_objects > 10000:
            errors.append("target_constraints.min_objects out of range")
        symmetry_tol = _to_float(constraints.get("symmetry_tolerance_m"))
        if symmetry_tol is None or symmetry_tol < 0.001 or symmetry_tol > 0.2:
            errors.append("target_constraints.symmetry_tolerance_m out of range")
        if not isinstance(constraints.get("require_rig"), bool):
            errors.append("target_constraints.require_rig must be boolean")

    required_outputs = spec.get("required_outputs")
    if not isinstance(required_outputs, dict):
        errors.append("required_outputs must be object")
    else:
        renders = _to_float(required_outputs.get("renders"))
        if renders is None or renders < 1 or renders > 10:
            errors.append("required_outputs.renders out of range")

    if "target_parts" in spec and not isinstance(spec.get("target_parts"), list):
        errors.append("target_parts must be array")
    if "reference_images" in spec and not isinstance(spec.get("reference_images"), list):
        errors.append("reference_images must be array")
    return errors


def _get_by_path(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _set_by_path(data: Dict[str, Any], path: str, value: Any) -> None:
    current: Any = data
    segments = path.split(".")
    for segment in segments[:-1]:
        if segment not in current or not isinstance(current[segment], dict):
            current[segment] = {}
        current = current[segment]
    current[segments[-1]] = value


def apply_repair_actions(spec: Dict[str, Any], actions: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    next_spec = copy.deepcopy(spec)
    applied: List[str] = []

    for item in actions or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        key = str(item.get("key", "")).strip()

        if action == "set_value" and key and "value" in item:
            _set_by_path(next_spec, key, item["value"])
            applied.append(f"{key} を {item['value']} に設定")
            continue

        if action == "clamp_range" and key:
            current = _to_float(_get_by_path(next_spec, key))
            lo = _to_float(item.get("min"))
            hi = _to_float(item.get("max"))
            if current is None:
                continue
            new_value = current
            if lo is not None and new_value < lo:
                new_value = lo
            if hi is not None and new_value > hi:
                new_value = hi
            if new_value != current:
                _set_by_path(next_spec, key, new_value)
                applied.append(f"{key} をレンジ補正 ({current} -> {new_value})")
            continue

        if action == "scale_value" and key:
            current = _to_float(_get_by_path(next_spec, key))
            factor = _to_float(item.get("factor"))
            if current is None or factor is None:
                continue
            new_value = current * factor
            _set_by_path(next_spec, key, new_value)
            applied.append(f"{key} をスケール補正 ({current} -> {new_value})")
            continue

        if action == "remove_part":
            target = str(item.get("value", "")).strip().lower()
            parts = _get_by_path(next_spec, "composition.parts")
            if not target or not isinstance(parts, list):
                continue
            before = len(parts)
            filtered = [p for p in parts if str(p.get("name", "")).strip().lower() != target]
            if len(filtered) != before:
                _set_by_path(next_spec, "composition.parts", filtered)
                applied.append(f"part '{target}' を除外")
            continue

        if action == "add_part":
            value = item.get("value")
            if not isinstance(value, dict):
                continue
            normalized = _normalized_parts([value])
            if not normalized:
                continue
            part = normalized[0]
            parts = _get_by_path(next_spec, "composition.parts")
            if not isinstance(parts, list):
                parts = []
            names = {str(p.get("name", "")).strip().lower() for p in parts if isinstance(p, dict)}
            if str(part.get("name", "")).strip().lower() in names:
                continue
            parts.append(part)
            _set_by_path(next_spec, "composition.parts", parts)
            applied.append(f"part '{part.get('name')}' を追加")
            continue

        if action == "remove_selected_asset":
            target_id = str(item.get("value", "")).strip()
            assets = _get_by_path(next_spec, "composition.selected_assets")
            if not target_id or not isinstance(assets, list):
                continue
            before = len(assets)
            filtered = [a for a in assets if str(a.get("id", "")) != target_id]
            if len(filtered) != before:
                _set_by_path(next_spec, "composition.selected_assets", filtered)
                applied.append(f"selected asset '{target_id}' を除外")
            continue

    return next_spec, applied
