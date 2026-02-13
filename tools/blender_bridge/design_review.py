"""
design_review.py - レンダ画像ベースのデザイン品質レビュー

特徴:
- 共通ルーブリック + 対象別ルーブリックを合算評価
- 追加依存なし（Blenderの画像読み込み機能を利用）
- 自動修正向け repair_actions を返す
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bpy

_ALLOWED_METRIC_SPACES = {"srgb", "linear"}
_REPAIR_VALUE_LIMITS: Dict[str, Tuple[float, float, float]] = {
    # key: (min, max, max_abs_step_per_iteration)
    "render_tuning.exposure": (-5.0, 5.0, 0.12),
    "render_tuning.key_light_energy": (0.1, 20000.0, 2.0),
    "render_tuning.fill_light_energy": (0.1, 20000.0, 120.0),
    "render_tuning.camera_distance_scale": (0.35, 3.2, 0.1),
    "render_tuning.saturation": (0.2, 2.5, 0.06),
    "procedural_controls.emissive_strength": (0.0, 8.0, 0.15),
    "procedural_controls.feature_density": (0.2, 5.0, 0.15),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _normalize_metric_space(value: Any, default: str = "srgb") -> str:
    raw = str(value or "").strip().lower()
    if raw in _ALLOWED_METRIC_SPACES:
        return raw
    return default


def _linear_to_srgb_channel(value: float) -> float:
    v = _clamp(float(value), 0.0, 1.0)
    if v <= 0.0031308:
        return 12.92 * v
    return 1.055 * (v ** (1.0 / 2.4)) - 0.055


def _init_color_acc() -> Dict[str, float]:
    return {
        "lum_sum": 0.0,
        "lum_sq_sum": 0.0,
        "sat_sum": 0.0,
        "r_sum": 0.0,
        "g_sum": 0.0,
        "b_sum": 0.0,
        "bright_count": 0.0,
        "dark_count": 0.0,
    }


def _accumulate_color_metrics(acc: Dict[str, float], r: float, g: float, b: float) -> None:
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    sat = max(r, g, b) - min(r, g, b)

    acc["lum_sum"] += lum
    acc["lum_sq_sum"] += lum * lum
    acc["sat_sum"] += sat
    acc["r_sum"] += r
    acc["g_sum"] += g
    acc["b_sum"] += b
    acc["bright_count"] += 1.0 if lum >= 0.82 else 0.0
    acc["dark_count"] += 1.0 if lum <= 0.12 else 0.0


def _finalize_color_metrics(acc: Dict[str, float], sample_count: int) -> Dict[str, float]:
    n = max(1, int(sample_count))
    lum_mean = float(acc["lum_sum"] / n)
    variance = max(0.0, float(acc["lum_sq_sum"] / n) - lum_mean * lum_mean)
    lum_std = variance ** 0.5

    mean_r = float(acc["r_sum"] / n)
    mean_g = float(acc["g_sum"] / n)
    mean_b = float(acc["b_sum"] / n)
    cool_ratio = mean_b / max(1.0e-6, mean_r + mean_g + mean_b)

    return {
        "luminance_mean": lum_mean,
        "luminance_std": float(lum_std),
        "saturation_mean": float(acc["sat_sum"] / n),
        "bright_ratio": float(acc["bright_count"] / n),
        "dark_ratio": float(acc["dark_count"] / n),
        "mean_r": mean_r,
        "mean_g": mean_g,
        "mean_b": mean_b,
        "cool_ratio": float(cool_ratio),
    }


def _nested_get(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return default
        current = current[segment]
    return current


def _bounded_repair_value(spec: Dict[str, Any], key: str, proposed: float) -> float:
    limits = _REPAIR_VALUE_LIMITS.get(key)
    if not limits:
        return float(proposed)
    lo, hi, max_step = limits
    current_raw = _nested_get(spec, key, None)
    current: Optional[float] = None
    if current_raw is not None:
        try:
            current = float(current_raw)
        except Exception:
            current = None

    bounded = float(proposed)
    if current is not None:
        delta = bounded - current
        if delta > max_step:
            bounded = current + max_step
        elif delta < -max_step:
            bounded = current - max_step
    return _clamp(bounded, lo, hi)


def _append_bounded_set_action(
    actions: List[Dict[str, Any]],
    spec: Dict[str, Any],
    key: str,
    proposed: float,
    reason: str,
) -> None:
    bounded = _bounded_repair_value(spec, key, proposed)
    actions.append(
        {
            "action": "set_value",
            "key": key,
            "value": round(float(bounded), 3),
            "reason": reason,
        }
    )


def _load_image_metrics(path: Path, sample_cap: int = 45000, metric_space: str = "srgb") -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {"exists": False, "path": str(path)}

    metric_space = _normalize_metric_space(metric_space, default="srgb")

    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width = int(image.size[0])
        height = int(image.size[1])
        pixels = list(image.pixels[:])
    finally:
        bpy.data.images.remove(image, do_unlink=True)

    total_pixels = max(1, width * height)
    stride = max(1, total_pixels // max(1, int(sample_cap)))

    n = 0
    linear_acc = _init_color_acc()
    srgb_acc = _init_color_acc()

    for px_idx in range(0, total_pixels, stride):
        base = px_idx * 4
        if base + 3 >= len(pixels):
            break
        r_lin = _clamp(float(pixels[base]), 0.0, 1.0)
        g_lin = _clamp(float(pixels[base + 1]), 0.0, 1.0)
        b_lin = _clamp(float(pixels[base + 2]), 0.0, 1.0)

        _accumulate_color_metrics(linear_acc, r_lin, g_lin, b_lin)

        r_srgb = _linear_to_srgb_channel(r_lin)
        g_srgb = _linear_to_srgb_channel(g_lin)
        b_srgb = _linear_to_srgb_channel(b_lin)
        _accumulate_color_metrics(srgb_acc, r_srgb, g_srgb, b_srgb)

        n += 1

    spaces = {
        "linear": _finalize_color_metrics(linear_acc, n),
        "srgb": _finalize_color_metrics(srgb_acc, n),
    }
    selected = spaces.get(metric_space) or spaces["srgb"]

    return {
        "exists": True,
        "path": str(path),
        "width": width,
        "height": height,
        "sample_count": max(1, int(n)),
        "metric_space": metric_space,
        "spaces": spaces,
        "luminance_mean": float(selected["luminance_mean"]),
        "luminance_std": float(selected["luminance_std"]),
        "saturation_mean": float(selected["saturation_mean"]),
        "bright_ratio": float(selected["bright_ratio"]),
        "dark_ratio": float(selected["dark_ratio"]),
        "mean_r": float(selected["mean_r"]),
        "mean_g": float(selected["mean_g"]),
        "mean_b": float(selected["mean_b"]),
        "cool_ratio": float(selected["cool_ratio"]),
    }


def _view_space_payload(view_payload: Dict[str, Any], metric_space: str) -> Dict[str, Any]:
    spaces = view_payload.get("spaces")
    if isinstance(spaces, dict):
        selected = spaces.get(metric_space)
        if isinstance(selected, dict):
            return selected
        fallback_srgb = spaces.get("srgb")
        if isinstance(fallback_srgb, dict):
            return fallback_srgb
        fallback_linear = spaces.get("linear")
        if isinstance(fallback_linear, dict):
            return fallback_linear
    return view_payload


def _pairwise_color_diversity(view_metrics: Dict[str, Dict[str, Any]], metric_space: str = "srgb") -> float:
    keys = [k for k, v in view_metrics.items() if v.get("exists")]
    if len(keys) < 2:
        return 0.0

    distances: List[float] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a = _view_space_payload(view_metrics[keys[i]], metric_space)
            b = _view_space_payload(view_metrics[keys[j]], metric_space)
            dr = float(a.get("mean_r", 0.0)) - float(b.get("mean_r", 0.0))
            dg = float(a.get("mean_g", 0.0)) - float(b.get("mean_g", 0.0))
            db = float(a.get("mean_b", 0.0)) - float(b.get("mean_b", 0.0))
            distances.append((dr * dr + dg * dg + db * db) ** 0.5)
    return float(sum(distances) / max(1, len(distances)))


@dataclass
class DesignRubric:
    rubric_id: str
    target: Dict[str, Any]
    score_threshold: float
    metric_space: str
    checks: List[Dict[str, Any]]


def _read_rubric(path: Path) -> Optional[DesignRubric]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("checks"), list):
        return None

    target = payload.get("target", {}) if isinstance(payload.get("target"), dict) else {}
    return DesignRubric(
        rubric_id=str(payload.get("id", path.stem)),
        target=target,
        score_threshold=float(_safe_float(payload.get("score_threshold"), 70.0)),
        metric_space=_normalize_metric_space(payload.get("metric_space"), default="srgb"),
        checks=payload.get("checks", []),
    )


def load_rubrics(base_dir: Path) -> List[DesignRubric]:
    rubrics: List[DesignRubric] = []
    if not base_dir.exists() or not base_dir.is_dir():
        return rubrics
    for path in sorted(base_dir.glob("*.json")):
        rubric = _read_rubric(path)
        if rubric is not None:
            rubrics.append(rubric)
    return rubrics


def _rubric_matches(rubric: DesignRubric, spec: Dict[str, Any]) -> bool:
    domain = str(spec.get("domain", "")).lower()
    prompt = str(spec.get("source_prompt", "")).lower()
    style_tags = []
    composition = spec.get("composition")
    if isinstance(composition, dict):
        raw_tags = composition.get("style_tags")
        if isinstance(raw_tags, list):
            style_tags = [str(v).lower() for v in raw_tags]

    target_domain = str(rubric.target.get("domain", "*")).lower()
    if target_domain not in ("*", "", domain):
        return False

    keywords = rubric.target.get("keywords", [])
    if isinstance(keywords, list) and keywords:
        if not any(str(k).lower() in prompt for k in keywords):
            return False

    require_tags = rubric.target.get("style_tags", [])
    if isinstance(require_tags, list) and require_tags:
        lower_tags = {tag.lower() for tag in style_tags}
        if not any(str(tag).lower() in lower_tags for tag in require_tags):
            return False

    return True


def _metric_value(
    metric_name: str,
    scope: str,
    metric_space: str,
    view_metrics: Dict[str, Dict[str, Any]],
    scene_metrics: Dict[str, Any],
) -> Tuple[float, Dict[str, float]]:
    per_view_values: Dict[str, float] = {}

    if scope == "global":
        if metric_name == "view_diversity":
            return _pairwise_color_diversity(view_metrics, metric_space=metric_space), {}
        if metric_name == "bbox_height_over_depth":
            h = _safe_float(scene_metrics.get("bbox_height_m"), 0.0)
            d = _safe_float(scene_metrics.get("bbox_depth_m"), 0.0)
            return (h / d) if d > 1.0e-6 else 0.0, {}
        if metric_name == "object_count":
            return _safe_float(scene_metrics.get("object_count"), 0.0), {}
        if metric_name == "poly_count":
            return _safe_float(scene_metrics.get("poly_count"), 0.0), {}
        if metric_name == "bright_ratio":
            vals = [
                float(_view_space_payload(v, metric_space).get("bright_ratio", 0.0))
                for v in view_metrics.values()
                if v.get("exists")
            ]
            return (sum(vals) / len(vals)) if vals else 0.0, {}
        if metric_name == "dark_ratio":
            vals = [
                float(_view_space_payload(v, metric_space).get("dark_ratio", 0.0))
                for v in view_metrics.values()
                if v.get("exists")
            ]
            return (sum(vals) / len(vals)) if vals else 0.0, {}
        if metric_name == "cool_ratio":
            vals = [
                float(_view_space_payload(v, metric_space).get("cool_ratio", 0.0))
                for v in view_metrics.values()
                if v.get("exists")
            ]
            return (sum(vals) / len(vals)) if vals else 0.0, {}
        return 0.0, {}

    # each_view
    for view_name, payload in view_metrics.items():
        if not payload.get("exists"):
            continue
        scoped_payload = _view_space_payload(payload, metric_space)
        if metric_name in scoped_payload:
            per_view_values[view_name] = _safe_float(scoped_payload.get(metric_name), 0.0)
        else:
            per_view_values[view_name] = _safe_float(payload.get(metric_name), 0.0)
    if not per_view_values:
        return 0.0, {}
    mean_value = sum(per_view_values.values()) / max(1, len(per_view_values))
    return float(mean_value), per_view_values


def _evaluate_threshold(value: float, min_value: Optional[float], max_value: Optional[float]) -> bool:
    if min_value is not None and value < min_value:
        return False
    if max_value is not None and value > max_value:
        return False
    return True


def _repair_from_check(
    check: Dict[str, Any],
    observed: float,
    min_value: Optional[float],
    max_value: Optional[float],
    spec: Dict[str, Any],
) -> List[Dict[str, Any]]:
    check_id = str(check.get("id", ""))
    actions: List[Dict[str, Any]] = []
    rt = spec.get("render_tuning", {}) if isinstance(spec.get("render_tuning"), dict) else {}
    pc = spec.get("procedural_controls", {}) if isinstance(spec.get("procedural_controls"), dict) else {}

    exposure = _safe_float(rt.get("exposure"), 0.0)
    key_energy = _safe_float(rt.get("key_light_energy"), 4.0)
    fill_energy = _safe_float(rt.get("fill_light_energy"), 250.0)
    camera_scale = _safe_float(rt.get("camera_distance_scale"), 1.0)
    saturation = _safe_float(rt.get("saturation"), 1.0)
    emissive = _safe_float(pc.get("emissive_strength"), 0.0)
    density = _safe_float(pc.get("feature_density"), 1.0)

    if check_id == "mean_luminance":
        if min_value is not None and observed < min_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.exposure",
                proposed=(exposure + 0.15),
                reason="design: brighten scene",
            )
        elif max_value is not None and observed > max_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.exposure",
                proposed=(exposure - 0.15),
                reason="design: darken scene",
            )
    elif check_id == "contrast":
        if min_value is not None and observed < min_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.key_light_energy",
                proposed=(key_energy * 1.12),
                reason="design: increase key light for contrast",
            )
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.fill_light_energy",
                proposed=(fill_energy * 0.9),
                reason="design: reduce fill light for contrast",
            )
    elif check_id == "view_diversity":
        if min_value is not None and observed < min_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.camera_distance_scale",
                proposed=(camera_scale + 0.12),
                reason="design: increase viewpoint separation",
            )
    elif check_id == "saturation":
        if min_value is not None and observed < min_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.saturation",
                proposed=(saturation + 0.08),
                reason="design: raise saturation",
            )
        elif max_value is not None and observed > max_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.saturation",
                proposed=max(0.2, saturation - 0.08),
                reason="design: reduce saturation",
            )
    elif check_id == "cool_palette":
        if min_value is not None and observed < min_value:
            actions.append({"action": "set_value", "key": "render_tuning.color_balance", "value": "cool", "reason": "design: shift palette cooler"})
    elif check_id == "highlight_presence":
        if min_value is not None and observed < min_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="procedural_controls.emissive_strength",
                proposed=(emissive + 0.2),
                reason="design: increase highlights",
            )
        elif max_value is not None and observed > max_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="procedural_controls.emissive_strength",
                proposed=max(0.0, emissive - 0.2),
                reason="design: reduce blown highlights",
            )
    elif check_id == "shadow_presence":
        # dark_ratio が少なすぎる = 画がフラット/明るすぎる可能性が高い
        if min_value is not None and observed < min_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.fill_light_energy",
                proposed=(fill_energy * 0.85),
                reason="design: increase shadows (reduce fill light)",
            )
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.exposure",
                proposed=(exposure - 0.12),
                reason="design: increase shadows (lower exposure)",
            )
        elif max_value is not None and observed > max_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.fill_light_energy",
                proposed=(fill_energy * 1.08),
                reason="design: reduce crushed blacks (raise fill light)",
            )
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.exposure",
                proposed=(exposure + 0.12),
                reason="design: reduce crushed blacks (raise exposure)",
            )
    elif check_id == "feature_density":
        if min_value is not None and observed < min_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="procedural_controls.feature_density",
                proposed=(density + 0.2),
                reason="design: increase feature density",
            )
        elif max_value is not None and observed > max_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="procedural_controls.feature_density",
                proposed=max(0.2, density - 0.2),
                reason="design: reduce feature density",
            )
    elif check_id == "scene_depth":
        if min_value is not None and observed < min_value:
            _append_bounded_set_action(
                actions=actions,
                spec=spec,
                key="render_tuning.camera_distance_scale",
                proposed=(camera_scale + 0.1),
                reason="design: improve perceived depth",
            )

    return actions


def evaluate_design_review(
    spec: Dict[str, Any],
    scene_metrics: Dict[str, Any],
    render_paths: Dict[str, Path],
    rubrics: List[DesignRubric],
) -> Dict[str, Any]:
    default_metric_space = "srgb"
    view_metrics: Dict[str, Dict[str, Any]] = {}
    for key, path in render_paths.items():
        view_metrics[key] = _load_image_metrics(path, metric_space=default_metric_space)

    active_rubrics = [rubric for rubric in rubrics if _rubric_matches(rubric, spec)]
    if not active_rubrics:
        return {
            "applied_rubrics": [],
            "score": 100.0,
            "pass": True,
            "checks": [],
            "repair_actions": [],
            "notes": ["no applicable design rubric"],
            "view_metrics": view_metrics,
        }

    all_checks: List[Dict[str, Any]] = []
    repair_actions: List[Dict[str, Any]] = []
    notes: List[str] = []
    score = 100.0
    threshold = 70.0

    for rubric in active_rubrics:
        threshold = max(threshold, float(rubric.score_threshold))
        rubric_metric_space = _normalize_metric_space(rubric.metric_space, default="srgb")
        for check in rubric.checks:
            if not isinstance(check, dict):
                continue
            metric = str(check.get("metric", "")).strip()
            if not metric:
                continue
            scope = str(check.get("scope", "global"))
            requested_metric_space = check.get("metric_space", rubric_metric_space)
            check_metric_space = _normalize_metric_space(requested_metric_space, default=rubric_metric_space)
            if str(requested_metric_space).strip().lower() not in _ALLOWED_METRIC_SPACES:
                notes.append(
                    f"{rubric.rubric_id}:{check.get('id', metric)} metric_space '{requested_metric_space}' は未対応のため {check_metric_space} を使用"
                )
            min_value = _safe_float(check.get("min"), None) if "min" in check else None
            max_value = _safe_float(check.get("max"), None) if "max" in check else None
            weight = max(0.0, _safe_float(check.get("weight"), 6.0))
            check_id = str(check.get("id", metric))

            observed, per_view = _metric_value(
                metric_name=metric,
                scope=scope,
                metric_space=check_metric_space,
                view_metrics=view_metrics,
                scene_metrics=scene_metrics,
            )
            check_pass = _evaluate_threshold(observed, min_value=min_value, max_value=max_value)
            all_checks.append(
                {
                    "rubric": rubric.rubric_id,
                    "id": check_id,
                    "metric": metric,
                    "scope": scope,
                    "metric_space": check_metric_space,
                    "expected_min": min_value,
                    "expected_max": max_value,
                    "observed": observed,
                    "per_view": per_view,
                    "weight": weight,
                    "pass": check_pass,
                }
            )
            if not check_pass:
                score -= weight
                repair_actions.extend(_repair_from_check(check, observed, min_value=min_value, max_value=max_value, spec=spec))

    # dedup repair actions
    dedup: List[Dict[str, Any]] = []
    seen = set()
    for action in repair_actions:
        token = (action.get("action"), action.get("key"), json.dumps(action.get("value", None), ensure_ascii=False))
        if token in seen:
            continue
        seen.add(token)
        dedup.append(action)
    repair_actions = dedup

    score = _clamp(score, 0.0, 100.0)
    passed = score >= threshold
    return {
        "applied_rubrics": [r.rubric_id for r in active_rubrics],
        "score": round(score, 2),
        "threshold": round(float(threshold), 2),
        "pass": passed,
        "checks": all_checks,
        "repair_actions": repair_actions,
        "notes": notes,
        "view_metrics": view_metrics,
    }
