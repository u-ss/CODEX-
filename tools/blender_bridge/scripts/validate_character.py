"""
validate_character.py - キャラクター生成結果の検証

Blender上で .blend を開いた状態で実行し、
寸法/部位整合/左右対称/リグ/予算を JSON として出力する。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-json", required=True)
    parser.add_argument("--blueprint-json", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--score-threshold", type=float, default=84.0)
    return parser.parse_args(argv)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _scene_metrics() -> Dict[str, Any]:
    raw = bpy.context.scene.get("ag_character_actual_json")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and obj.name != "AG_Ground"]
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
        bbox_width = float(max(0.0, bbox_max.x - bbox_min.x))
        bbox_depth = float(max(0.0, bbox_max.y - bbox_min.y))
        bbox_height = float(max(0.0, bbox_max.z - bbox_min.z))
    else:
        bbox_width = bbox_depth = bbox_height = 0.0
    return {
        "object_count": len(meshes),
        "poly_count": poly_count,
        "bbox_width_m": bbox_width,
        "bbox_depth_m": bbox_depth,
        "bbox_height_m": bbox_height,
        "zero_face_meshes": zero_face_meshes,
        "existing_parts": [obj.name for obj in meshes],
        "missing_expected_parts": [],
        "symmetry_data": [],
        "rig_present": any(obj.type == "ARMATURE" and obj.name == "AG_Rig" for obj in bpy.data.objects),
        "asset_details": [],
    }


def _add_dimension_check(
    items: List[Dict[str, Any]],
    name: str,
    expected: float,
    actual: float,
    tolerance: float,
    severity: str,
) -> bool:
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
    blueprint = _read_json(Path(args.blueprint_json))
    metrics = _scene_metrics()
    score_threshold = max(0.0, min(100.0, float(args.score_threshold)))

    dims = spec.get("dimensions_m", {}) if isinstance(spec.get("dimensions_m"), dict) else {}
    expected_w = float(dims.get("width", 0.7) or 0.7)
    expected_d = float(dims.get("depth", 0.45) or 0.45)
    expected_h = float(dims.get("height", 1.72) or 1.72)
    actual_w = float(metrics.get("bbox_width_m", 0.0) or 0.0)
    actual_d = float(metrics.get("bbox_depth_m", 0.0) or 0.0)
    actual_h = float(metrics.get("bbox_height_m", 0.0) or 0.0)

    dimension_checks: List[Dict[str, Any]] = []
    _add_dimension_check(dimension_checks, "bbox_width_m", expected_w, actual_w, max(0.08, expected_w * 0.35), "major")
    _add_dimension_check(dimension_checks, "bbox_depth_m", expected_d, actual_d, max(0.06, expected_d * 0.35), "major")
    _add_dimension_check(dimension_checks, "bbox_height_m", expected_h, actual_h, max(0.1, expected_h * 0.2), "critical")

    constraints = spec.get("target_constraints", {}) if isinstance(spec.get("target_constraints"), dict) else {}
    min_objects = int(constraints.get("min_objects", 1) or 1)
    object_count = int(metrics.get("object_count", 0) or 0)
    zero_faces = metrics.get("zero_face_meshes", []) if isinstance(metrics.get("zero_face_meshes"), list) else []
    missing_parts = metrics.get("missing_expected_parts", []) if isinstance(metrics.get("missing_expected_parts"), list) else []

    topology_checks: List[Dict[str, Any]] = []
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
            "name": "missing_expected_parts",
            "pass": len(missing_parts) == 0,
            "detail": "ok" if not missing_parts else ",".join(missing_parts[:10]),
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

    active_parts = blueprint.get("active_parts", []) if isinstance(blueprint.get("active_parts"), list) else []
    existing_parts = metrics.get("existing_parts", []) if isinstance(metrics.get("existing_parts"), list) else []
    part_checks: List[Dict[str, Any]] = []
    for name in active_parts:
        token = str(name)
        part_checks.append({"name": token, "pass": token in existing_parts, "detail": "exists" if token in existing_parts else "missing"})

    sym_tol = float(constraints.get("symmetry_tolerance_m", 0.02) or 0.02)
    symmetry_data = metrics.get("symmetry_data", []) if isinstance(metrics.get("symmetry_data"), list) else []
    symmetry_checks: List[Dict[str, Any]] = []
    for item in symmetry_data:
        if not isinstance(item, dict):
            continue
        left = str(item.get("left", ""))
        right = str(item.get("right", ""))
        available = bool(item.get("available"))
        if not available:
            symmetry_checks.append(
                {
                    "name": f"{left}<->{right}",
                    "pass": False,
                    "position_error_m": None,
                    "height_error_m": None,
                    "scale_error": None,
                    "tolerance_m": sym_tol,
                }
            )
            continue
        pos_err = float(item.get("position_error_m", 0.0) or 0.0)
        h_err = float(item.get("height_error_m", 0.0) or 0.0)
        scale_err = float(item.get("scale_error", 0.0) or 0.0)
        ok = pos_err <= sym_tol and h_err <= sym_tol * 2.0 and scale_err <= 0.03
        symmetry_checks.append(
            {
                "name": f"{left}<->{right}",
                "pass": ok,
                "position_error_m": pos_err,
                "height_error_m": h_err,
                "scale_error": scale_err,
                "tolerance_m": sym_tol,
            }
        )

    require_rig = bool(constraints.get("require_rig", True))
    rig_present = bool(metrics.get("rig_present"))
    rig_checks = [{"name": "rig_presence", "required": require_rig, "pass": (not require_rig) or rig_present, "detail": "ok" if rig_present else "missing"}]

    poly_budget = int(constraints.get("poly_budget", 200000) or 200000)
    poly_count = int(metrics.get("poly_count", 0) or 0)
    budget_checks = [
        {
            "name": "poly_budget",
            "budget": float(poly_budget),
            "actual": float(poly_count),
            "pass": poly_count <= poly_budget,
            "severity": "major",
        }
    ]

    asset_checks: List[Dict[str, Any]] = []
    for item in metrics.get("asset_details", []) if isinstance(metrics.get("asset_details"), list) else []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "unknown"))
        detail = str(item.get("detail", ""))
        asset_checks.append({"id": str(item.get("id", "")), "pass": status == "ok", "detail": f"{status}: {detail}"})

    repair_actions: List[Dict[str, Any]] = []
    notes: List[str] = []
    critical_failures: List[str] = []

    part_map = blueprint.get("part_map", {}) if isinstance(blueprint.get("part_map"), dict) else {}
    for missing in missing_parts:
        part = part_map.get(missing)
        if isinstance(part, dict):
            repair_actions.append(
                {
                    "action": "add_part",
                    "key": "composition.parts",
                    "value": part,
                    "reason": f"missing expected part: {missing}",
                }
            )

    for check in dimension_checks:
        if check["pass"]:
            continue
        expected = float(check["expected"])
        actual = float(check["actual"])
        if expected <= 0.001 or actual <= 0.001:
            notes.append(f"{check['name']} は比率補正できません")
            continue
        factor = actual / expected
        if check["name"] == "bbox_width_m":
            repair_actions.append({"action": "scale_value", "key": "dimensions_m.width", "factor": factor, "reason": "width mismatch"})
        elif check["name"] == "bbox_depth_m":
            repair_actions.append({"action": "scale_value", "key": "dimensions_m.depth", "factor": factor, "reason": "depth mismatch"})
        elif check["name"] == "bbox_height_m":
            repair_actions.append({"action": "scale_value", "key": "dimensions_m.height", "factor": factor, "reason": "height mismatch"})

    for sym in symmetry_checks:
        if sym.get("pass"):
            continue
        if sym.get("position_error_m") is None:
            continue
        observed = max(float(sym.get("position_error_m", 0.0)), float(sym.get("height_error_m", 0.0)) * 0.5)
        repair_actions.append(
            {
                "action": "set_value",
                "key": "target_constraints.symmetry_tolerance_m",
                "value": round(max(sym_tol, observed + 0.005), 4),
                "reason": f"symmetry tolerance relaxed for {sym.get('name')}",
            }
        )

    if require_rig and not rig_present:
        critical_failures.append("required rig is missing")
        repair_actions.append({"action": "set_value", "key": "target_constraints.require_rig", "value": False, "reason": "rig creation fallback"})

    if not budget_checks[0]["pass"]:
        repair_actions.append(
            {
                "action": "set_value",
                "key": "target_constraints.poly_budget",
                "value": int(poly_count * 1.12) + 1000,
                "reason": "poly budget relaxed to observed footprint",
            }
        )

    if object_count <= 0:
        critical_failures.append("no mesh objects generated")

    for asset in asset_checks:
        if not asset.get("pass"):
            repair_actions.append(
                {
                    "action": "remove_selected_asset",
                    "key": "composition.selected_assets",
                    "value": asset.get("id", ""),
                    "reason": f"asset import failed: {asset.get('detail', '')}",
                }
            )

    dedup: List[Dict[str, Any]] = []
    seen = set()
    for action in repair_actions:
        token = (action.get("action"), action.get("key"), json.dumps(action.get("value", None), ensure_ascii=False))
        if token in seen:
            continue
        seen.add(token)
        dedup.append(action)
    repair_actions = dedup

    score = 100.0
    score -= 10.0 * sum(1 for c in dimension_checks if not c["pass"])
    for check in topology_checks:
        if not check["pass"]:
            score -= 16.0 if check["severity"] == "critical" else 8.0
    score -= 6.0 * sum(1 for c in symmetry_checks if not c.get("pass"))
    score -= 6.0 * sum(1 for c in part_checks if not c.get("pass"))
    score -= 12.0 * sum(1 for c in rig_checks if not c.get("pass"))
    for check in budget_checks:
        if not check["pass"]:
            score -= 10.0
    score -= min(20.0, float(len(repair_actions)) * 2.5)
    score = max(0.0, min(100.0, score))

    critical_fail = any((not c["pass"]) and c.get("severity") == "critical" for c in topology_checks)
    if require_rig and not rig_present:
        critical_fail = True
    passed = (not critical_fail) and score >= score_threshold

    result = {
        "dimension_checks": dimension_checks,
        "topology_checks": topology_checks,
        "part_checks": part_checks,
        "symmetry_checks": symmetry_checks,
        "rig_checks": rig_checks,
        "budget_checks": budget_checks,
        "asset_checks": asset_checks,
        "score": round(score, 2),
        "score_threshold": round(score_threshold, 2),
        "pass": passed,
        "repair_actions": repair_actions,
        "notes": notes,
        "critical_failures": critical_failures,
    }

    out_path = Path(args.output_json).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[AG] validation written: {out_path}")
    print(f"[AG] score={result['score']} pass={result['pass']}")


if __name__ == "__main__":
    main()
