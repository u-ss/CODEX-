"""
validate_universal_asset.py - Blender汎用生成結果の検証

Blender上で .blend を開いた状態で実行し、
寸法/トポロジ/予算/アセット取込結果を JSON として出力する。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import bpy
from mathutils import Vector

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from design_review import evaluate_design_review, load_rubrics

DEFAULT_SCORE_THRESHOLD = 82.0


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-json", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--render-dir", default="")
    parser.add_argument("--render-prefix", default="")
    parser.add_argument("--rubrics-dir", default="")
    parser.add_argument("--score-threshold", type=float, default=DEFAULT_SCORE_THRESHOLD)
    return parser.parse_args(argv)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _scene_metrics() -> Dict[str, Any]:
    raw = bpy.context.scene.get("ag_universal_actual_json")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
    bbox_min = Vector((10**9, 10**9, 10**9))
    bbox_max = Vector((-10**9, -10**9, -10**9))
    poly_count = 0
    zero_face_meshes: List[str] = []

    for obj in meshes:
        poly_count += int(len(obj.data.polygons) if obj.data else 0)
        if obj.data and any(poly.area <= 1.0e-10 for poly in obj.data.polygons):
            zero_face_meshes.append(obj.name)
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            bbox_min.x = min(bbox_min.x, world.x)
            bbox_min.y = min(bbox_min.y, world.y)
            bbox_min.z = min(bbox_min.z, world.z)
            bbox_max.x = max(bbox_max.x, world.x)
            bbox_max.y = max(bbox_max.y, world.y)
            bbox_max.z = max(bbox_max.z, world.z)

    if meshes:
        width = float(max(0.0, bbox_max.x - bbox_min.x))
        depth = float(max(0.0, bbox_max.y - bbox_min.y))
        height = float(max(0.0, bbox_max.z - bbox_min.z))
    else:
        width = depth = height = 0.0

    return {
        "object_count": len(meshes),
        "poly_count": poly_count,
        "bbox_width_m": width,
        "bbox_depth_m": depth,
        "bbox_height_m": height,
        "zero_face_meshes": zero_face_meshes,
        "asset_details": [],
    }


def _add_dimension_check(items: List[Dict[str, Any]], name: str, expected: float, actual: float, tolerance: float, severity: str) -> bool:
    diff = abs(float(expected) - float(actual))
    ok = diff <= float(tolerance)
    items.append(
        {
            "name": name,
            "expected": float(expected),
            "actual": float(actual),
            "tolerance": float(tolerance),
            "pass": ok,
            "severity": severity,
        }
    )
    return ok


def main() -> None:
    args = parse_args()
    spec = _read_json(Path(args.spec_json))
    metrics = _scene_metrics()
    score_threshold = max(0.0, min(100.0, float(args.score_threshold)))

    dims = spec.get("dimensions_m", {}) if isinstance(spec.get("dimensions_m"), dict) else {}
    expected_w = float(dims.get("width", 1.0) or 1.0)
    expected_d = float(dims.get("depth", 1.0) or 1.0)
    expected_h = float(dims.get("height", 1.0) or 1.0)

    actual_w = float(metrics.get("bbox_width_m", 0.0) or 0.0)
    actual_d = float(metrics.get("bbox_depth_m", 0.0) or 0.0)
    actual_h = float(metrics.get("bbox_height_m", 0.0) or 0.0)

    tol_w = max(0.2, expected_w * 0.35)
    tol_d = max(0.2, expected_d * 0.35)
    tol_h = max(0.2, expected_h * 0.35)

    dimension_checks: List[Dict[str, Any]] = []
    _add_dimension_check(dimension_checks, "bbox_width_m", expected_w, actual_w, tol_w, "major")
    _add_dimension_check(dimension_checks, "bbox_depth_m", expected_d, actual_d, tol_d, "major")
    _add_dimension_check(dimension_checks, "bbox_height_m", expected_h, actual_h, tol_h, "major")

    topology_checks: List[Dict[str, Any]] = []
    object_count = int(metrics.get("object_count", 0) or 0)
    zero_faces = metrics.get("zero_face_meshes", []) if isinstance(metrics.get("zero_face_meshes"), list) else []

    min_objects = int(spec.get("target_constraints", {}).get("min_objects", 1) or 1)
    topology_checks.append(
        {
            "name": "mesh_object_count",
            "pass": object_count >= min_objects,
            "detail": f"actual={object_count}, required={min_objects}",
            "severity": "critical",
        }
    )
    topology_checks.append(
        {
            "name": "zero_area_faces",
            "pass": len(zero_faces) == 0,
            "detail": "ok" if not zero_faces else ",".join(zero_faces[:10]),
            "severity": "major",
        }
    )

    budget_checks: List[Dict[str, Any]] = []
    poly_budget = int(spec.get("target_constraints", {}).get("poly_budget", 100000) or 100000)
    poly_count = int(metrics.get("poly_count", 0) or 0)
    budget_checks.append(
        {
            "name": "poly_budget",
            "budget": float(poly_budget),
            "actual": float(poly_count),
            "pass": poly_count <= poly_budget,
            "severity": "major",
        }
    )

    asset_checks: List[Dict[str, Any]] = []
    asset_details = metrics.get("asset_details", []) if isinstance(metrics.get("asset_details"), list) else []
    for item in asset_details:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "unknown"))
        detail = str(item.get("detail", ""))
        asset_checks.append({"id": str(item.get("id", "")), "pass": status == "ok", "detail": f"{status}: {detail}"})

    repair_actions: List[Dict[str, Any]] = []
    notes: List[str] = []
    critical_failures: List[str] = []

    if object_count <= 0:
        critical_failures.append("no mesh objects generated")
        repair_actions.append({"action": "set_value", "key": "target_constraints.min_objects", "value": 1, "reason": "ensure minimum mesh count"})

    if not budget_checks[0]["pass"]:
        repair_actions.append(
            {
                "action": "set_value",
                "key": "target_constraints.poly_budget",
                "value": int(poly_count * 1.15) + 100,
                "reason": "poly budget relaxed to observed footprint",
            }
        )

    dim_fail = [c for c in dimension_checks if not c["pass"]]
    for check in dim_fail:
        name = str(check["name"])
        expected = float(check["expected"])
        actual = float(check["actual"])
        if actual > 0.001 and expected > 0.001:
            factor = actual / expected
            if name == "bbox_width_m":
                repair_actions.append({"action": "scale_value", "key": "dimensions_m.width", "factor": factor, "reason": "width mismatch"})
            elif name == "bbox_depth_m":
                repair_actions.append({"action": "scale_value", "key": "dimensions_m.depth", "factor": factor, "reason": "depth mismatch"})
            elif name == "bbox_height_m":
                repair_actions.append({"action": "scale_value", "key": "dimensions_m.height", "factor": factor, "reason": "height mismatch"})
        else:
            notes.append(f"{name} は actual が小さすぎるため比率補正をスキップ")

    for item in asset_checks:
        if not item.get("pass"):
            repair_actions.append(
                {
                    "action": "remove_selected_asset",
                    "key": "composition.selected_assets",
                    "value": item.get("id", ""),
                    "reason": f"asset import failed: {item.get('detail', '')}",
                }
            )

    output_path = Path(args.output_json).resolve()
    render_dir = Path(args.render_dir).resolve() if args.render_dir else output_path.parent
    render_prefix = args.render_prefix.strip()
    if not render_prefix:
        stem = output_path.stem
        if stem.startswith("validation_"):
            render_prefix = stem[len("validation_") :]
    if not render_prefix:
        render_prefix = "iter_00"

    render_paths = {
        "front": render_dir / f"{render_prefix}_front.png",
        "oblique": render_dir / f"{render_prefix}_oblique.png",
        "bird": render_dir / f"{render_prefix}_bird.png",
    }
    rubrics_dir = Path(args.rubrics_dir).resolve() if args.rubrics_dir else (ROOT_DIR / "design_rubrics")
    design_review = evaluate_design_review(
        spec=spec,
        scene_metrics=metrics,
        render_paths=render_paths,
        rubrics=load_rubrics(rubrics_dir),
    )
    design_checks = design_review.get("checks", []) if isinstance(design_review.get("checks"), list) else []
    design_score = float(design_review.get("score", 100.0) or 100.0)
    design_pass = bool(design_review.get("pass", True))
    design_threshold = float(design_review.get("threshold", 70.0) or 70.0)
    design_repair = design_review.get("repair_actions", []) if isinstance(design_review.get("repair_actions"), list) else []
    repair_actions.extend(design_repair)

    # deduplicate repair actions by (action,key,value)
    dedup: List[Dict[str, Any]] = []
    seen = set()
    for action in repair_actions:
        token = (action.get("action"), action.get("key"), str(action.get("value", "")))
        if token in seen:
            continue
        seen.add(token)
        dedup.append(action)
    repair_actions = dedup

    core_score = 100.0
    for check in dimension_checks:
        if not check["pass"]:
            core_score -= 10.0
    for check in topology_checks:
        if not check["pass"]:
            core_score -= 16.0 if check["severity"] == "critical" else 8.0
    for check in budget_checks:
        if not check["pass"]:
            core_score -= 12.0
    core_score -= min(20.0, float(len(repair_actions)) * 3.0)
    core_score = max(0.0, min(100.0, core_score))

    score = (core_score * 0.65) + (design_score * 0.35)
    score = max(0.0, min(100.0, score))

    critical_fail = any((not check["pass"]) and check["severity"] == "critical" for check in topology_checks)
    passed = (not critical_fail) and bool(design_pass) and score >= score_threshold

    result = {
        "dimension_checks": dimension_checks,
        "topology_checks": topology_checks,
        "budget_checks": budget_checks,
        "asset_checks": asset_checks,
        "design_checks": design_checks,
        "design_score": round(design_score, 2),
        "design_pass": design_pass,
        "design_threshold": round(design_threshold, 2),
        "applied_design_rubrics": design_review.get("applied_rubrics", []),
        "score": round(score, 2),
        "score_threshold": round(score_threshold, 2),
        "pass": passed,
        "repair_actions": repair_actions,
        "notes": notes + list(design_review.get("notes", [])),
        "critical_failures": critical_failures,
    }

    out_path = output_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[AG] validation written: {out_path}")
    print(f"[AG] score={result['score']} pass={result['pass']}")


if __name__ == "__main__":
    main()
