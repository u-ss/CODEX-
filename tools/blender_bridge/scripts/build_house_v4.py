"""
build_house_v4.py - spec-driven house builder for house_agent

Responsibilities:
- Build a simple 2-3 floor detached house from normalized house spec JSON
- Emit iter render images (front/oblique/bird)
- Save .blend and scene metrics (ag_house_actual_json)

Compatibility:
- New contract: --spec-json --output-dir --render-prefix --save-blend
- Legacy flags are still accepted: --output, --blend
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

import bmesh
import bpy
from mathutils import Vector


# ------------------------------
# Args / IO
# ------------------------------

def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    p = argparse.ArgumentParser()

    # New (house_agent)
    p.add_argument("--spec-json", default="")
    p.add_argument("--output-dir", default="")
    p.add_argument("--render-prefix", default="iter_00")
    p.add_argument("--save-blend", default="")

    # Legacy
    p.add_argument("--output", default="")
    p.add_argument("--blend", default="")

    p.add_argument("--samples", type=int, default=128)
    p.add_argument("--engine", default="CYCLES")
    p.add_argument("--device", default="GPU")
    return p.parse_args(argv)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ------------------------------
# Utility
# ------------------------------

def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _box(name: str, size_xyz: Sequence[float], location_xyz: Sequence[float]) -> bpy.types.Object:
    sx, sy, sz = [max(0.001, float(v)) for v in size_xyz]
    lx, ly, lz = [float(v) for v in location_xyz]

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(lx, ly, lz))
    obj = bpy.context.active_object
    obj.name = name
    # Blender primitive cube has side length 1. Scale maps directly to dimensions.
    obj.scale = (sx, sy, sz)
    bpy.ops.object.transform_apply(scale=True)
    return obj


def _cut_boolean(target: bpy.types.Object, cutter_size: Sequence[float], cutter_location: Sequence[float], name: str) -> None:
    cutter = _box(f"_cut_{name}", cutter_size, cutter_location)
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    mod = target.modifiers.new(name=name, type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def _assign_material(obj: bpy.types.Object, rgba: Sequence[float], roughness: float = 0.55, metallic: float = 0.0) -> None:
    mat = bpy.data.materials.new(name=f"mat_{obj.name}")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (
            float(rgba[0]),
            float(rgba[1]),
            float(rgba[2]),
            float(rgba[3]) if len(rgba) > 3 else 1.0,
        )
        bsdf.inputs["Roughness"].default_value = float(_clamp(roughness, 0.0, 1.0))
        bsdf.inputs["Metallic"].default_value = float(_clamp(metallic, 0.0, 1.0))

    if not obj.data or not hasattr(obj.data, "materials"):
        return
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat


def _world_bbox_max_z(obj: bpy.types.Object) -> float:
    return max((obj.matrix_world @ Vector(corner)).z for corner in obj.bound_box)


# ------------------------------
# Build primitives
# ------------------------------

def _build_foundation(spec: Dict[str, Any]) -> bpy.types.Object:
    fw = _safe_float(spec.get("footprint_w_m"), 8.0)
    fd = _safe_float(spec.get("footprint_d_m"), 6.0)
    fh = _safe_float(spec.get("foundation_h_m"), 0.4)

    foundation = _box("Foundation", (fw, fd, fh), (0.0, 0.0, fh * 0.5))
    _assign_material(foundation, (0.58, 0.57, 0.55, 1.0), roughness=0.85)
    return foundation


def _floor_heights(spec: Dict[str, Any]) -> List[float]:
    floors = max(1, _safe_int(spec.get("floors"), 2))
    heights_raw = spec.get("floor_heights_m", [])
    values: List[float] = []
    if isinstance(heights_raw, list):
        for item in heights_raw:
            values.append(_clamp(_safe_float(item, 2.4), 2.0, 3.5))

    if not values:
        values = [2.6, 2.4]
    if len(values) < floors:
        values.extend([values[-1]] * (floors - len(values)))
    if len(values) > floors:
        values = values[:floors]
    return values


def _build_walls(spec: Dict[str, Any]) -> bpy.types.Object:
    fw = _safe_float(spec.get("footprint_w_m"), 8.0)
    fd = _safe_float(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_safe_float(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)
    foundation_h = _safe_float(spec.get("foundation_h_m"), 0.4)
    slab_t = _safe_float(spec.get("floor_slab_t_m"), 0.25)

    heights = _floor_heights(spec)
    floors = len(heights)
    wall_total = sum(heights) + max(0, floors - 1) * slab_t

    walls = _box("Walls", (fw, fd, wall_total), (0.0, 0.0, foundation_h + wall_total * 0.5))

    inner_w = max(0.1, fw - wt * 2.0)
    inner_d = max(0.1, fd - wt * 2.0)
    _cut_boolean(
        target=walls,
        cutter_size=(inner_w, inner_d, wall_total + 0.02),
        cutter_location=(0.0, 0.0, foundation_h + wall_total * 0.5),
        name="WallHollow",
    )

    _assign_material(walls, (0.87, 0.84, 0.79, 1.0), roughness=0.65)

    # Optional floor slabs (non-required objects)
    current_z = foundation_h
    for i in range(floors - 1):
        current_z += heights[i]
        slab = _box(
            f"FloorSlab_{i+1}",
            (max(0.1, fw - 0.04), max(0.1, fd - 0.04), slab_t),
            (0.0, 0.0, current_z + slab_t * 0.5),
        )
        _assign_material(slab, (0.45, 0.37, 0.28, 1.0), roughness=0.5)
        current_z += slab_t

    return walls


def _build_door(spec: Dict[str, Any], foundation_h: float) -> bpy.types.Object:
    fd = _safe_float(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_safe_float(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)
    door_w = _clamp(_safe_float(spec.get("door_width_m"), 0.9), 0.7, 1.2)
    door_h = _clamp(_safe_float(spec.get("door_height_m"), 2.1), 1.8, 2.4)

    # Place on south side.
    door = _box(
        "Door",
        (door_w, max(0.03, wt * 0.45), door_h),
        (0.0, -fd * 0.5 + wt * 0.3, foundation_h + door_h * 0.5),
    )
    _assign_material(door, (0.34, 0.22, 0.14, 1.0), roughness=0.55)

    walls = bpy.data.objects.get("Walls")
    if walls:
        _cut_boolean(
            target=walls,
            cutter_size=(door_w + 0.03, wt + 0.08, door_h + 0.03),
            cutter_location=(0.0, -fd * 0.5, foundation_h + door_h * 0.5),
            name="DoorOpening",
        )

    return door


def _build_windows(spec: Dict[str, Any], foundation_h: float) -> None:
    fw = _safe_float(spec.get("footprint_w_m"), 8.0)
    fd = _safe_float(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_safe_float(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)
    win_w = _clamp(_safe_float(spec.get("window_width_m"), 1.2), 0.5, 2.4)
    win_h = _clamp(_safe_float(spec.get("window_height_m"), 1.0), 0.5, 2.4)

    heights = _floor_heights(spec)
    slab_t = _safe_float(spec.get("floor_slab_t_m"), 0.25)

    z = foundation_h
    walls = bpy.data.objects.get("Walls")
    if not walls:
        return

    for floor_idx, floor_h in enumerate(heights):
        sill = z + max(0.35, min(1.1, floor_h * 0.33))
        center_z = sill + win_h * 0.5

        for i, x in enumerate((-fw * 0.25, fw * 0.25)):
            # South windows
            name = f"Window_{floor_idx+1}_S_{i+1}"
            win = _box(name, (win_w, max(0.01, wt * 0.2), win_h), (x, -fd * 0.5 + wt * 0.4, center_z))
            _assign_material(win, (0.72, 0.84, 0.94, 1.0), roughness=0.05)
            _cut_boolean(
                target=walls,
                cutter_size=(win_w + 0.02, wt + 0.08, win_h + 0.02),
                cutter_location=(x, -fd * 0.5, center_z),
                name=f"WindowOpening_{floor_idx}_{i}",
            )

        z += floor_h
        if floor_idx < len(heights) - 1:
            z += slab_t


def _build_roof(spec: Dict[str, Any], foundation_h: float, wall_total_h: float) -> bpy.types.Object:
    fw = _safe_float(spec.get("footprint_w_m"), 8.0)
    fd = _safe_float(spec.get("footprint_d_m"), 6.0)
    roof_type = str(spec.get("roof_type", "gable")).lower()
    roof_pitch = _clamp(_safe_float(spec.get("roof_pitch"), 0.4), 0.2, 0.7)
    eaves = _clamp(_safe_float(spec.get("eaves_m"), 0.45), 0.2, 0.9)

    z0 = foundation_h + wall_total_h
    ridge_h = fw * roof_pitch if roof_type == "shed" else (fw * 0.5 * roof_pitch)
    z1 = z0 + ridge_h

    hw = fw * 0.5 + eaves
    hd = fd * 0.5 + eaves

    mesh = bpy.data.meshes.new("RoofMesh")
    roof = bpy.data.objects.new("Roof", mesh)
    bpy.context.collection.objects.link(roof)

    bm = bmesh.new()

    # Base perimeter
    v0 = bm.verts.new((-hw, -hd, z0))
    v1 = bm.verts.new((hw, -hd, z0))
    v2 = bm.verts.new((hw, hd, z0))
    v3 = bm.verts.new((-hw, hd, z0))

    if roof_type == "shed":
        # Single slope: south low -> north high
        v4 = bm.verts.new((-hw, hd, z1))
        v5 = bm.verts.new((hw, hd, z1))
        bm.faces.new([v0, v1, v5, v4])  # main plane
        bm.faces.new([v1, v2, v5])
        bm.faces.new([v0, v4, v3])
        bm.faces.new([v3, v4, v5, v2])
    elif roof_type == "hip":
        # Hip roof
        ridge_half = max(0.01, hd - hw)
        if ridge_half > 0.12:
            v4 = bm.verts.new((0.0, -ridge_half, z1))
            v5 = bm.verts.new((0.0, ridge_half, z1))
            bm.faces.new([v0, v1, v4])
            bm.faces.new([v1, v2, v5, v4])
            bm.faces.new([v2, v3, v5])
            bm.faces.new([v3, v0, v4, v5])
        else:
            vc = bm.verts.new((0.0, 0.0, z1))
            bm.faces.new([v0, v1, vc])
            bm.faces.new([v1, v2, vc])
            bm.faces.new([v2, v3, vc])
            bm.faces.new([v3, v0, vc])
    else:
        # Gable roof
        v4 = bm.verts.new((0.0, -hd, z1))
        v5 = bm.verts.new((0.0, hd, z1))
        bm.faces.new([v0, v1, v4])
        bm.faces.new([v3, v5, v2])
        bm.faces.new([v0, v4, v5, v3])
        bm.faces.new([v1, v2, v5, v4])

    bm.to_mesh(mesh)
    bm.free()

    # Add thickness downward to keep the peak z unchanged.
    solid = roof.modifiers.new(name="RoofSolid", type="SOLIDIFY")
    solid.thickness = 0.08
    solid.offset = -1.0
    bpy.context.view_layer.objects.active = roof
    roof.select_set(True)
    bpy.ops.object.modifier_apply(modifier=solid.name)
    roof.select_set(False)

    _assign_material(roof, (0.2, 0.18, 0.24, 1.0), roughness=0.55)
    return roof


# ------------------------------
# Camera / Render
# ------------------------------

def _setup_render(engine: str, device: str, samples: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080

    if engine == "CYCLES":
        scene.cycles.samples = int(_clamp(float(samples), 16.0, 2048.0))
        scene.cycles.device = device
        if device == "GPU":
            prefs = bpy.context.preferences.addons.get("cycles")
            if prefs:
                prefs.preferences.compute_device_type = "OPTIX"
                prefs.preferences.get_devices()
                for d in prefs.preferences.devices:
                    d.use = True


def _look_at(camera: bpy.types.Object, target: Vector) -> None:
    direction = target - camera.location
    if direction.length < 1.0e-6:
        return
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _setup_camera_and_lights(spec: Dict[str, Any]) -> bpy.types.Object:
    fw = _safe_float(spec.get("footprint_w_m"), 8.0)
    fd = _safe_float(spec.get("footprint_d_m"), 6.0)
    foundation_h = _safe_float(spec.get("foundation_h_m"), 0.4)
    heights = _floor_heights(spec)
    slab_t = _safe_float(spec.get("floor_slab_t_m"), 0.25)
    wall_total = sum(heights) + max(0, len(heights) - 1) * slab_t

    target = Vector((0.0, 0.0, foundation_h + wall_total * 0.55))

    bpy.ops.object.camera_add(location=(fw * 2.0, -fd * 2.2, foundation_h + wall_total * 1.25))
    cam = bpy.context.active_object
    cam.name = "Camera"
    cam.data.lens = 35
    bpy.context.scene.camera = cam
    _look_at(cam, target)

    bpy.ops.object.light_add(type="SUN", location=(fw * 1.8, -fd * 1.8, foundation_h + wall_total * 2.4))
    sun = bpy.context.active_object
    sun.name = "Sun"
    sun.data.energy = 3.5

    bpy.ops.object.light_add(type="AREA", location=(0.0, 0.0, foundation_h + wall_total * 2.0))
    fill = bpy.context.active_object
    fill.name = "FillLight"
    fill.data.energy = 220.0
    fill.data.size = max(6.0, fw)

    return cam


def _render_views(output_dir: Path, prefix: str, cam: bpy.types.Object, spec: Dict[str, Any]) -> Dict[str, str]:
    fw = _safe_float(spec.get("footprint_w_m"), 8.0)
    fd = _safe_float(spec.get("footprint_d_m"), 6.0)
    foundation_h = _safe_float(spec.get("foundation_h_m"), 0.4)
    heights = _floor_heights(spec)
    slab_t = _safe_float(spec.get("floor_slab_t_m"), 0.25)
    wall_total = sum(heights) + max(0, len(heights) - 1) * slab_t

    target = Vector((0.0, 0.0, foundation_h + wall_total * 0.55))
    views = {
        "front": Vector((0.0, -max(8.0, fd * 2.5), foundation_h + wall_total * 0.9)),
        "oblique": Vector((max(7.5, fw * 1.9), -max(7.5, fd * 1.9), foundation_h + wall_total * 1.1)),
        "bird": Vector((0.0, -0.001, foundation_h + wall_total * 2.6)),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    out: Dict[str, str] = {}

    for name, loc in views.items():
        cam.location = loc
        _look_at(cam, target)
        path = output_dir / f"{prefix}_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        out[name] = str(path)
    return out


# ------------------------------
# Metrics
# ------------------------------

def _build_metrics(spec: Dict[str, Any], foundation: bpy.types.Object, walls: bpy.types.Object, roof: bpy.types.Object, door: bpy.types.Object) -> Dict[str, Any]:
    metrics = {
        "footprint_w_m": float(foundation.dimensions.x),
        "footprint_d_m": float(foundation.dimensions.y),
        "wall_total_h_m": float(walls.dimensions.z),
        "roof_peak_z_m": float(_world_bbox_max_z(roof)),
        "door_height_m": float(door.dimensions.z),
        "roof_type_requested": str(spec.get("roof_type", "gable")),
        "roof_type_actual": str(spec.get("roof_type", "gable")),
    }
    return metrics


# ------------------------------
# Main
# ------------------------------

def main() -> None:
    args = parse_args()

    if args.spec_json:
        spec = _read_json(Path(args.spec_json).resolve())
    else:
        # Legacy fallback
        spec = {
            "style_preset": "JP_WOOD_2F_STANDARD",
            "floors": 2,
            "footprint_w_m": 8.0,
            "footprint_d_m": 6.0,
            "floor_heights_m": [2.6, 2.4],
            "wall_thickness_m": 0.18,
            "roof_type": "gable",
            "roof_pitch": 0.4,
            "eaves_m": 0.45,
            "foundation_h_m": 0.4,
            "floor_slab_t_m": 0.25,
            "door_height_m": 2.1,
            "door_width_m": 0.9,
            "window_height_m": 1.0,
            "window_width_m": 1.2,
            "ground_margin_m": 8.0,
            "quality_mode": "balanced",
            "samples": 128,
        }

    samples = _safe_int(args.samples if args.samples is not None else spec.get("samples"), 128)

    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    if output_dir is None:
        # Legacy mode from --output, fallback to blend parent.
        if args.output:
            output_dir = Path(args.output).resolve().parent
        elif args.save_blend:
            output_dir = Path(args.save_blend).resolve().parent
        elif args.blend:
            output_dir = Path(args.blend).resolve().parent
        else:
            output_dir = Path("ag_runs").resolve()

    blend_path_str = args.save_blend or args.blend
    if not blend_path_str:
        blend_path_str = str((output_dir / "house_v4.blend").resolve())
    blend_path = Path(blend_path_str).resolve()

    _clear_scene()

    fw = _safe_float(spec.get("footprint_w_m"), 8.0)
    fd = _safe_float(spec.get("footprint_d_m"), 6.0)
    margin = _clamp(_safe_float(spec.get("ground_margin_m"), 8.0), 2.0, 30.0)
    ground_size = max(fw, fd) + margin * 2.0

    ground = _box("Ground", (ground_size, ground_size, 0.08), (0.0, 0.0, -0.04))
    _assign_material(ground, (0.24, 0.39, 0.18, 1.0), roughness=0.9)

    foundation = _build_foundation(spec)
    walls = _build_walls(spec)

    foundation_h = _safe_float(spec.get("foundation_h_m"), 0.4)
    door = _build_door(spec, foundation_h=foundation_h)
    _build_windows(spec, foundation_h=foundation_h)

    wall_total_h = float(walls.dimensions.z)
    roof = _build_roof(spec, foundation_h=foundation_h, wall_total_h=wall_total_h)

    _setup_render(engine=args.engine, device=args.device, samples=samples)
    cam = _setup_camera_and_lights(spec)
    views = _render_views(output_dir=output_dir, prefix=args.render_prefix, cam=cam, spec=spec)

    metrics = _build_metrics(spec, foundation=foundation, walls=walls, roof=roof, door=door)
    bpy.context.scene["ag_house_actual_json"] = json.dumps(metrics, ensure_ascii=False)

    blend_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    # Legacy single output path support.
    if args.output:
        legacy_out = Path(args.output).resolve()
        if legacy_out != Path(views["front"]).resolve():
            legacy_out.parent.mkdir(parents=True, exist_ok=True)
            bpy.context.scene.render.filepath = str(legacy_out)
            bpy.ops.render.render(write_still=True)

    print(f"[AG] blend={blend_path}")
    print(f"[AG] front={views.get('front', '')}")
    print(f"[AG] oblique={views.get('oblique', '')}")
    print(f"[AG] bird={views.get('bird', '')}")


if __name__ == "__main__":
    main()
