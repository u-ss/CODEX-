"""
universal_spec.py - Blender汎用生成向け仕様正規化

責務:
- prompt + form入力を汎用 asset_spec に正規化
- ドメイン推定 (house/product/furniture/vehicle/scene/prop/character)
- 寸法・品質・制約のデフォルト補完
- validator が返す repair_actions の適用
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple


ALLOWED_DOMAINS = ("house", "product", "furniture", "vehicle", "scene", "prop", "character")
ALLOWED_QUALITY = ("draft", "balanced", "high")
ALLOWED_SHAPES = ("cube", "sphere", "cylinder", "cone", "torus", "plane")

_DOMAIN_KEYWORDS = {
    "house": ("戸建", "住宅", "家", "建物", "house", "home", "building"),
    "product": ("製品", "商品", "プロダクト", "product", "device", "gadget"),
    "furniture": ("家具", "机", "椅子", "棚", "table", "chair", "desk", "sofa"),
    "vehicle": ("車", "自動車", "バイク", "vehicle", "car", "bike", "truck"),
    "scene": ("シーン", "部屋", "室内", "環境", "scene", "room", "environment"),
    "prop": ("小物", "プロップ", "prop", "asset", "object"),
    "character": ("キャラ", "キャラクター", "人物", "character", "humanoid", "avatar", "アバター"),
}

_STYLE_KEYWORDS = {
    "modern": ("モダン", "modern", "minimal"),
    "industrial": ("インダストリアル", "industrial"),
    "wood": ("木", "木目", "wood", "timber"),
    "metal": ("金属", "metal"),
    "stylized": ("トゥーン", "stylized", "cartoon"),
    "realistic": ("リアル", "フォトリアル", "realistic", "photoreal"),
    "crystal": ("crystal", "水晶", "鉱石"),
    "cave": ("cave", "洞窟"),
    "cool": ("cool", "寒色", "青系"),
    "cinematic": ("cinematic", "映画風", "シネマ"),
}

_SHAPE_KEYWORDS = {
    "cube": ("箱", "立方体", "cube", "box"),
    "sphere": ("球", "sphere", "ball"),
    "cylinder": ("円柱", "cylinder", "pipe"),
    "cone": ("円錐", "cone"),
    "torus": ("トーラス", "torus", "ring"),
    "plane": ("平面", "plane", "ground"),
}

_NUMBER_UNIT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z\u3040-\u30ff\u4e00-\u9fff0-9/%]*)")
_DIMENSION_TRIPLE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mm|cm|m)?\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?",
    re.IGNORECASE,
)
_DIMENSION_DOUBLE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mm|cm|m)?\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)?",
    re.IGNORECASE,
)


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


def _unique_keep_order(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value.strip())
    return out


def _clamp(value: float, lo: float, hi: float, key: str, assumptions: List[str]) -> float:
    if value < lo:
        assumptions.append(f"{key} を最小値 {lo} に補正")
        return lo
    if value > hi:
        assumptions.append(f"{key} を最大値 {hi} に補正")
        return hi
    return value


def _stable_prompt_seed(prompt: str) -> int:
    digest = hashlib.sha256((prompt or "").encode("utf-8", "replace")).hexdigest()
    return int(digest[:8], 16)


def _pick(source: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _guess_domain(prompt: str) -> str:
    lower = prompt.lower()
    scores: Dict[str, int] = {domain: 0 for domain in ALLOWED_DOMAINS}
    for domain, keys in _DOMAIN_KEYWORDS.items():
        for key in keys:
            if key in prompt or key in lower:
                scores[domain] += 1
    best = max(scores.items(), key=lambda x: x[1])
    return best[0] if best[1] > 0 else "prop"


def _extract_style_tags(prompt: str) -> List[str]:
    lower = prompt.lower()
    tags: List[str] = []
    for tag, keys in _STYLE_KEYWORDS.items():
        if any((k in prompt) or (k in lower) for k in keys):
            tags.append(tag)
    return tags


def _extract_shape_candidates(prompt: str) -> List[str]:
    lower = prompt.lower()
    found: List[str] = []
    for shape, keys in _SHAPE_KEYWORDS.items():
        if any((k in prompt) or (k in lower) for k in keys):
            found.append(shape)
    return _unique_keep_order(found)


def _parse_quality(prompt: str) -> Optional[str]:
    lower = prompt.lower()
    if any(k in prompt or k in lower for k in ("ラフ", "高速", "draft", "quick", "lowpoly")):
        return "draft"
    if any(k in prompt or k in lower for k in ("高品質", "フォトリアル", "high", "realistic", "photoreal")):
        return "high"
    if any(k in prompt or k in lower for k in ("標準", "balanced", "normal")):
        return "balanced"
    return None


def _parse_dimensions(prompt: str) -> Dict[str, float]:
    out: Dict[str, float] = {}

    m3 = _DIMENSION_TRIPLE_RE.search(prompt)
    if m3:
        w = parse_length_m(f"{m3.group(1)}{m3.group(2) or 'm'}")
        d = parse_length_m(f"{m3.group(3)}{m3.group(4) or m3.group(2) or 'm'}")
        h = parse_length_m(f"{m3.group(5)}{m3.group(6) or m3.group(4) or m3.group(2) or 'm'}")
        if w is not None:
            out["width"] = w
        if d is not None:
            out["depth"] = d
        if h is not None:
            out["height"] = h
        return out

    m2 = _DIMENSION_DOUBLE_RE.search(prompt)
    if m2:
        w = parse_length_m(f"{m2.group(1)}{m2.group(2) or 'm'}")
        d = parse_length_m(f"{m2.group(3)}{m2.group(4) or m2.group(2) or 'm'}")
        if w is not None:
            out["width"] = w
        if d is not None:
            out["depth"] = d

    width_m = re.search(r"(?:幅|間口|width)\s*[:=]?\s*([0-9.]+\s*(?:mm|cm|m)?)", prompt.lower())
    depth_m = re.search(r"(?:奥行|奥行き|depth)\s*[:=]?\s*([0-9.]+\s*(?:mm|cm|m)?)", prompt.lower())
    height_m = re.search(r"(?:高さ|height)\s*[:=]?\s*([0-9.]+\s*(?:mm|cm|m)?)", prompt.lower())

    if width_m:
        value = parse_length_m(width_m.group(1))
        if value is not None:
            out["width"] = value
    if depth_m:
        value = parse_length_m(depth_m.group(1))
        if value is not None:
            out["depth"] = value
    if height_m:
        value = parse_length_m(height_m.group(1))
        if value is not None:
            out["height"] = value

    return out


def _parse_prompt(prompt: str) -> Dict[str, Any]:
    text = prompt or ""
    return {
        "domain": _guess_domain(text),
        "quality_mode": _parse_quality(text),
        "dimensions_m": _parse_dimensions(text),
        "style_tags": _extract_style_tags(text),
        "shape_candidates": _extract_shape_candidates(text),
    }


def _default_preset_for_domain(domain: str) -> Dict[str, Any]:
    base = {
        "quality_mode": "balanced",
        "dimensions_m": {"width": 2.0, "depth": 2.0, "height": 2.0},
        "target_constraints": {"poly_budget": 250000, "min_objects": 3},
        "required_outputs": {"blend": True, "renders": 3, "validation_json": True},
        "composition": {"style_tags": [], "parts": [], "selected_assets": []},
        "render_tuning": {
            "key_light_energy": 4.0,
            "fill_light_energy": 250.0,
            "camera_distance_scale": 1.0,
            "exposure": 0.0,
            "saturation": 1.0,
            "value_scale": 1.0,
            "color_balance": "neutral",
        },
        "procedural_controls": {
            "feature_density": 1.0,
            "feature_variation": 0.5,
            "emissive_strength": 0.0,
        },
    }
    if domain == "house":
        base["dimensions_m"] = {"width": 8.0, "depth": 6.0, "height": 7.5}
        base["target_constraints"] = {"poly_budget": 800000, "min_objects": 8}
    elif domain == "vehicle":
        base["dimensions_m"] = {"width": 4.5, "depth": 2.0, "height": 1.8}
        base["target_constraints"] = {"poly_budget": 500000, "min_objects": 10}
    elif domain == "scene":
        base["dimensions_m"] = {"width": 10.0, "depth": 10.0, "height": 3.0}
        base["target_constraints"] = {"poly_budget": 1200000, "min_objects": 12}
    elif domain == "character":
        base["dimensions_m"] = {"width": 0.8, "depth": 0.6, "height": 1.75}
        base["target_constraints"] = {"poly_budget": 900000, "min_objects": 12}
    elif domain == "furniture":
        base["dimensions_m"] = {"width": 1.2, "depth": 0.8, "height": 0.75}
        base["target_constraints"] = {"poly_budget": 180000, "min_objects": 5}
    elif domain == "product":
        base["dimensions_m"] = {"width": 0.35, "depth": 0.25, "height": 0.12}
        base["target_constraints"] = {"poly_budget": 150000, "min_objects": 4}
    else:  # prop
        base["dimensions_m"] = {"width": 2.0, "depth": 2.0, "height": 1.2}
        base["target_constraints"] = {"poly_budget": 120000, "min_objects": 2}
    return base


def _default_parts_for_domain(domain: str) -> List[Dict[str, Any]]:
    if domain == "vehicle":
        return [
            {"name": "body", "shape": "cube", "size": [2.8, 1.4, 0.6], "location": [0.0, 0.0, 0.8]},
            {"name": "cab", "shape": "cube", "size": [1.2, 1.2, 0.45], "location": [0.2, 0.0, 1.3]},
            {"name": "wheel_fl", "shape": "cylinder", "size": [0.35, 0.35, 0.22], "location": [0.9, 0.75, 0.35]},
            {"name": "wheel_fr", "shape": "cylinder", "size": [0.35, 0.35, 0.22], "location": [0.9, -0.75, 0.35]},
            {"name": "wheel_rl", "shape": "cylinder", "size": [0.35, 0.35, 0.22], "location": [-0.9, 0.75, 0.35]},
            {"name": "wheel_rr", "shape": "cylinder", "size": [0.35, 0.35, 0.22], "location": [-0.9, -0.75, 0.35]},
        ]
    if domain == "furniture":
        return [
            {"name": "top", "shape": "cube", "size": [1.2, 0.8, 0.08], "location": [0.0, 0.0, 0.75]},
            {"name": "leg_1", "shape": "cylinder", "size": [0.05, 0.05, 0.7], "location": [0.52, 0.32, 0.35]},
            {"name": "leg_2", "shape": "cylinder", "size": [0.05, 0.05, 0.7], "location": [0.52, -0.32, 0.35]},
            {"name": "leg_3", "shape": "cylinder", "size": [0.05, 0.05, 0.7], "location": [-0.52, 0.32, 0.35]},
            {"name": "leg_4", "shape": "cylinder", "size": [0.05, 0.05, 0.7], "location": [-0.52, -0.32, 0.35]},
        ]
    if domain == "scene":
        return [
            {"name": "room_floor", "shape": "plane", "size": [6.0, 6.0, 0.05], "location": [0.0, 0.0, 0.0]},
            {"name": "centerpiece", "shape": "cube", "size": [1.8, 1.8, 1.8], "location": [0.0, 0.0, 0.9]},
            {"name": "accent", "shape": "sphere", "size": [0.55, 0.55, 0.55], "location": [1.6, 1.2, 0.55]},
        ]
    if domain == "character":
        return [
            {"name": "torso", "shape": "cube", "size": [0.5, 0.3, 0.8], "location": [0.0, 0.0, 1.1]},
            {"name": "head", "shape": "sphere", "size": [0.24, 0.24, 0.24], "location": [0.0, 0.0, 1.75]},
            {"name": "leg_l", "shape": "cylinder", "size": [0.09, 0.09, 0.8], "location": [0.12, 0.0, 0.5]},
            {"name": "leg_r", "shape": "cylinder", "size": [0.09, 0.09, 0.8], "location": [-0.12, 0.0, 0.5]},
        ]
    if domain == "house":
        return [
            {"name": "body", "shape": "cube", "size": [8.0, 6.0, 5.2], "location": [0.0, 0.0, 2.6]},
            {"name": "roof", "shape": "cone", "size": [4.8, 4.8, 2.3], "location": [0.0, 0.0, 6.4]},
            {"name": "door", "shape": "cube", "size": [0.9, 0.2, 2.1], "location": [0.0, -3.1, 1.05]},
        ]
    if domain == "product":
        return [
            {"name": "body", "shape": "cube", "size": [0.35, 0.25, 0.1], "location": [0.0, 0.0, 0.2]},
            {"name": "dial", "shape": "cylinder", "size": [0.05, 0.05, 0.02], "location": [0.12, 0.0, 0.28]},
        ]
    return [
        {"name": "main", "shape": "cube", "size": [1.0, 1.0, 1.0], "location": [0.0, 0.0, 0.5]},
        {"name": "accent", "shape": "sphere", "size": [0.35, 0.35, 0.35], "location": [0.9, 0.0, 0.35]},
    ]


def _normalize_parts(parts: Any) -> List[Dict[str, Any]]:
    if not isinstance(parts, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(parts):
        if not isinstance(item, dict):
            continue
        shape = str(item.get("shape", "cube")).lower()
        if shape not in ALLOWED_SHAPES:
            shape = "cube"
        size = item.get("size") or [1.0, 1.0, 1.0]
        location = item.get("location") or [0.0, 0.0, 0.5]
        rotation_deg = item.get("rotation_deg") or [0.0, 0.0, 0.0]
        color = item.get("color") or [0.8, 0.8, 0.8, 1.0]
        if not (isinstance(size, list) and len(size) == 3):
            size = [1.0, 1.0, 1.0]
        if not (isinstance(location, list) and len(location) == 3):
            location = [0.0, 0.0, 0.5]
        if not (isinstance(rotation_deg, list) and len(rotation_deg) == 3):
            rotation_deg = [0.0, 0.0, 0.0]
        if not (isinstance(color, list) and len(color) == 4):
            color = [0.8, 0.8, 0.8, 1.0]
        normalized.append(
            {
                "name": str(item.get("name") or f"part_{idx:02d}"),
                "shape": shape,
                "size": [float(max(0.01, _to_float(v) or 1.0)) for v in size],
                "location": [float(_to_float(v) or 0.0) for v in location],
                "rotation_deg": [float(_to_float(v) or 0.0) for v in rotation_deg],
                "color": [float(min(1.0, max(0.0, _to_float(v) or 0.8))) for v in color],
            }
        )
    return normalized


def normalize_asset_spec(
    prompt: str,
    form_data: Optional[Dict[str, Any]],
    preset: Optional[Dict[str, Any]] = None,
    domain_override: Optional[str] = None,
) -> Dict[str, Any]:
    form_data = form_data or {}
    parsed = _parse_prompt(prompt)
    assumptions: List[str] = []

    explicit_domain = str(domain_override or _pick(form_data, ["domain"]) or "").strip().lower()
    domain = explicit_domain if explicit_domain else parsed["domain"]
    if domain in ("", "auto"):
        domain = parsed["domain"]
    if domain not in ALLOWED_DOMAINS:
        assumptions.append(f"未知ドメイン '{domain}' を prop として処理")
        domain = "prop"

    spec = _default_preset_for_domain(domain)
    if isinstance(preset, dict):
        spec = copy.deepcopy(spec)
        for key, value in preset.items():
            spec[key] = value

    spec["domain"] = domain
    spec["intent"] = str(_pick(form_data, ["intent", "title"]) or prompt or f"Generate {domain} asset")
    raw_seed = _to_float(_pick(form_data, ["random_seed", "seed"]))
    if raw_seed is None:
        spec["random_seed"] = _stable_prompt_seed(prompt)
    else:
        spec["random_seed"] = int(_clamp(float(raw_seed), 0.0, 4294967295.0, "random_seed", assumptions))

    quality = str(
        _pick(form_data, ["quality_mode", "quality", "qualityMode"]) or parsed.get("quality_mode") or spec.get("quality_mode")
    ).lower()
    if quality not in ALLOWED_QUALITY:
        assumptions.append("quality_mode が未知値のため balanced を使用")
        quality = "balanced"
    spec["quality_mode"] = quality

    dims = dict(spec.get("dimensions_m", {}))
    parsed_dims = parsed.get("dimensions_m") or {}
    for key in ("width", "depth", "height"):
        from_form = parse_length_m(_pick(form_data, [f"{key}", f"{key}_m", key.upper()]))
        if from_form is not None:
            dims[key] = from_form
            continue
        if key in parsed_dims:
            dims[key] = parsed_dims[key]

    for key, fallback in (("width", 1.0), ("depth", 1.0), ("height", 1.0)):
        value = _to_float(dims.get(key))
        if value is None:
            assumptions.append(f"dimensions_m.{key} 未指定のため既定値を使用")
            value = fallback
        dims[key] = _clamp(float(value), 0.1, 200.0, f"dimensions_m.{key}", assumptions)
    spec["dimensions_m"] = dims

    style_tags = list(spec.get("composition", {}).get("style_tags", []))
    style_tags.extend(parsed.get("style_tags") or [])
    style_tags.extend(form_data.get("style_tags", []) if isinstance(form_data.get("style_tags"), list) else [])
    style_tags = _unique_keep_order([str(x) for x in style_tags])

    parts = _normalize_parts(_pick(form_data, ["parts", "composition.parts"]))
    if not parts:
        shape_candidates = parsed.get("shape_candidates") or []
        if shape_candidates:
            parts = []
            for idx, shape in enumerate(shape_candidates[:8]):
                parts.append(
                    {
                        "name": f"shape_{idx:02d}",
                        "shape": shape if shape in ALLOWED_SHAPES else "cube",
                        "size": [1.0, 1.0, 1.0],
                        "location": [idx * 1.4, 0.0, 0.5],
                        "rotation_deg": [0.0, 0.0, 0.0],
                        "color": [0.8, 0.8, 0.8, 1.0],
                    }
                )
        else:
            parts = _default_parts_for_domain(domain)
            assumptions.append("parts 未指定のためドメイン既定パーツを使用")

    composition = spec.get("composition", {}) if isinstance(spec.get("composition"), dict) else {}
    composition["style_tags"] = style_tags
    composition["parts"] = parts
    selected_assets = form_data.get("selected_assets") if isinstance(form_data.get("selected_assets"), list) else []
    composition["selected_assets"] = selected_assets
    spec["composition"] = composition

    constraints = dict(spec.get("target_constraints", {}))
    poly_budget = _to_float(_pick(form_data, ["poly_budget", "target_constraints.poly_budget"]))
    if poly_budget is not None:
        constraints["poly_budget"] = int(poly_budget)

    min_objects = _to_float(_pick(form_data, ["min_objects", "target_constraints.min_objects"]))
    if min_objects is None:
        base_min = max(1, len(parts) + len(selected_assets))
        constraints["min_objects"] = base_min
    else:
        constraints["min_objects"] = int(min_objects)

    quality_overrides = {
        "draft": 120000,
        "balanced": 300000,
        "high": 1200000,
    }
    constraints["poly_budget"] = int(
        _clamp(
            float(constraints.get("poly_budget", quality_overrides[quality])),
            100.0,
            50000000.0,
            "target_constraints.poly_budget",
            assumptions,
        )
    )
    constraints["min_objects"] = int(
        _clamp(
            float(constraints.get("min_objects", 1)),
            1.0,
            10000.0,
            "target_constraints.min_objects",
            assumptions,
        )
    )
    spec["target_constraints"] = constraints

    render_tuning = dict(spec.get("render_tuning", {}))
    procedural_controls = dict(spec.get("procedural_controls", {}))

    def _pick_numeric(src: Dict[str, Any], keys: List[str]) -> Optional[float]:
        value = _pick(src, keys)
        return _to_float(value)

    key_light = _pick_numeric(form_data, ["key_light_energy", "render_tuning.key_light_energy"])
    fill_light = _pick_numeric(form_data, ["fill_light_energy", "render_tuning.fill_light_energy"])
    camera_scale = _pick_numeric(form_data, ["camera_distance_scale", "render_tuning.camera_distance_scale"])
    exposure = _pick_numeric(form_data, ["exposure", "render_tuning.exposure"])
    saturation = _pick_numeric(form_data, ["saturation", "render_tuning.saturation"])
    value_scale = _pick_numeric(form_data, ["value_scale", "render_tuning.value_scale"])
    color_balance = str(_pick(form_data, ["color_balance", "render_tuning.color_balance"]) or render_tuning.get("color_balance", "neutral")).strip().lower()

    feature_density = _pick_numeric(form_data, ["feature_density", "procedural_controls.feature_density"])
    feature_variation = _pick_numeric(form_data, ["feature_variation", "procedural_controls.feature_variation"])
    emissive_strength = _pick_numeric(form_data, ["emissive_strength", "procedural_controls.emissive_strength"])

    render_tuning["key_light_energy"] = _clamp(float(key_light if key_light is not None else render_tuning.get("key_light_energy", 4.0)), 0.1, 20000.0, "render_tuning.key_light_energy", assumptions)
    render_tuning["fill_light_energy"] = _clamp(float(fill_light if fill_light is not None else render_tuning.get("fill_light_energy", 250.0)), 0.1, 20000.0, "render_tuning.fill_light_energy", assumptions)
    render_tuning["camera_distance_scale"] = _clamp(float(camera_scale if camera_scale is not None else render_tuning.get("camera_distance_scale", 1.0)), 0.35, 3.2, "render_tuning.camera_distance_scale", assumptions)
    render_tuning["exposure"] = _clamp(float(exposure if exposure is not None else render_tuning.get("exposure", 0.0)), -5.0, 5.0, "render_tuning.exposure", assumptions)
    render_tuning["saturation"] = _clamp(float(saturation if saturation is not None else render_tuning.get("saturation", 1.0)), 0.2, 2.5, "render_tuning.saturation", assumptions)
    render_tuning["value_scale"] = _clamp(float(value_scale if value_scale is not None else render_tuning.get("value_scale", 1.0)), 0.2, 2.0, "render_tuning.value_scale", assumptions)
    if color_balance not in ("neutral", "cool", "warm"):
        assumptions.append("render_tuning.color_balance が未知値のため neutral を使用")
        color_balance = "neutral"
    render_tuning["color_balance"] = color_balance
    spec["render_tuning"] = render_tuning

    procedural_controls["feature_density"] = _clamp(
        float(feature_density if feature_density is not None else procedural_controls.get("feature_density", 1.0)),
        0.2,
        5.0,
        "procedural_controls.feature_density",
        assumptions,
    )
    procedural_controls["feature_variation"] = _clamp(
        float(feature_variation if feature_variation is not None else procedural_controls.get("feature_variation", 0.5)),
        0.05,
        1.0,
        "procedural_controls.feature_variation",
        assumptions,
    )
    procedural_controls["emissive_strength"] = _clamp(
        float(emissive_strength if emissive_strength is not None else procedural_controls.get("emissive_strength", 0.0)),
        0.0,
        8.0,
        "procedural_controls.emissive_strength",
        assumptions,
    )
    spec["procedural_controls"] = procedural_controls

    required_outputs = dict(spec.get("required_outputs", {}))
    required_outputs.setdefault("blend", True)
    required_outputs.setdefault("renders", 3)
    required_outputs.setdefault("validation_json", True)
    required_outputs["renders"] = int(_clamp(float(required_outputs.get("renders", 3)), 1.0, 10.0, "required_outputs.renders", assumptions))
    spec["required_outputs"] = required_outputs

    spec["assumptions"] = assumptions
    spec["source_prompt"] = prompt
    return spec


def validate_asset_spec(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ("domain", "intent", "quality_mode", "dimensions_m", "composition", "target_constraints", "required_outputs"):
        if key not in spec:
            errors.append(f"required key missing: {key}")

    domain = str(spec.get("domain", ""))
    if domain not in ALLOWED_DOMAINS:
        errors.append("domain is invalid")

    quality = str(spec.get("quality_mode", ""))
    if quality not in ALLOWED_QUALITY:
        errors.append("quality_mode is invalid")

    seed = _to_float(spec.get("random_seed"))
    if seed is None or seed < 0 or seed > 4294967295:
        errors.append("random_seed out of range")

    dims = spec.get("dimensions_m")
    if not isinstance(dims, dict):
        errors.append("dimensions_m must be object")
    else:
        for key in ("width", "depth", "height"):
            value = _to_float(dims.get(key))
            if value is None:
                errors.append(f"dimensions_m.{key} is required")
            elif value < 0.1 or value > 200.0:
                errors.append(f"dimensions_m.{key} out of range")

    composition = spec.get("composition")
    if not isinstance(composition, dict):
        errors.append("composition must be object")
    else:
        parts = composition.get("parts")
        if not isinstance(parts, list) or not parts:
            errors.append("composition.parts must be non-empty array")
        else:
            for i, part in enumerate(parts):
                if not isinstance(part, dict):
                    errors.append(f"composition.parts[{i}] must be object")
                    continue
                shape = str(part.get("shape", ""))
                if shape not in ALLOWED_SHAPES:
                    errors.append(f"composition.parts[{i}].shape is invalid")

    constraints = spec.get("target_constraints")
    if not isinstance(constraints, dict):
        errors.append("target_constraints must be object")
    else:
        poly = _to_float(constraints.get("poly_budget"))
        if poly is None or poly < 100 or poly > 50000000:
            errors.append("target_constraints.poly_budget out of range")
        min_obj = _to_float(constraints.get("min_objects"))
        if min_obj is None or min_obj < 1 or min_obj > 10000:
            errors.append("target_constraints.min_objects out of range")

    required_outputs = spec.get("required_outputs")
    if not isinstance(required_outputs, dict):
        errors.append("required_outputs must be object")
    else:
        renders = _to_float(required_outputs.get("renders"))
        if renders is None or renders < 1 or renders > 10:
            errors.append("required_outputs.renders out of range")

    render_tuning = spec.get("render_tuning")
    if render_tuning is not None:
        if not isinstance(render_tuning, dict):
            errors.append("render_tuning must be object")
        else:
            for key, lo, hi in (
                ("key_light_energy", 0.1, 20000.0),
                ("fill_light_energy", 0.1, 20000.0),
                ("camera_distance_scale", 0.35, 3.2),
                ("exposure", -5.0, 5.0),
                ("saturation", 0.2, 2.5),
                ("value_scale", 0.2, 2.0),
            ):
                value = _to_float(render_tuning.get(key))
                if value is None or value < lo or value > hi:
                    errors.append(f"render_tuning.{key} out of range")
            if str(render_tuning.get("color_balance", "neutral")) not in ("neutral", "cool", "warm"):
                errors.append("render_tuning.color_balance is invalid")

    procedural_controls = spec.get("procedural_controls")
    if procedural_controls is not None:
        if not isinstance(procedural_controls, dict):
            errors.append("procedural_controls must be object")
        else:
            for key, lo, hi in (
                ("feature_density", 0.2, 5.0),
                ("feature_variation", 0.05, 1.0),
                ("emissive_strength", 0.0, 8.0),
            ):
                value = _to_float(procedural_controls.get(key))
                if value is None or value < lo or value > hi:
                    errors.append(f"procedural_controls.{key} out of range")

    return errors


def _get_by_path(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _set_by_path(data: Dict[str, Any], path: str, value: Any) -> bool:
    current: Any = data
    segments = path.split(".")
    for segment in segments[:-1]:
        if segment not in current or not isinstance(current[segment], dict):
            current[segment] = {}
        current = current[segment]
    current[segments[-1]] = value
    return True


_REPAIR_NUMERIC_BOUNDS: Dict[str, Tuple[float, float]] = {
    "dimensions_m.width": (0.1, 200.0),
    "dimensions_m.depth": (0.1, 200.0),
    "dimensions_m.height": (0.1, 200.0),
    "target_constraints.poly_budget": (100.0, 50000000.0),
    "target_constraints.min_objects": (1.0, 10000.0),
    "render_tuning.key_light_energy": (0.1, 20000.0),
    "render_tuning.fill_light_energy": (0.1, 20000.0),
    "render_tuning.camera_distance_scale": (0.35, 3.2),
    "render_tuning.exposure": (-5.0, 5.0),
    "render_tuning.saturation": (0.2, 2.5),
    "render_tuning.value_scale": (0.2, 2.0),
    "procedural_controls.feature_density": (0.2, 5.0),
    "procedural_controls.feature_variation": (0.05, 1.0),
    "procedural_controls.emissive_strength": (0.0, 8.0),
}
_REPAIR_NUMERIC_MAX_STEP: Dict[str, float] = {
    "render_tuning.key_light_energy": 2.0,
    "render_tuning.fill_light_energy": 120.0,
    "render_tuning.camera_distance_scale": 0.1,
    "render_tuning.exposure": 0.12,
    "render_tuning.saturation": 0.06,
    "render_tuning.value_scale": 0.08,
    "procedural_controls.feature_density": 0.15,
    "procedural_controls.feature_variation": 0.1,
    "procedural_controls.emissive_strength": 0.15,
}


def _clamp_repair_numeric(key: str, value: float) -> float:
    bounds = _REPAIR_NUMERIC_BOUNDS.get(key)
    if not bounds:
        return float(value)
    lo, hi = bounds
    return max(lo, min(hi, float(value)))


def _damp_repair_step(current: Optional[float], key: str, proposed: float) -> float:
    if current is None:
        return _clamp_repair_numeric(key, proposed)
    max_step = _REPAIR_NUMERIC_MAX_STEP.get(key)
    if max_step is None:
        return _clamp_repair_numeric(key, proposed)
    delta = float(proposed) - float(current)
    if delta > max_step:
        proposed = float(current) + max_step
    elif delta < -max_step:
        proposed = float(current) - max_step
    return _clamp_repair_numeric(key, proposed)


def apply_repair_actions(spec: Dict[str, Any], actions: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    next_spec = copy.deepcopy(spec)
    applied: List[str] = []

    for item in actions or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        key = str(item.get("key", "")).strip()
        if not action or not key:
            continue

        if action == "set_value" and "value" in item:
            raw_value = item["value"]
            numeric = _to_float(raw_value)
            if numeric is not None and key in _REPAIR_NUMERIC_BOUNDS:
                current = _to_float(_get_by_path(next_spec, key))
                bounded = _damp_repair_step(current=current, key=key, proposed=float(numeric))
                _set_by_path(next_spec, key, bounded)
                applied.append(f"{key} を {bounded} に設定")
            else:
                _set_by_path(next_spec, key, raw_value)
                applied.append(f"{key} を {raw_value} に設定")
            continue

        if action == "clamp_range":
            current = _to_float(_get_by_path(next_spec, key))
            lower = _to_float(item.get("min"))
            upper = _to_float(item.get("max"))
            if current is None:
                continue
            new_value = current
            if lower is not None and new_value < lower:
                new_value = lower
            if upper is not None and new_value > upper:
                new_value = upper
            if new_value != current:
                _set_by_path(next_spec, key, new_value)
                applied.append(f"{key} をレンジ補正 ({current} -> {new_value})")
            continue

        if action == "scale_value":
            current = _to_float(_get_by_path(next_spec, key))
            factor = _to_float(item.get("factor"))
            if current is None or factor is None:
                continue
            if key.startswith("dimensions_m."):
                factor = max(0.6, min(1.6, factor))
            elif key in _REPAIR_NUMERIC_BOUNDS:
                factor = max(0.75, min(1.25, factor))
            else:
                factor = max(0.5, min(2.0, factor))
            new_value = current * factor
            new_value = _damp_repair_step(current=current, key=key, proposed=new_value)
            _set_by_path(next_spec, key, new_value)
            applied.append(f"{key} をスケール補正 ({current} -> {new_value})")
            continue

        if action == "remove_selected_asset":
            target_id = str(item.get("value", "")).strip()
            assets = _get_by_path(next_spec, "composition.selected_assets")
            if not target_id or not isinstance(assets, list):
                continue
            before = len(assets)
            assets = [x for x in assets if str(x.get("id", "")) != target_id] if assets else []
            if len(assets) != before:
                _set_by_path(next_spec, "composition.selected_assets", assets)
                applied.append(f"selected asset '{target_id}' を除外")
            continue

    return next_spec, applied
