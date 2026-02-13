"""
build_universal_asset.py - Blender汎用アセット生成スクリプト

実行責務:
- spec JSONからドメイン非依存でシーンを構築
- 選定済み外部アセットを取り込み配置
- 3視点(front/oblique/bird)をレンダ
- 検証用メトリクスを scene custom property に格納
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--render-prefix", default="iter_00")
    parser.add_argument("--save-blend", required=True)
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--engine", default="CYCLES")
    parser.add_argument("--device", default="GPU")
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _apply_transform(obj: bpy.types.Object, location: Sequence[float], rotation_deg: Sequence[float], scale: Sequence[float]) -> None:
    obj.location = Vector((float(location[0]), float(location[1]), float(location[2])))
    obj.rotation_euler = (
        math.radians(float(rotation_deg[0])),
        math.radians(float(rotation_deg[1])),
        math.radians(float(rotation_deg[2])),
    )
    obj.scale = Vector((float(scale[0]), float(scale[1]), float(scale[2])))


def _adjust_color(color_rgba: Sequence[float], saturation: float, value_scale: float, color_balance: str) -> Tuple[float, float, float, float]:
    r = _clamp(float(color_rgba[0]), 0.0, 1.0)
    g = _clamp(float(color_rgba[1]), 0.0, 1.0)
    b = _clamp(float(color_rgba[2]), 0.0, 1.0)
    a = _clamp(float(color_rgba[3]), 0.0, 1.0)

    mean = (r + g + b) / 3.0
    sat = max(0.2, min(2.5, float(saturation)))
    r = mean + (r - mean) * sat
    g = mean + (g - mean) * sat
    b = mean + (b - mean) * sat

    if color_balance == "cool":
        r *= 0.92
        b *= 1.08
    elif color_balance == "warm":
        r *= 1.08
        b *= 0.92

    value = max(0.2, min(2.0, float(value_scale)))
    r *= value
    g *= value
    b *= value
    return (_clamp(r, 0.0, 1.0), _clamp(g, 0.0, 1.0), _clamp(b, 0.0, 1.0), a)


def _make_material(
    name: str,
    color_rgba: Sequence[float],
    emissive_strength: float = 0.0,
    roughness: float = 0.45,
    metallic: float = 0.0,
) -> bpy.types.Material:
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
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = float(_clamp(roughness, 0.0, 1.0))
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = float(_clamp(metallic, 0.0, 1.0))
        if emissive_strength > 0.0 and "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (
                float(color_rgba[0]),
                float(color_rgba[1]),
                float(color_rgba[2]),
                1.0,
            )
            bsdf.inputs["Emission Strength"].default_value = float(emissive_strength)
    return mat


def _assign_material(obj: bpy.types.Object, mat: bpy.types.Material) -> None:
    if not obj.data or not hasattr(obj.data, "materials"):
        return
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat


def _contains_any(text: str, tokens: Sequence[str]) -> bool:
    lower = (text or "").lower()
    return any((t in lower) or (t in text) for t in tokens)


def _spec_prompt(spec: Dict[str, Any]) -> str:
    return str(spec.get("source_prompt", "") or "")


def _spec_style_tags(spec: Dict[str, Any]) -> List[str]:
    composition = spec.get("composition", {}) if isinstance(spec.get("composition"), dict) else {}
    tags = composition.get("style_tags", [])
    if not isinstance(tags, list):
        return []
    out: List[str] = []
    for tag in tags:
        token = str(tag).strip().lower()
        if token and token not in out:
            out.append(token)
    return out


_SCENE_CAVE_TOKENS = ("cave", "洞窟", "洞穴", "鉱山", "地下", "grotto", "crypt")
_SCENE_ROOM_TOKENS = ("room", "部屋", "室内", "dungeon", "boss", "ボス", "hall", "temple", "城", "塔", "廊下", "地下室", "扉", "door")
_SCENE_OUTDOOR_TOKENS = ("outdoor", "屋外", "forest", "森", "desert", "砂漠", "beach", "海", "sky", "mountain", "草原", "city", "街")


def _infer_scene_view_mode(spec: Dict[str, Any]) -> str:
    if str(spec.get("domain", "")).lower() != "scene":
        return "default"
    prompt = _spec_prompt(spec)
    tags = _spec_style_tags(spec)
    if "cave" in tags or _contains_any(prompt, _SCENE_CAVE_TOKENS):
        return "enclosed"
    if _contains_any(prompt, _SCENE_OUTDOOR_TOKENS):
        return "open"
    if _contains_any(prompt, _SCENE_ROOM_TOKENS):
        return "enclosed"
    # sceneは既定で「囲い」を作り、陰影と奥行きを確保する
    return "enclosed"


def _setup_world(color_balance: str, strength: float, mode: str = "default") -> None:
    strength = float(_clamp(float(strength), 0.0, 10.0))
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    if not nt:
        return

    bg = None
    out = None
    for node in nt.nodes:
        if node.type == "BACKGROUND":
            bg = node
        elif node.type == "OUTPUT_WORLD":
            out = node
    if bg is None or out is None:
        return

    base = (0.05, 0.05, 0.06, 1.0)
    if mode == "enclosed":
        base = (0.02, 0.02, 0.025, 1.0)
    if str(color_balance).lower() == "cool":
        base = (base[0] * 0.9, base[1] * 0.95, base[2] * 1.15, 1.0)
    elif str(color_balance).lower() == "warm":
        base = (base[0] * 1.15, base[1] * 1.05, base[2] * 0.9, 1.0)

    if "Color" in bg.inputs:
        bg.inputs["Color"].default_value = base
    if "Strength" in bg.inputs:
        bg.inputs["Strength"].default_value = strength


def _add_scaled_cube(name: str, dimensions: Sequence[float], location: Sequence[float], rotation_deg: Sequence[float] = (0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    sx, sy, sz = [max(0.01, float(v)) for v in dimensions]
    obj.scale = Vector((sx, sy, sz))
    _apply_transform(obj, location=location, rotation_deg=rotation_deg, scale=obj.scale)
    return obj


def _apply_noise_displace(obj: bpy.types.Object, strength: float, noise_scale: float, subsurf_levels: int) -> None:
    try:
        subsurf_levels = int(max(0, min(3, subsurf_levels)))
        if subsurf_levels > 0:
            subsurf = obj.modifiers.new(name="AG_SubSurf", type="SUBSURF")
            subsurf.levels = subsurf_levels
            subsurf.render_levels = subsurf_levels
    except Exception:
        pass

    try:
        tex = bpy.data.textures.new(name=f"tex_{obj.name}", type="CLOUDS")
        tex.noise_scale = float(_clamp(noise_scale, 0.2, 50.0))
        mod = obj.modifiers.new(name="AG_Displace", type="DISPLACE")
        mod.texture = tex
        mod.strength = float(_clamp(strength, 0.0, 5.0))
        mod.mid_level = 0.0
    except Exception:
        return


def _maybe_add_fog_volume(width: float, depth: float, height: float, density: float) -> Optional[bpy.types.Object]:
    density = float(_clamp(density, 0.0, 0.2))
    if density <= 0.0:
        return None

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, max(0.6, height * 0.5)))
    vol = bpy.context.active_object
    vol.name = "AG_FogVolume"
    vol.scale = Vector((max(0.5, width * 0.55), max(0.5, depth * 0.55), max(0.5, height * 0.55)))

    mat = bpy.data.materials.new(name="mat_fog")
    mat.use_nodes = True
    nt = mat.node_tree
    if nt:
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
        try:
            node = nt.nodes.new("ShaderNodeVolumePrincipled")
            node.location = (0, 0)
            if "Density" in node.inputs:
                node.inputs["Density"].default_value = density
            if "Anisotropy" in node.inputs:
                node.inputs["Anisotropy"].default_value = 0.2
            nt.links.new(node.outputs.get("Volume"), out.inputs.get("Volume"))
        except Exception:
            scatter = nt.nodes.new("ShaderNodeVolumeScatter")
            scatter.location = (0, 40)
            if "Density" in scatter.inputs:
                scatter.inputs["Density"].default_value = density
            absorb = nt.nodes.new("ShaderNodeVolumeAbsorption")
            absorb.location = (0, -60)
            if "Density" in absorb.inputs:
                absorb.inputs["Density"].default_value = density * 0.55
            add = nt.nodes.new("ShaderNodeAddShader")
            add.location = (160, 0)
            nt.links.new(scatter.outputs.get("Volume"), add.inputs[0])
            nt.links.new(absorb.outputs.get("Volume"), add.inputs[1])
            nt.links.new(add.outputs.get("Shader"), out.inputs.get("Volume"))

    if vol.data and hasattr(vol.data, "materials"):
        if len(vol.data.materials) == 0:
            vol.data.materials.append(mat)
        else:
            vol.data.materials[0] = mat
    return vol


def _maybe_add_scene_enclosure(
    spec: Dict[str, Any],
    width: float,
    depth: float,
    height: float,
    saturation: float,
    value_scale: float,
    color_balance: str,
    feature_density: float,
    quality_mode: str,
) -> Dict[str, Any]:
    if str(spec.get("domain", "")).lower() != "scene":
        return {"mode": "none", "objects": []}

    view_mode = _infer_scene_view_mode(spec)
    if view_mode == "open":
        return {"mode": "open", "objects": []}

    wall_t = float(_clamp(min(width, depth) * 0.04, 0.08, 0.35))
    wall_color = _adjust_color((0.14, 0.14, 0.16, 1.0), saturation=saturation, value_scale=max(0.6, value_scale * 0.85), color_balance=color_balance)
    wall_mat = _make_material("mat_scene_wall", wall_color, roughness=0.9)

    objects: List[str] = []
    back = _add_scaled_cube("AG_Wall_Back", (width, wall_t, height), (0.0, depth * 0.5 - wall_t * 0.5, height * 0.5))
    _assign_material(back, wall_mat)
    objects.append(back.name)

    left = _add_scaled_cube("AG_Wall_Left", (wall_t, depth, height), (-width * 0.5 + wall_t * 0.5, 0.0, height * 0.5))
    _assign_material(left, wall_mat)
    objects.append(left.name)

    right = _add_scaled_cube("AG_Wall_Right", (wall_t, depth, height), (width * 0.5 - wall_t * 0.5, 0.0, height * 0.5))
    _assign_material(right, wall_mat)
    objects.append(right.name)

    ceiling = _add_scaled_cube("AG_Ceiling", (width, depth, wall_t), (0.0, 0.0, height - wall_t * 0.5))
    _assign_material(ceiling, wall_mat)
    objects.append(ceiling.name)

    # caveっぽい場合は壁にノイズを入れて平坦さを減らす
    prompt = _spec_prompt(spec)
    tags = _spec_style_tags(spec)
    is_cave = ("cave" in tags) or _contains_any(prompt, _SCENE_CAVE_TOKENS)
    if is_cave:
        subsurf = 0
        if quality_mode == "high":
            subsurf = 2
        elif quality_mode == "balanced":
            subsurf = 1
        strength = float(_clamp(0.12 * feature_density, 0.04, 0.45))
        noise_scale = float(_clamp(6.0 - feature_density * 1.2, 1.2, 8.0))
        for obj in (back, left, right, ceiling):
            _apply_noise_displace(obj, strength=strength, noise_scale=noise_scale, subsurf_levels=subsurf)

    # 柱/岩で陰影を追加
    pillar_count = int(_clamp(2.0 + feature_density * 2.0, 2.0, 6.0))
    pillar_radius = float(_clamp(min(width, depth) * 0.04, 0.08, 0.28))
    pillar_h = float(_clamp(height * 0.75, 1.2, max(1.4, height * 0.95)))
    pillar_color = _adjust_color((0.20, 0.20, 0.22, 1.0), saturation=saturation, value_scale=value_scale * 0.9, color_balance=color_balance)
    pillar_mat = _make_material("mat_scene_pillar", pillar_color, roughness=0.8)
    for idx in range(pillar_count):
        x = (-width * 0.25) if (idx % 2 == 0) else (width * 0.25)
        y = -depth * (0.15 + 0.22 * (idx // 2))
        bpy.ops.mesh.primitive_cylinder_add(radius=pillar_radius, depth=pillar_h, location=(x, y, pillar_h * 0.5))
        obj = bpy.context.active_object
        obj.name = f"AG_Pillar_{idx+1:02d}"
        _assign_material(obj, pillar_mat)
        objects.append(obj.name)

    return {"mode": "cave" if is_cave else "room", "objects": objects, "view_mode": view_mode}


def _add_part(
    part: Dict[str, Any],
    idx: int,
    saturation: float = 1.0,
    value_scale: float = 1.0,
    color_balance: str = "neutral",
    emissive_strength: float = 0.0,
) -> bpy.types.Object:
    shape = str(part.get("shape", "cube")).lower()
    size = part.get("size") or [1.0, 1.0, 1.0]
    location = part.get("location") or [0.0, 0.0, 0.5]
    rotation_deg = part.get("rotation_deg") or [0.0, 0.0, 0.0]
    color = part.get("color") or [0.8, 0.8, 0.8, 1.0]
    name = str(part.get("name") or f"part_{idx:02d}")

    sx, sy, sz = [max(0.01, float(v)) for v in size]

    if shape == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0.0, 0.0, 0.0), segments=32, ring_count=16)
        obj = bpy.context.active_object
        obj.scale = (sx, sy, sz)
    elif shape == "cylinder":
        radius = max(sx, sy) * 0.5
        bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=sz, location=(0.0, 0.0, 0.0), vertices=32)
        obj = bpy.context.active_object
    elif shape == "cone":
        radius = max(sx, sy) * 0.5
        bpy.ops.mesh.primitive_cone_add(radius1=radius, depth=sz, location=(0.0, 0.0, 0.0), vertices=32)
        obj = bpy.context.active_object
    elif shape == "torus":
        major = max(sx, sy) * 0.5
        minor = max(0.01, min(sx, sy, sz) * 0.18)
        bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
    elif shape == "plane":
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
        obj.scale = (sx, sy, 1.0)
    else:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
        obj = bpy.context.active_object
        obj.scale = (sx, sy, sz)

    obj.name = name
    _apply_transform(obj, location=location, rotation_deg=rotation_deg, scale=obj.scale)

    adj_color = _adjust_color(color, saturation=saturation, value_scale=value_scale, color_balance=color_balance)
    mat = _make_material(f"mat_{name}", adj_color, emissive_strength=emissive_strength)
    _assign_material(obj, mat)
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
            imported = _import_blend(path)
            return imported, "ok"
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
    new_names = sorted(after - before)
    return [bpy.data.objects[name] for name in new_names if name in bpy.data.objects], "ok"


def _apply_asset_transform(objects: Sequence[bpy.types.Object], location: Sequence[float], rotation_deg: Sequence[float], scale: Sequence[float]) -> None:
    for obj in objects:
        _apply_transform(obj, location=location, rotation_deg=rotation_deg, scale=scale)


def _look_at(camera: bpy.types.Object, target: Vector) -> None:
    direction = target - camera.location
    if direction.length < 1.0e-6:
        return
    rot_quat = direction.to_track_quat("-Z", "Y")
    camera.rotation_euler = rot_quat.to_euler()


def _setup_camera_and_light(dimensions: Dict[str, float], key_light_energy: float, fill_light_energy: float) -> bpy.types.Object:
    width = float(dimensions.get("width", 2.0))
    depth = float(dimensions.get("depth", 2.0))
    height = float(dimensions.get("height", 2.0))

    bpy.ops.object.camera_add(location=(width * 1.8, -depth * 1.8, height * 1.6))
    camera = bpy.context.active_object
    camera.name = "AG_Camera"
    bpy.context.scene.camera = camera

    bpy.ops.object.light_add(type="SUN", location=(width * 2.0, -depth * 2.0, height * 3.0))
    sun = bpy.context.active_object
    sun.name = "AG_Sun"
    sun.data.energy = float(_clamp(key_light_energy, 0.1, 20000.0))

    bpy.ops.object.light_add(type="AREA", location=(0.0, 0.0, height * 2.2))
    area = bpy.context.active_object
    area.name = "AG_Area"
    area.data.energy = float(_clamp(fill_light_energy, 0.1, 20000.0))

    return camera


def _setup_render(
    engine: str,
    device: str,
    samples: int,
    exposure: float = 0.0,
    output_size: Tuple[int, int] = (1920, 1080),
) -> None:
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.render.resolution_x = int(output_size[0])
    scene.render.resolution_y = int(output_size[1])
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.exposure = float(_clamp(exposure, -5.0, 5.0))

    if engine == "CYCLES":
        scene.cycles.samples = int(max(16, min(samples, 2048)))
        scene.cycles.device = device
        if device == "GPU":
            prefs = bpy.context.preferences.addons.get("cycles")
            if prefs:
                prefs.preferences.compute_device_type = "OPTIX"
                prefs.preferences.get_devices()
                for dev in prefs.preferences.devices:
                    dev.use = True


def _render_views(
    camera: bpy.types.Object,
    output_dir: Path,
    prefix: str,
    dimensions: Dict[str, float],
    camera_distance_scale: float = 1.0,
    view_mode: str = "default",
) -> Dict[str, str]:
    width = float(dimensions.get("width", 2.0))
    depth = float(dimensions.get("depth", 2.0))
    height = float(dimensions.get("height", 2.0))
    target_z = max(0.6, height * 0.5)
    if view_mode == "enclosed":
        target_z = max(0.6, height * 0.45)
    target = Vector((0.0, 0.0, target_z))

    distance_scale = float(_clamp(camera_distance_scale, 0.35, 3.2))
    if view_mode == "enclosed":
        view_defs = {
            "front": Vector((0.0, -max(0.8, depth * 0.28) * distance_scale, max(1.0, height * 0.65) * distance_scale)),
            "oblique": Vector((max(0.8, width * 0.22) * distance_scale, -max(0.7, depth * 0.22) * distance_scale, max(1.0, height * 0.72) * distance_scale)),
            "bird": Vector((0.0, -0.001, max(1.8, height * 1.15) * distance_scale)),
        }
    else:
        view_defs = {
            "front": Vector((0.0, -max(2.5, depth * 2.0) * distance_scale, max(1.2, height * 1.2) * distance_scale)),
            "oblique": Vector((max(2.0, width * 1.6) * distance_scale, -max(2.0, depth * 1.6) * distance_scale, max(1.2, height * 1.2) * distance_scale)),
            "bird": Vector((0.0, -0.001, max(3.0, max(width, depth, height) * 2.6) * distance_scale)),
        }

    outputs: Dict[str, str] = {}
    scene = bpy.context.scene
    output_dir.mkdir(parents=True, exist_ok=True)

    for name, location in view_defs.items():
        camera.location = location
        _look_at(camera, target)
        path = output_dir / f"{prefix}_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        outputs[name] = str(path)
    return outputs


def _render_turntable(
    camera: bpy.types.Object,
    output_dir: Path,
    prefix: str,
    dimensions: Dict[str, float],
    camera_distance_scale: float,
    view_mode: str,
    frames: int = 8,
) -> List[str]:
    width = float(dimensions.get("width", 2.0))
    depth = float(dimensions.get("depth", 2.0))
    height = float(dimensions.get("height", 2.0))

    distance_scale = float(_clamp(camera_distance_scale, 0.35, 3.2))
    target = Vector((0.0, 0.0, max(0.6, height * (0.45 if view_mode == "enclosed" else 0.5))))

    if view_mode == "enclosed":
        radius = max(0.6, min(width, depth) * 0.22) * distance_scale
        z = max(1.0, height * 0.7) * distance_scale
    else:
        radius = max(2.5, max(width, depth) * 2.0) * distance_scale
        z = max(1.4, height * 1.2) * distance_scale

    frames = int(max(3, min(64, frames)))
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene

    outputs: List[str] = []
    for i in range(frames):
        angle = (float(i) / float(frames)) * math.pi * 2.0
        camera.location = Vector((math.cos(angle) * radius, math.sin(angle) * radius, z))
        _look_at(camera, target)
        path = output_dir / f"{prefix}_turn_{i:02d}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        outputs.append(str(path))
    return outputs


def _compute_scene_metrics(asset_details: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and obj.name != "AG_Ground"]
    poly_count = 0
    zero_face_meshes: List[str] = []

    bbox_min = Vector((10**9, 10**9, 10**9))
    bbox_max = Vector((-10**9, -10**9, -10**9))

    for obj in meshes:
        poly_count += int(len(obj.data.polygons) if obj.data else 0)
        if obj.data:
            has_zero_face = any(poly.area <= 1.0e-10 for poly in obj.data.polygons)
            if has_zero_face:
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
        "asset_details": list(asset_details),
    }


def _is_crystal_cave_scene(spec: Dict[str, Any]) -> bool:
    if str(spec.get("domain", "")).lower() != "scene":
        return False
    prompt = str(spec.get("source_prompt", "")).lower()
    composition = spec.get("composition", {}) if isinstance(spec.get("composition"), dict) else {}
    style_tags = [str(v).lower() for v in (composition.get("style_tags") or []) if str(v).strip()]
    tokens = ("crystal", "cave", "水晶", "洞窟")
    return any(token in prompt for token in tokens) or any(token in " ".join(style_tags) for token in tokens)


def _stable_seed_from_text(seed_text: str) -> int:
    digest = hashlib.sha256(seed_text.encode("utf-8", "replace")).hexdigest()
    # Use the first 8 hex chars to keep an int32-sized seed.
    return int(digest[:8], 16)


def _add_crystal_cluster(
    seed_text: str,
    feature_density: float,
    feature_variation: float,
    saturation: float,
    value_scale: float,
    color_balance: str,
    emissive_strength: float,
) -> int:
    seed = _stable_seed_from_text(seed_text)
    rng = random.Random(seed)
    count = int(_clamp(14.0 * feature_density, 6.0, 70.0))
    variation = float(_clamp(feature_variation, 0.05, 1.0))
    base_radius = 2.2
    placed = 0

    for idx in range(count):
        angle = (idx / max(1, count)) * math.pi * 2.0
        angle += rng.uniform(-0.2, 0.2) * variation
        radius = base_radius * (0.45 + rng.uniform(0.0, 0.8) * variation)
        x = math.cos(angle) * radius
        y = math.sin(angle) * radius
        height = 0.6 + rng.uniform(0.2, 2.1) * (0.55 + variation * 0.8)
        width = 0.08 + rng.uniform(0.0, 0.25) * (0.6 + variation * 0.7)
        tilt_x = rng.uniform(-15.0, 15.0) * variation
        tilt_y = rng.uniform(-15.0, 15.0) * variation
        rot_z = rng.uniform(0.0, 360.0)
        color = [
            _clamp(0.34 + rng.uniform(-0.12, 0.08) * variation, 0.0, 1.0),
            _clamp(0.52 + rng.uniform(-0.18, 0.1) * variation, 0.0, 1.0),
            _clamp(0.88 + rng.uniform(-0.08, 0.08) * variation, 0.0, 1.0),
            1.0,
        ]
        _add_part(
            {
                "name": f"crystal_auto_{idx:02d}",
                "shape": "cone",
                "size": [width, width, height],
                "location": [x, y, max(0.02, height * 0.48)],
                "rotation_deg": [tilt_x, tilt_y, rot_z],
                "color": color,
            },
            idx=1000 + idx,
            saturation=saturation,
            value_scale=value_scale,
            color_balance=color_balance,
            emissive_strength=max(0.0, emissive_strength),
        )
        placed += 1
    return placed


def main() -> None:
    args = parse_args()
    spec = json.loads(Path(args.spec_json).read_text(encoding="utf-8-sig"))

    clear_scene()

    dims = spec.get("dimensions_m") if isinstance(spec.get("dimensions_m"), dict) else {}
    width = float(dims.get("width", 2.0) or 2.0)
    depth = float(dims.get("depth", 2.0) or 2.0)
    height = float(dims.get("height", 2.0) or 2.0)

    render_tuning = spec.get("render_tuning", {}) if isinstance(spec.get("render_tuning"), dict) else {}
    procedural_controls = spec.get("procedural_controls", {}) if isinstance(spec.get("procedural_controls"), dict) else {}

    key_light_energy = _safe_float(render_tuning.get("key_light_energy"), 4.0)
    fill_light_energy = _safe_float(render_tuning.get("fill_light_energy"), 250.0)
    camera_distance_scale = _safe_float(render_tuning.get("camera_distance_scale"), 1.0)
    exposure = _safe_float(render_tuning.get("exposure"), 0.0)
    saturation = _safe_float(render_tuning.get("saturation"), 1.0)
    value_scale = _safe_float(render_tuning.get("value_scale"), 1.0)
    color_balance = str(render_tuning.get("color_balance", "neutral")).strip().lower()
    emissive_strength = _safe_float(procedural_controls.get("emissive_strength"), 0.0)
    feature_density = _safe_float(procedural_controls.get("feature_density"), 1.0)
    feature_variation = _safe_float(procedural_controls.get("feature_variation"), 0.5)
    quality_mode = str(spec.get("quality_mode", "balanced")).strip().lower()

    # ground
    bpy.ops.mesh.primitive_plane_add(size=max(4.0, width * 4.0, depth * 4.0), location=(0.0, 0.0, 0.0))
    ground = bpy.context.active_object
    ground.name = "AG_Ground"
    ground_color = _adjust_color((0.82, 0.82, 0.82, 1.0), saturation=saturation, value_scale=value_scale, color_balance=color_balance)
    ground_mat = _make_material("mat_ground", ground_color, emissive_strength=max(0.0, emissive_strength * 0.15))
    _assign_material(ground, ground_mat)

    scene_view_mode = _infer_scene_view_mode(spec)
    _setup_world(color_balance=color_balance, strength=(0.25 if scene_view_mode == "enclosed" else 0.8), mode=scene_view_mode)

    scene_enclosure = _maybe_add_scene_enclosure(
        spec=spec,
        width=width,
        depth=depth,
        height=height,
        saturation=saturation,
        value_scale=value_scale,
        color_balance=color_balance,
        feature_density=feature_density,
        quality_mode=quality_mode,
    )
    if scene_view_mode == "enclosed":
        # enclosedでは軽い霧で奥行きを出す（過度に重くしない）
        fog_density = 0.0
        if quality_mode == "high":
            fog_density = 0.018
        elif quality_mode == "balanced":
            fog_density = 0.012
        _maybe_add_fog_volume(width=width, depth=depth, height=height, density=fog_density)

    parts = spec.get("composition", {}).get("parts", []) if isinstance(spec.get("composition"), dict) else []
    if not isinstance(parts, list):
        parts = []

    for idx, part in enumerate(parts):
        if isinstance(part, dict):
            _add_part(
                part,
                idx,
                saturation=saturation,
                value_scale=value_scale,
                color_balance=color_balance,
                emissive_strength=max(0.0, emissive_strength),
            )

    auto_generated = 0
    if _is_crystal_cave_scene(spec):
        seed_text = f"{str(spec.get('source_prompt', ''))}|{str(spec.get('random_seed', ''))}"
        auto_generated = _add_crystal_cluster(
            seed_text=seed_text,
            feature_density=feature_density,
            feature_variation=feature_variation,
            saturation=saturation,
            value_scale=value_scale,
            color_balance=color_balance,
            emissive_strength=emissive_strength,
        )

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

    camera = _setup_camera_and_light(spec.get("dimensions_m", {}), key_light_energy=key_light_energy, fill_light_energy=fill_light_energy)
    _setup_render(engine=args.engine, device=args.device, samples=args.samples, exposure=exposure)

    output_dir = Path(args.output_dir).resolve()
    views = _render_views(
        camera,
        output_dir=output_dir,
        prefix=args.render_prefix,
        dimensions=spec.get("dimensions_m", {}),
        camera_distance_scale=camera_distance_scale,
        view_mode=("enclosed" if scene_view_mode == "enclosed" else "default"),
    )
    turntable: List[str] = []
    if str(spec.get("domain", "")).lower() == "scene" and quality_mode in ("balanced", "high"):
        turntable = _render_turntable(
            camera=camera,
            output_dir=output_dir,
            prefix=args.render_prefix,
            dimensions=spec.get("dimensions_m", {}),
            camera_distance_scale=camera_distance_scale,
            view_mode=("enclosed" if scene_view_mode == "enclosed" else "default"),
            frames=(12 if quality_mode == "high" else 8),
        )

    blend_path = Path(args.save_blend).resolve()
    blend_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    metrics = _compute_scene_metrics(asset_details)
    metrics["render_outputs"] = views
    if turntable:
        metrics["turntable_outputs"] = turntable
    metrics["auto_generated_feature_count"] = int(auto_generated)
    metrics["scene_view_mode"] = scene_view_mode
    metrics["scene_enclosure"] = scene_enclosure
    metrics["render_tuning"] = {
        "key_light_energy": key_light_energy,
        "fill_light_energy": fill_light_energy,
        "camera_distance_scale": camera_distance_scale,
        "exposure": exposure,
        "saturation": saturation,
        "value_scale": value_scale,
        "color_balance": color_balance,
    }
    metrics["procedural_controls"] = {
        "feature_density": feature_density,
        "feature_variation": feature_variation,
        "emissive_strength": emissive_strength,
    }
    bpy.context.scene["ag_universal_actual_json"] = json.dumps(metrics, ensure_ascii=False)

    # 保存し直してcustom propertyを反映
    bpy.ops.wm.save_mainfile(filepath=str(blend_path))

    print(f"[AG] blend={blend_path}")
    print(f"[AG] front={views.get('front', '')}")
    print(f"[AG] oblique={views.get('oblique', '')}")
    print(f"[AG] bird={views.get('bird', '')}")


if __name__ == "__main__":
    main()
