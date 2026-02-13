"""
build_character_v1.py - キャラクター生成スクリプト

責務:
- character_spec + character_blueprint から部位パーツを生成
- 必要に応じて簡易リグを作成
- 3視点レンダ (front/oblique/bird) を出力
- 検証用メトリクスを scene custom property に記録
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-json", required=True)
    parser.add_argument("--blueprint-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--render-prefix", default="iter_00")
    parser.add_argument("--save-blend", required=True)
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--engine", default="CYCLES")
    parser.add_argument("--device", default="GPU")
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _apply_transform(obj: bpy.types.Object, location: Sequence[float], rotation_deg: Sequence[float], scale: Sequence[float]) -> None:
    obj.location = Vector((float(location[0]), float(location[1]), float(location[2])))
    obj.rotation_euler = (
        math.radians(float(rotation_deg[0])),
        math.radians(float(rotation_deg[1])),
        math.radians(float(rotation_deg[2])),
    )
    obj.scale = Vector((float(scale[0]), float(scale[1]), float(scale[2])))


def _make_material(name: str, color_rgba: Sequence[float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (
            float(color_rgba[0]),
            float(color_rgba[1]),
            float(color_rgba[2]),
            float(color_rgba[3]),
        )
        bsdf.inputs["Roughness"].default_value = 0.45
    return mat


def _assign_material(obj: bpy.types.Object, mat: bpy.types.Material) -> None:
    if not obj.data or not hasattr(obj.data, "materials"):
        return
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat


def _add_part(part: Dict[str, Any], idx: int) -> bpy.types.Object:
    shape = str(part.get("shape", "cube")).lower()
    size = part.get("size") or [1.0, 1.0, 1.0]
    location = part.get("location") or [0.0, 0.0, 0.0]
    rotation_deg = part.get("rotation_deg") or [0.0, 0.0, 0.0]
    color = part.get("color") or [0.8, 0.8, 0.8, 1.0]
    name = str(part.get("name") or f"part_{idx:02d}")

    sx, sy, sz = [max(0.01, float(v)) for v in size]
    if shape == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=32, ring_count=16, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
        scale = [sx, sy, sz]
    elif shape == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, vertices=32, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
        scale = [sx, sy, sz]
    elif shape == "cone":
        bpy.ops.mesh.primitive_cone_add(radius1=0.5, depth=1.0, vertices=32, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
        scale = [sx, sy, sz]
    elif shape == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=0.5, minor_radius=0.2, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
        scale = [sx, sy, sz]
    elif shape == "plane":
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
        scale = [sx, sy, 1.0]
    else:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
        scale = [sx, sy, sz]

    obj.name = name
    _apply_transform(obj, location=location, rotation_deg=rotation_deg, scale=scale)
    _assign_material(obj, _make_material(f"mat_{name}", color))
    return obj


def _import_blend(path: Path) -> List[bpy.types.Object]:
    imported: List[bpy.types.Object] = []
    with bpy.data.libraries.load(str(path), link=False) as (data_from, data_to):
        data_to.objects = data_from.objects
    for obj in data_to.objects:
        if obj is None:
            continue
        bpy.context.collection.objects.link(obj)
        imported.append(obj)
    return imported


def _import_asset(path: Path) -> Tuple[List[bpy.types.Object], str]:
    ext = path.suffix.lower()
    before = set(o.name for o in bpy.data.objects)
    try:
        if ext == ".blend":
            return _import_blend(path), "ok"
        if ext == ".obj":
            if hasattr(bpy.ops.wm, "obj_import"):
                bpy.ops.wm.obj_import(filepath=str(path))
            else:
                bpy.ops.import_scene.obj(filepath=str(path))
        elif ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=str(path))
        elif ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=str(path))
        else:
            return [], f"unsupported format: {ext}"
    except Exception as exc:  # pragma: no cover - Blender runtime dependent
        return [], f"import failed: {exc}"

    after = set(o.name for o in bpy.data.objects)
    names = sorted(after - before)
    return [bpy.data.objects[name] for name in names if name in bpy.data.objects], "ok"


def _apply_asset_transform(objects: Sequence[bpy.types.Object], location: Sequence[float], rotation_deg: Sequence[float], scale: Sequence[float]) -> None:
    for obj in objects:
        _apply_transform(obj, location=location, rotation_deg=rotation_deg, scale=scale)


def _create_basic_rig(height_m: float) -> bpy.types.Object:
    bpy.ops.object.armature_add(enter_editmode=True, location=(0.0, 0.0, 0.0))
    armature = bpy.context.active_object
    armature.name = "AG_Rig"
    edit_bones = armature.data.edit_bones

    for bone in list(edit_bones):
        edit_bones.remove(bone)

    def _new_bone(name: str, head: Sequence[float], tail: Sequence[float], parent: Any = None) -> Any:
        bone = edit_bones.new(name)
        bone.head = Vector((float(head[0]), float(head[1]), float(head[2])))
        bone.tail = Vector((float(tail[0]), float(tail[1]), float(tail[2])))
        if parent is not None:
            bone.parent = parent
        return bone

    h = max(0.8, min(3.0, float(height_m)))
    root = _new_bone("root", [0.0, 0.0, 0.0], [0.0, 0.0, h * 0.12])
    spine = _new_bone("spine", [0.0, 0.0, h * 0.12], [0.0, 0.0, h * 0.55], parent=root)
    chest = _new_bone("chest", [0.0, 0.0, h * 0.55], [0.0, 0.0, h * 0.76], parent=spine)
    neck = _new_bone("neck", [0.0, 0.0, h * 0.76], [0.0, 0.0, h * 0.85], parent=chest)
    _new_bone("head", [0.0, 0.0, h * 0.85], [0.0, 0.0, h * 0.96], parent=neck)

    upper_arm_l = _new_bone("upper_arm_l", [h * 0.12, 0.0, h * 0.72], [h * 0.24, 0.0, h * 0.66], parent=chest)
    _new_bone("lower_arm_l", [h * 0.24, 0.0, h * 0.66], [h * 0.33, 0.0, h * 0.56], parent=upper_arm_l)
    upper_arm_r = _new_bone("upper_arm_r", [-h * 0.12, 0.0, h * 0.72], [-h * 0.24, 0.0, h * 0.66], parent=chest)
    _new_bone("lower_arm_r", [-h * 0.24, 0.0, h * 0.66], [-h * 0.33, 0.0, h * 0.56], parent=upper_arm_r)

    upper_leg_l = _new_bone("upper_leg_l", [h * 0.05, 0.0, h * 0.48], [h * 0.06, 0.0, h * 0.25], parent=root)
    _new_bone("lower_leg_l", [h * 0.06, 0.0, h * 0.25], [h * 0.07, 0.0, h * 0.03], parent=upper_leg_l)
    upper_leg_r = _new_bone("upper_leg_r", [-h * 0.05, 0.0, h * 0.48], [-h * 0.06, 0.0, h * 0.25], parent=root)
    _new_bone("lower_leg_r", [-h * 0.06, 0.0, h * 0.25], [-h * 0.07, 0.0, h * 0.03], parent=upper_leg_r)

    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def _look_at(camera: bpy.types.Object, target: Vector) -> None:
    direction = target - camera.location
    if direction.length < 1.0e-6:
        return
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _setup_camera_and_light(dims: Dict[str, float], view_hints: Dict[str, Any]) -> bpy.types.Object:
    height = float(dims.get("height", 1.72))
    width = float(dims.get("width", 0.7))
    depth = float(dims.get("depth", 0.45))

    default_front = Vector((0.0, -max(2.4, depth * 4.5), max(1.1, height * 0.95)))
    hint_front = view_hints.get("front", {}) if isinstance(view_hints.get("front"), dict) else {}
    loc_front = hint_front.get("camera_location") if isinstance(hint_front.get("camera_location"), list) else list(default_front)
    loc = (float(loc_front[0]), float(loc_front[1]), float(loc_front[2]))

    bpy.ops.object.camera_add(location=loc)
    camera = bpy.context.active_object
    camera.name = "AG_Camera"
    camera.data.lens = 50
    bpy.context.scene.camera = camera

    bpy.ops.object.light_add(type="SUN", location=(max(3.0, width * 5.0), -max(3.0, depth * 7.0), max(4.0, height * 3.0)))
    sun = bpy.context.active_object
    sun.name = "AG_Sun"
    sun.data.energy = 3.5

    bpy.ops.object.light_add(type="AREA", location=(0.0, 0.0, max(3.5, height * 2.3)))
    area = bpy.context.active_object
    area.name = "AG_Area"
    area.data.energy = 240.0
    return camera


def _setup_render(engine: str, device: str, samples: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.image_settings.file_format = "PNG"
    if engine == "CYCLES":
        scene.cycles.samples = int(max(16, min(int(samples), 2048)))
        scene.cycles.device = device
        if device == "GPU":
            prefs = bpy.context.preferences.addons.get("cycles")
            if prefs:
                prefs.preferences.compute_device_type = "OPTIX"
                prefs.preferences.get_devices()
                for dev in prefs.preferences.devices:
                    dev.use = True


def _render_views(camera: bpy.types.Object, output_dir: Path, prefix: str, dims: Dict[str, float], view_hints: Dict[str, Any]) -> Dict[str, str]:
    height = float(dims.get("height", 1.72))
    width = float(dims.get("width", 0.7))
    depth = float(dims.get("depth", 0.45))
    target = Vector((0.0, 0.0, max(0.6, height * 0.54)))

    defaults = {
        "front": [0.0, -max(2.4, depth * 4.5), max(1.1, height * 0.95)],
        "oblique": [max(1.8, width * 3.0), -max(2.0, depth * 3.5), max(1.1, height * 0.95)],
        "bird": [0.0, -0.001, max(3.0, height * 2.8)],
    }
    views: Dict[str, Vector] = {}
    for name, default_loc in defaults.items():
        hint = view_hints.get(name, {}) if isinstance(view_hints.get(name), dict) else {}
        loc = hint.get("camera_location") if isinstance(hint.get("camera_location"), list) and len(hint.get("camera_location")) == 3 else default_loc
        views[name] = Vector((float(loc[0]), float(loc[1]), float(loc[2])))

    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    outputs: Dict[str, str] = {}
    for name, location in views.items():
        camera.location = location
        _look_at(camera, target)
        path = output_dir / f"{prefix}_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        outputs[name] = str(path)
    return outputs


def _compute_scene_metrics(expected_parts: Sequence[str], symmetry_pairs: Sequence[Dict[str, str]], asset_details: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and obj.name != "AG_Ground"]
    poly_count = 0
    zero_face_meshes: List[str] = []

    bbox_min = Vector((10**9, 10**9, 10**9))
    bbox_max = Vector((-10**9, -10**9, -10**9))

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

    existing_parts = [obj.name for obj in meshes]
    expected = [str(name).strip() for name in expected_parts if str(name).strip()]
    missing_expected_parts = [name for name in expected if name not in existing_parts]

    symmetry_data: List[Dict[str, Any]] = []
    for pair in symmetry_pairs:
        left_name = str(pair.get("left", ""))
        right_name = str(pair.get("right", ""))
        left_obj = bpy.data.objects.get(left_name)
        right_obj = bpy.data.objects.get(right_name)
        if not left_obj or not right_obj:
            symmetry_data.append(
                {
                    "left": left_name,
                    "right": right_name,
                    "available": False,
                    "position_error_m": None,
                    "height_error_m": None,
                    "scale_error": None,
                }
            )
            continue
        position_error = abs(float(left_obj.location.x) + float(right_obj.location.x))
        height_error = abs(float(left_obj.location.z) - float(right_obj.location.z))
        scale_error = max(abs(float(left_obj.scale[i]) - float(right_obj.scale[i])) for i in range(3))
        symmetry_data.append(
            {
                "left": left_name,
                "right": right_name,
                "available": True,
                "position_error_m": float(position_error),
                "height_error_m": float(height_error),
                "scale_error": float(scale_error),
            }
        )

    rig_present = any(obj.type == "ARMATURE" and obj.name == "AG_Rig" for obj in bpy.data.objects)
    return {
        "object_count": len(meshes),
        "poly_count": poly_count,
        "bbox_width_m": bbox_width,
        "bbox_depth_m": bbox_depth,
        "bbox_height_m": bbox_height,
        "zero_face_meshes": zero_face_meshes,
        "existing_parts": existing_parts,
        "missing_expected_parts": missing_expected_parts,
        "symmetry_data": symmetry_data,
        "rig_present": rig_present,
        "asset_details": list(asset_details),
    }


def main() -> None:
    args = parse_args()
    spec = json.loads(Path(args.spec_json).read_text(encoding="utf-8-sig"))
    blueprint = json.loads(Path(args.blueprint_json).read_text(encoding="utf-8-sig"))
    clear_scene()

    dims = spec.get("dimensions_m", {}) if isinstance(spec.get("dimensions_m"), dict) else {}
    width = float(dims.get("width", 0.7) or 0.7)
    depth = float(dims.get("depth", 0.45) or 0.45)

    bpy.ops.mesh.primitive_plane_add(size=max(3.0, width * 6.0, depth * 10.0), location=(0.0, 0.0, 0.0))
    ground = bpy.context.active_object
    ground.name = "AG_Ground"
    _assign_material(ground, _make_material("mat_ground", (0.82, 0.82, 0.82, 1.0)))

    part_plan = blueprint.get("part_plan", []) if isinstance(blueprint.get("part_plan"), list) else []
    built_mesh_count = 0
    for idx, part in enumerate(part_plan):
        if not isinstance(part, dict):
            continue
        _add_part(part, idx)
        built_mesh_count += 1

    if built_mesh_count == 0:
        _add_part({"name": "fallback_body", "shape": "cube", "size": [0.2, 0.2, 0.5], "location": [0.0, 0.0, 0.25]}, 0)

    selected_assets = spec.get("composition", {}).get("selected_assets", []) if isinstance(spec.get("composition"), dict) else []
    if not isinstance(selected_assets, list):
        selected_assets = []
    asset_details: List[Dict[str, Any]] = []

    for asset in selected_assets:
        if not isinstance(asset, dict):
            continue
        path = Path(str(asset.get("path", "")).strip())
        if not path.exists():
            asset_details.append({"id": str(asset.get("id", "")), "status": "missing", "detail": str(path)})
            continue
        imported, detail = _import_asset(path)
        if not imported:
            asset_details.append({"id": str(asset.get("id", "")), "status": "failed", "detail": detail})
            continue
        location = asset.get("location") or [0.0, 0.0, 0.0]
        rotation_deg = asset.get("rotation_deg") or [0.0, 0.0, 0.0]
        scale = asset.get("scale") or [1.0, 1.0, 1.0]
        _apply_asset_transform(imported, location=location, rotation_deg=rotation_deg, scale=scale)
        asset_details.append(
            {
                "id": str(asset.get("id", "")),
                "status": "ok",
                "detail": detail,
                "imported_objects": [obj.name for obj in imported],
            }
        )

    constraints = spec.get("target_constraints", {}) if isinstance(spec.get("target_constraints"), dict) else {}
    require_rig = bool(constraints.get("require_rig", True))
    if require_rig:
        _create_basic_rig(float(dims.get("height", 1.72) or 1.72))

    view_hints = blueprint.get("view_hints", {}) if isinstance(blueprint.get("view_hints"), dict) else {}
    camera = _setup_camera_and_light(dims=dims, view_hints=view_hints)
    _setup_render(engine=args.engine, device=args.device, samples=args.samples)

    output_dir = Path(args.output_dir).resolve()
    views = _render_views(camera, output_dir=output_dir, prefix=args.render_prefix, dims=dims, view_hints=view_hints)

    blend_path = Path(args.save_blend).resolve()
    blend_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    metrics = _compute_scene_metrics(
        expected_parts=blueprint.get("active_parts", []) if isinstance(blueprint.get("active_parts"), list) else [],
        symmetry_pairs=blueprint.get("symmetry_pairs", []) if isinstance(blueprint.get("symmetry_pairs"), list) else [],
        asset_details=asset_details,
    )
    metrics["render_outputs"] = views
    bpy.context.scene["ag_character_actual_json"] = json.dumps(metrics, ensure_ascii=False)
    bpy.ops.wm.save_mainfile(filepath=str(blend_path))

    print(f"[AG] blend={blend_path}")
    print(f"[AG] front={views.get('front', '')}")
    print(f"[AG] oblique={views.get('oblique', '')}")
    print(f"[AG] bird={views.get('bird', '')}")


if __name__ == "__main__":
    main()
