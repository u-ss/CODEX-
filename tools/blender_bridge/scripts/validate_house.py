"""
validate_house.py - 戸建て生成結果の検証

Blender上で .blend を開いた状態で実行し、寸法/トポロジの検証結果を JSON 出力する。
"""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--spec-json", required=True)
    p.add_argument("--output-json", required=True)
    p.add_argument("--score-threshold", type=float, default=85.0)
    return p.parse_args(argv)


def load_scene_metrics():
    raw = bpy.context.scene.get("ag_house_actual_json")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def object_world_z_max(obj) -> float:
    return max((obj.matrix_world @ Vector(corner)).z for corner in obj.bound_box)


def add_dimension_check(items, name, expected, actual, tolerance, severity="major"):
    diff = abs(float(expected) - float(actual))
    ok = diff <= tolerance
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


def main():
    args = parse_args()
    spec = json.loads(Path(args.spec_json).read_text(encoding="utf-8"))
    metrics = load_scene_metrics()
    score_threshold = max(0.0, min(100.0, float(args.score_threshold)))

    floor_heights = [float(x) for x in spec.get("floor_heights_m", [2.6, 2.4])]
    floors = int(spec.get("floors", len(floor_heights) if floor_heights else 2))
    slab_t = float(spec.get("floor_slab_t_m", 0.25))
    foundation_h = float(spec.get("foundation_h_m", 0.4))
    wall_total_expected = sum(floor_heights[:floors]) + max(0, floors - 1) * slab_t
    roof_pitch = float(spec.get("roof_pitch", 0.4))
    roof_type = str(spec.get("roof_type", "gable")).lower()
    if roof_type == "shed":
        roof_ridge_h = float(spec.get("footprint_w_m", 8.0)) * roof_pitch
    else:
        roof_ridge_h = float(spec.get("footprint_w_m", 8.0)) * 0.5 * roof_pitch
    roof_peak_expected = foundation_h + wall_total_expected + roof_ridge_h

    foundation_obj = bpy.data.objects.get("Foundation")
    walls_obj = bpy.data.objects.get("Walls")
    roof_obj = bpy.data.objects.get("Roof")
    door_obj = bpy.data.objects.get("Door")

    actual_w = metrics.get("footprint_w_m")
    actual_d = metrics.get("footprint_d_m")
    actual_wall_total = metrics.get("wall_total_h_m")
    actual_peak = metrics.get("roof_peak_z_m")
    actual_door_h = metrics.get("door_height_m")

    if actual_w is None and foundation_obj:
        actual_w = float(foundation_obj.dimensions.x)
    if actual_d is None and foundation_obj:
        actual_d = float(foundation_obj.dimensions.y)
    if actual_wall_total is None and walls_obj:
        actual_wall_total = float(walls_obj.dimensions.z)
    if actual_peak is None and roof_obj:
        actual_peak = float(object_world_z_max(roof_obj))
    if actual_door_h is None and door_obj:
        actual_door_h = float(door_obj.dimensions.z)

    dimension_checks = []
    add_dimension_check(dimension_checks, "footprint_w_m", spec.get("footprint_w_m", 8.0), actual_w or 0.0, 0.01, "critical")
    add_dimension_check(dimension_checks, "footprint_d_m", spec.get("footprint_d_m", 6.0), actual_d or 0.0, 0.01, "critical")
    add_dimension_check(dimension_checks, "wall_total_h_m", wall_total_expected, actual_wall_total or 0.0, 0.01, "critical")
    add_dimension_check(dimension_checks, "roof_peak_z_m", roof_peak_expected, actual_peak or 0.0, 0.03, "major")
    add_dimension_check(dimension_checks, "door_height_m", spec.get("door_height_m", 2.1), actual_door_h or 0.0, 0.03, "major")

    topology_checks = []
    critical_failures = []

    required_names = ("Foundation", "Walls", "Roof", "Door")
    for name in required_names:
        obj = bpy.data.objects.get(name)
        ok = obj is not None
        topology_checks.append(
            {"name": f"required_object:{name}", "pass": ok, "detail": "exists" if ok else "missing", "severity": "critical"}
        )
        if not ok:
            critical_failures.append(f"required object missing: {name}")

    mesh_zero_issues = []
    normal_issues = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        dims = obj.dimensions
        if min(float(dims.x), float(dims.y), float(dims.z)) <= 0.0005:
            mesh_zero_issues.append(obj.name)
        if obj.data and hasattr(obj.data, "polygons"):
            for poly in obj.data.polygons:
                if poly.area <= 1.0e-10:
                    normal_issues.append(obj.name)
                    break

    topology_checks.append(
        {
            "name": "mesh_non_zero_thickness",
            "pass": len(mesh_zero_issues) == 0,
            "detail": "ok" if not mesh_zero_issues else ",".join(mesh_zero_issues[:10]),
            "severity": "major",
        }
    )
    topology_checks.append(
        {
            "name": "mesh_polygon_area_valid",
            "pass": len(normal_issues) == 0,
            "detail": "ok" if not normal_issues else ",".join(normal_issues[:10]),
            "severity": "major",
        }
    )

    repair_actions = []
    notes = []

    # 仕様整合チェック（未整合なら deterministic repair を提案）
    if not (0.2 <= roof_pitch <= 0.7):
        repair_actions.append({"action": "clamp_range", "key": "roof_pitch", "min": 0.2, "max": 0.7, "reason": "roof pitch range"})

    wall_t = float(spec.get("wall_thickness_m", 0.18))
    if not (0.09 <= wall_t <= 0.30):
        repair_actions.append(
            {"action": "clamp_range", "key": "wall_thickness_m", "min": 0.09, "max": 0.30, "reason": "wall thickness range"}
        )

    eaves_m = float(spec.get("eaves_m", 0.45))
    if not (0.2 <= eaves_m <= 0.9):
        repair_actions.append({"action": "clamp_range", "key": "eaves_m", "min": 0.2, "max": 0.9, "reason": "eaves range"})

    door_h = float(spec.get("door_height_m", 2.1))
    min_floor = min(floor_heights) if floor_heights else 2.4
    if door_h > min_floor - 0.1:
        target = max(1.8, min_floor - 0.2)
        repair_actions.append({"action": "set_value", "key": "door_height_m", "value": round(target, 3), "reason": "door must fit floor height"})

    if metrics.get("roof_type_actual") and metrics.get("roof_type_actual") != metrics.get("roof_type_requested"):
        notes.append(
            f"roof_type '{metrics.get('roof_type_requested')}' は初期版で '{metrics.get('roof_type_actual')}' にフォールバック"
        )

    score = 100.0
    for c in dimension_checks:
        if not c["pass"]:
            score -= 12.0 if c["severity"] == "critical" else 8.0
    for c in topology_checks:
        if not c["pass"]:
            score -= 15.0 if c["severity"] == "critical" else 7.0
    score -= min(15.0, float(len(repair_actions)) * 3.0)
    score = max(0.0, min(100.0, score))

    critical_dim_fail = any((not c["pass"]) and c["severity"] == "critical" for c in dimension_checks)
    critical_topology_fail = any((not c["pass"]) and c["severity"] == "critical" for c in topology_checks)
    passed = (not critical_dim_fail) and (not critical_topology_fail) and score >= score_threshold and len(repair_actions) == 0

    result = {
        "dimension_checks": dimension_checks,
        "topology_checks": topology_checks,
        "score": round(score, 2),
        "score_threshold": round(score_threshold, 2),
        "pass": passed,
        "repair_actions": repair_actions,
        "notes": notes,
        "critical_failures": critical_failures,
    }

    out = Path(args.output_json).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[AG] validation written: {out}")
    print(f"[AG] score={result['score']} pass={result['pass']}")


if __name__ == "__main__":
    main()
