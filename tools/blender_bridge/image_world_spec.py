"""
image_world_spec.py - 参照画像からワールド生成用specを正規化

方針:
- 有料APIは使わない（ローカル解析のみ）
- 参照画像がなくても動くが、画像があるほど初期specを強く拘束
- 出力は universal_agent が解釈できる form_data へ変換可能
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from universal_spec import normalize_asset_spec, parse_length_m


_VIEW_HINTS: Dict[str, Tuple[str, ...]] = {
    "front": ("front", "frontal", "正面"),
    "back": ("back", "rear", "背面"),
    "left": ("left", "左"),
    "right": ("right", "右"),
    "side": ("side", "横", "側面"),
    "top": ("top", "bird", "俯瞰", "上"),
    "detail": ("detail", "close", "部分"),
}

_DARK_KEYWORDS = (
    "dark",
    "dungeon",
    "catacomb",
    "crypt",
    "gothic",
    "ボス部屋",
    "暗い",
    "地下",
    "ダンジョン",
    "石造り",
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


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _pick(data: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _unique_keep_order(values: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        token = str(value).strip()
        if not token:
            continue
        lower = token.lower()
        if lower in seen:
            continue
        seen.add(lower)
        out.append(token)
    return out


def _guess_view_from_path(path: str, explicit_view: str = "") -> str:
    view = explicit_view.strip().lower()
    if view:
        return view

    lower = Path(path).name.lower()
    for canonical, hints in _VIEW_HINTS.items():
        if any(h in lower for h in hints):
            return canonical
    return "reference"


def parse_reference_images_arg(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_list = value
    elif isinstance(value, dict):
        if isinstance(value.get("reference_images"), list):
            raw_list = value.get("reference_images")
        elif isinstance(value.get("images"), list):
            raw_list = value.get("images")
        else:
            raw_list = []
    else:
        text = str(value).strip()
        if not text:
            return []
        candidate = Path(text)
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".json":
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except Exception:
                payload = None
            if isinstance(payload, list):
                raw_list = payload
            elif isinstance(payload, dict) and isinstance(payload.get("reference_images"), list):
                raw_list = payload.get("reference_images")
            else:
                raw_list = []
        else:
            try:
                payload = json.loads(text)
            except Exception:
                payload = None
            if isinstance(payload, list):
                raw_list = payload
            elif isinstance(payload, dict) and isinstance(payload.get("reference_images"), list):
                raw_list = payload.get("reference_images")
            else:
                raw_list = [token.strip() for token in text.split(",") if token.strip()]

    out: List[Dict[str, Any]] = []
    for item in raw_list:
        if isinstance(item, str):
            path = item.strip()
            if not path:
                continue
            out.append({"path": path, "view": _guess_view_from_path(path)})
            continue
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path:
            continue
        view = _guess_view_from_path(path, explicit_view=str(item.get("view", "")).strip())
        payload = {"path": path, "view": view}
        if "weight" in item:
            w = _to_float(item.get("weight"))
            if w is not None:
                payload["weight"] = _clamp(w, 0.0, 5.0)
        out.append(payload)

    return out


def _safe_analyze_image(path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists() and path.is_file(),
        "analyzed": False,
        "width": None,
        "height": None,
        "brightness": None,
        "contrast": None,
        "saturation": None,
        "cool_ratio": None,
        "edge_density": None,
        "error": None,
    }
    if not info["exists"]:
        return info

    try:
        from PIL import Image, ImageFilter, ImageStat  # type: ignore

        with Image.open(path) as img:
            rgb = img.convert("RGB")
            width, height = rgb.size
            info["width"] = int(width)
            info["height"] = int(height)

            resized = rgb.resize((max(32, min(192, width)), max(32, min(192, height))))
            stat = ImageStat.Stat(resized)
            mean_r = float(stat.mean[0]) / 255.0
            mean_g = float(stat.mean[1]) / 255.0
            mean_b = float(stat.mean[2]) / 255.0
            brightness = (0.2126 * mean_r) + (0.7152 * mean_g) + (0.0722 * mean_b)
            variance = sum(float(v) for v in stat.var) / 3.0
            contrast = (variance ** 0.5) / 255.0

            pixels = list(resized.getdata())
            sat_sum = 0.0
            for r, g, b in pixels:
                rn = r / 255.0
                gn = g / 255.0
                bn = b / 255.0
                sat_sum += max(rn, gn, bn) - min(rn, gn, bn)
            saturation = sat_sum / max(1, len(pixels))

            edges = resized.filter(ImageFilter.FIND_EDGES)
            edge_stat = ImageStat.Stat(edges)
            edge_density = (sum(float(v) for v in edge_stat.mean) / 3.0) / 255.0

            info["brightness"] = float(_clamp(brightness, 0.0, 1.0))
            info["contrast"] = float(_clamp(contrast, 0.0, 1.0))
            info["saturation"] = float(_clamp(saturation, 0.0, 1.0))
            info["cool_ratio"] = float(mean_b / max(1.0e-6, mean_r + mean_g + mean_b))
            info["edge_density"] = float(_clamp(edge_density, 0.0, 1.0))
            info["analyzed"] = True
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"

    return info


def analyze_reference_images(reference_images: List[Dict[str, Any]]) -> Dict[str, Any]:
    analyzed_images: List[Dict[str, Any]] = []
    for idx, item in enumerate(reference_images):
        path = Path(str(item.get("path", "")).strip())
        payload = _safe_analyze_image(path)
        payload["index"] = idx
        payload["view"] = str(item.get("view", "reference")).strip().lower() or "reference"
        payload["weight"] = float(_clamp(_to_float(item.get("weight")) or 1.0, 0.0, 5.0))
        analyzed_images.append(payload)

    valid = [x for x in analyzed_images if x.get("exists")]
    analyzed = [x for x in valid if x.get("analyzed")]
    summary: Dict[str, Any] = {
        "reference_count": len(reference_images),
        "existing_count": len(valid),
        "analyzed_count": len(analyzed),
        "views": sorted({str(x.get("view", "reference")) for x in analyzed_images}),
        "brightness_mean": None,
        "contrast_mean": None,
        "saturation_mean": None,
        "cool_ratio_mean": None,
        "edge_density_mean": None,
        "primary_aspect_ratio": None,
        "image_size_hint": None,
    }

    if analyzed:
        def _avg(key: str) -> Optional[float]:
            vals = [float(x[key]) for x in analyzed if x.get(key) is not None]
            if not vals:
                return None
            return float(sum(vals) / len(vals))

        summary["brightness_mean"] = _avg("brightness")
        summary["contrast_mean"] = _avg("contrast")
        summary["saturation_mean"] = _avg("saturation")
        summary["cool_ratio_mean"] = _avg("cool_ratio")
        summary["edge_density_mean"] = _avg("edge_density")

        first = analyzed[0]
        w = _to_float(first.get("width"))
        h = _to_float(first.get("height"))
        if w and h and h > 0:
            summary["primary_aspect_ratio"] = float(w / h)

    if valid:
        areas = []
        for item in valid:
            w = _to_float(item.get("width"))
            h = _to_float(item.get("height"))
            if w and h:
                areas.append(w * h)
        if areas:
            mean_area = float(sum(areas) / len(areas))
            if mean_area >= (1600 * 900):
                summary["image_size_hint"] = "large"
            elif mean_area >= (960 * 540):
                summary["image_size_hint"] = "medium"
            else:
                summary["image_size_hint"] = "small"

    return {"images": analyzed_images, "summary": summary}


def _extract_dark_intent(prompt: str, style_tags: List[str], brightness: Optional[float]) -> bool:
    lower = prompt.lower()
    if any(token in lower for token in _DARK_KEYWORDS):
        return True
    if any(tag.lower() in {"dark", "dungeon", "gothic"} for tag in style_tags):
        return True
    if brightness is not None and brightness < 0.42:
        return True
    return False


def _compose_room_parts(
    width: float,
    depth: float,
    height: float,
    style_dark: bool,
    cool_ratio: Optional[float],
    detail_factor: float,
) -> List[Dict[str, Any]]:
    w = _clamp(width, 4.0, 60.0)
    d = _clamp(depth, 4.0, 60.0)
    h = _clamp(height, 2.4, 24.0)

    wall_t = _clamp(min(w, d) * 0.03, 0.16, 0.45)
    floor_t = _clamp(h * 0.02, 0.06, 0.2)
    door_w = _clamp(w * 0.16, 1.2, 2.6)
    door_h = _clamp(h * 0.56, 2.0, 3.8)

    base_dark = [0.13, 0.13, 0.14, 1.0] if style_dark else [0.48, 0.46, 0.42, 1.0]
    wall_dark = [0.1, 0.1, 0.11, 1.0] if style_dark else [0.55, 0.53, 0.49, 1.0]
    accent = [0.22, 0.3, 0.42, 1.0] if (cool_ratio is not None and cool_ratio >= 0.35) else [0.42, 0.3, 0.2, 1.0]

    parts: List[Dict[str, Any]] = [
        {"name": "room_floor", "shape": "cube", "size": [w, d, floor_t], "location": [0.0, 0.0, floor_t * 0.5], "color": base_dark},
        {"name": "room_ceiling", "shape": "cube", "size": [w, d, floor_t], "location": [0.0, 0.0, h - (floor_t * 0.5)], "color": wall_dark},
        {"name": "wall_north", "shape": "cube", "size": [w, wall_t, h], "location": [0.0, d * 0.5, h * 0.5], "color": wall_dark},
        {"name": "wall_south", "shape": "cube", "size": [w, wall_t, h], "location": [0.0, -d * 0.5, h * 0.5], "color": wall_dark},
        {"name": "wall_east", "shape": "cube", "size": [wall_t, d, h], "location": [w * 0.5, 0.0, h * 0.5], "color": wall_dark},
        {"name": "wall_west", "shape": "cube", "size": [wall_t, d, h], "location": [-w * 0.5, 0.0, h * 0.5], "color": wall_dark},
        {"name": "door_frame_top", "shape": "cube", "size": [door_w, wall_t * 1.1, max(0.15, h * 0.03)], "location": [0.0, -d * 0.5, door_h + max(0.08, h * 0.015)], "color": accent},
        {"name": "door_frame_left", "shape": "cube", "size": [max(0.15, wall_t * 1.2), wall_t * 1.1, door_h], "location": [-(door_w * 0.5) - max(0.07, wall_t * 0.2), -d * 0.5, door_h * 0.5], "color": accent},
        {"name": "door_frame_right", "shape": "cube", "size": [max(0.15, wall_t * 1.2), wall_t * 1.1, door_h], "location": [(door_w * 0.5) + max(0.07, wall_t * 0.2), -d * 0.5, door_h * 0.5], "color": accent},
        {"name": "door_panel", "shape": "cube", "size": [door_w * 0.92, wall_t * 0.4, door_h * 0.94], "location": [0.0, -d * 0.5 + (wall_t * 0.35), door_h * 0.47], "color": [0.2, 0.16, 0.12, 1.0]},
    ]

    if detail_factor >= 1.0:
        col_r = _clamp(min(w, d) * 0.03, 0.12, 0.35)
        col_h = h * 0.72
        x = w * 0.35
        y = d * 0.3
        for idx, (cx, cy) in enumerate(((x, y), (-x, y), (x, -y), (-x, -y))):
            parts.append(
                {
                    "name": f"pillar_{idx:02d}",
                    "shape": "cylinder",
                    "size": [col_r, col_r, col_h],
                    "location": [cx, cy, col_h * 0.5],
                    "color": [wall_dark[0] * 0.85, wall_dark[1] * 0.85, wall_dark[2] * 0.9, 1.0],
                }
            )

    if detail_factor >= 1.3:
        altar_w = _clamp(w * 0.22, 1.2, 4.0)
        altar_d = _clamp(d * 0.12, 0.9, 3.0)
        altar_h = _clamp(h * 0.12, 0.45, 1.4)
        parts.append(
            {
                "name": "center_altar",
                "shape": "cube",
                "size": [altar_w, altar_d, altar_h],
                "location": [0.0, d * 0.14, altar_h * 0.5],
                "color": accent,
            }
        )

    return parts


def normalize_image_world_spec(
    prompt: str,
    form_data: Optional[Dict[str, Any]],
    reference_images: List[Dict[str, Any]],
    preset: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    form_data = form_data or {}
    analysis = analyze_reference_images(reference_images)
    summary = analysis.get("summary", {}) if isinstance(analysis.get("summary"), dict) else {}

    # 既存 universal spec を土台にし、domain を scene に固定する
    spec = normalize_asset_spec(
        prompt=prompt,
        form_data={**form_data, "domain": "scene"},
        preset=preset,
        domain_override="scene",
    )

    assumptions = [str(x) for x in spec.get("assumptions", [])]
    style_tags = list(spec.get("composition", {}).get("style_tags", [])) if isinstance(spec.get("composition"), dict) else []
    style_tags.extend(["reference_world", "single_room"])

    brightness = _to_float(summary.get("brightness_mean"))
    cool_ratio = _to_float(summary.get("cool_ratio_mean"))
    edge_density = _to_float(summary.get("edge_density_mean")) or 0.18

    style_dark = _extract_dark_intent(prompt=prompt, style_tags=style_tags, brightness=brightness)
    if style_dark:
        style_tags.extend(["dark", "dungeon", "stone"])

    aspect = _to_float(summary.get("primary_aspect_ratio")) or 1.0
    width_hint = parse_length_m(_pick(form_data, ["room_width_m", "width_m", "width"]))
    depth_hint = parse_length_m(_pick(form_data, ["room_depth_m", "depth_m", "depth"]))
    height_hint = parse_length_m(_pick(form_data, ["room_height_m", "height_m", "height"]))

    dims = dict(spec.get("dimensions_m", {})) if isinstance(spec.get("dimensions_m"), dict) else {}
    if width_hint is not None:
        dims["width"] = width_hint
    if depth_hint is not None:
        dims["depth"] = depth_hint
    if height_hint is not None:
        dims["height"] = height_hint

    if width_hint is None or depth_hint is None:
        if aspect >= 1.25:
            dims.setdefault("width", 12.0)
            dims.setdefault("depth", 8.0)
        elif aspect <= 0.82:
            dims.setdefault("width", 8.5)
            dims.setdefault("depth", 11.5)
        else:
            dims.setdefault("width", 10.0)
            dims.setdefault("depth", 9.0)
    if height_hint is None:
        dims["height"] = max(float(_to_float(dims.get("height")) or 0.0), (4.8 if style_dark else 3.8))
    else:
        dims["height"] = height_hint

    dims["width"] = _clamp(float(_to_float(dims.get("width")) or 10.0), 4.0, 60.0)
    dims["depth"] = _clamp(float(_to_float(dims.get("depth")) or 9.0), 4.0, 60.0)
    dims["height"] = _clamp(float(_to_float(dims.get("height")) or (4.8 if style_dark else 3.8)), 2.4, 24.0)
    spec["dimensions_m"] = dims

    detail_factor = _clamp(0.9 + (edge_density * 1.8), 0.8, 1.6)
    parts = _compose_room_parts(
        width=float(dims["width"]),
        depth=float(dims["depth"]),
        height=float(dims["height"]),
        style_dark=style_dark,
        cool_ratio=cool_ratio,
        detail_factor=detail_factor,
    )
    composition = dict(spec.get("composition", {})) if isinstance(spec.get("composition"), dict) else {}
    composition["parts"] = parts
    composition["style_tags"] = _unique_keep_order(style_tags)
    spec["composition"] = composition

    target_constraints = dict(spec.get("target_constraints", {})) if isinstance(spec.get("target_constraints"), dict) else {}
    target_constraints["min_objects"] = max(int(target_constraints.get("min_objects", 1) or 1), max(8, len(parts) - 1))
    poly_base = int(target_constraints.get("poly_budget", 350000) or 350000)
    poly_hint = int(260000 + (edge_density * 650000))
    target_constraints["poly_budget"] = max(poly_base, poly_hint)
    spec["target_constraints"] = target_constraints

    render_tuning = dict(spec.get("render_tuning", {})) if isinstance(spec.get("render_tuning"), dict) else {}
    if style_dark:
        render_tuning["exposure"] = _clamp(-0.75 + ((brightness if brightness is not None else 0.35) * 0.55), -2.6, 0.6)
        render_tuning["key_light_energy"] = _clamp(2.0 + (edge_density * 4.5), 1.0, 12.0)
        render_tuning["fill_light_energy"] = _clamp(80.0 + ((brightness if brightness is not None else 0.35) * 220.0), 35.0, 420.0)
        render_tuning["saturation"] = _clamp(0.75 + ((_to_float(summary.get("saturation_mean")) or 0.18) * 1.0), 0.45, 1.35)
        render_tuning["value_scale"] = _clamp(0.68 + ((brightness if brightness is not None else 0.35) * 0.5), 0.45, 1.1)
    else:
        render_tuning["exposure"] = _clamp(-0.15 + ((brightness if brightness is not None else 0.5) * 0.3), -1.2, 1.2)
        render_tuning["key_light_energy"] = _clamp(3.6 + (edge_density * 4.0), 2.0, 16.0)
        render_tuning["fill_light_energy"] = _clamp(180.0 + ((brightness if brightness is not None else 0.5) * 240.0), 80.0, 600.0)
        render_tuning["saturation"] = _clamp(0.9 + ((_to_float(summary.get("saturation_mean")) or 0.25) * 0.7), 0.6, 1.6)
        render_tuning["value_scale"] = _clamp(0.86 + ((brightness if brightness is not None else 0.5) * 0.4), 0.65, 1.35)

    render_tuning["camera_distance_scale"] = _clamp(0.85 + (edge_density * 0.7), 0.75, 1.5)
    if cool_ratio is not None and cool_ratio >= 0.37:
        render_tuning["color_balance"] = "cool"
    elif cool_ratio is not None and cool_ratio <= 0.28:
        render_tuning["color_balance"] = "warm"
    else:
        render_tuning["color_balance"] = "neutral"
    spec["render_tuning"] = render_tuning

    procedural_controls = dict(spec.get("procedural_controls", {})) if isinstance(spec.get("procedural_controls"), dict) else {}
    procedural_controls["feature_density"] = _clamp(0.85 + (edge_density * 2.2), 0.6, 2.1)
    procedural_controls["feature_variation"] = _clamp(0.35 + (edge_density * 1.5), 0.2, 0.95)
    procedural_controls["emissive_strength"] = _clamp(0.04 + ((1.0 - (brightness if brightness is not None else 0.45)) * 0.35), 0.0, 0.8)
    spec["procedural_controls"] = procedural_controls

    if summary.get("existing_count", 0) <= 0:
        assumptions.append("reference_images が見つからなかったため prompt 主導で部屋構成を生成")
    elif summary.get("analyzed_count", 0) <= 0:
        assumptions.append("画像解析ライブラリ未利用のためファイル存在情報のみで初期値を推定")

    spec["reference_images"] = analysis.get("images", [])
    spec["reference_analysis"] = summary
    assumptions = [x for x in assumptions if "parts 未指定のためドメイン既定パーツを使用" not in x]
    spec["assumptions"] = _unique_keep_order([str(x) for x in assumptions])
    return spec


def to_universal_form_data(spec: Dict[str, Any]) -> Dict[str, Any]:
    dims = spec.get("dimensions_m", {}) if isinstance(spec.get("dimensions_m"), dict) else {}
    composition = spec.get("composition", {}) if isinstance(spec.get("composition"), dict) else {}
    constraints = spec.get("target_constraints", {}) if isinstance(spec.get("target_constraints"), dict) else {}
    render_tuning = spec.get("render_tuning", {}) if isinstance(spec.get("render_tuning"), dict) else {}
    controls = spec.get("procedural_controls", {}) if isinstance(spec.get("procedural_controls"), dict) else {}

    out: Dict[str, Any] = {
        "domain": "scene",
        "quality_mode": spec.get("quality_mode", "balanced"),
        "width_m": dims.get("width"),
        "depth_m": dims.get("depth"),
        "height_m": dims.get("height"),
        "style_tags": composition.get("style_tags", []),
        "parts": composition.get("parts", []),
        "selected_assets": composition.get("selected_assets", []),
        "poly_budget": constraints.get("poly_budget"),
        "min_objects": constraints.get("min_objects"),
        "key_light_energy": render_tuning.get("key_light_energy"),
        "fill_light_energy": render_tuning.get("fill_light_energy"),
        "camera_distance_scale": render_tuning.get("camera_distance_scale"),
        "exposure": render_tuning.get("exposure"),
        "saturation": render_tuning.get("saturation"),
        "value_scale": render_tuning.get("value_scale"),
        "color_balance": render_tuning.get("color_balance"),
        "feature_density": controls.get("feature_density"),
        "feature_variation": controls.get("feature_variation"),
        "emissive_strength": controls.get("emissive_strength"),
        "reference_images": spec.get("reference_images", []),
        "reference_analysis": spec.get("reference_analysis", {}),
    }
    return out
