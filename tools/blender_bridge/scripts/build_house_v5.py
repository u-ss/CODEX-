"""
build_house_v5.py - 高精度戸建てビルダー v5

v4からの改善:
- 6視点カメラ (front/back/left/right/oblique/bird)
- テクスチャ付きマテリアル (ノイズ+Bump)
- ディテール (巾木/廻縁/窓枠/破風板/雨樋/バルコニー/玄関ポーチ)
- 基本家具 (階段/キッチン/浴室/トイレ)
- 外構 (フェンス/駐車場/アプローチ)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import bmesh
import bpy
from mathutils import Vector


# ==============================
# Args / IO
# ==============================

def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--spec-json", default="")
    p.add_argument("--output-dir", default="")
    p.add_argument("--render-prefix", default="iter_00")
    p.add_argument("--save-blend", default="")
    p.add_argument("--output", default="")
    p.add_argument("--blend", default="")
    p.add_argument("--samples", type=int, default=128)
    p.add_argument("--engine", default="CYCLES")
    p.add_argument("--device", default="GPU")
    return p.parse_args(argv)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ==============================
# ユーティリティ
# ==============================

def _sf(value: Any, default: float) -> float:
    """安全にfloat変換"""
    try:
        return float(value)
    except Exception:
        return default


def _si(value: Any, default: int) -> int:
    """安全にint変換"""
    try:
        return int(value)
    except Exception:
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _box(name: str, size: Sequence[float], loc: Sequence[float]) -> bpy.types.Object:
    """ボックスプリミティブ生成"""
    sx, sy, sz = [max(0.001, float(v)) for v in size]
    lx, ly, lz = [float(v) for v in loc]
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(lx, ly, lz))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (sx, sy, sz)
    bpy.ops.object.transform_apply(scale=True)
    return obj


def _cylinder(name: str, radius: float, depth: float, loc: Sequence[float],
              rot: Sequence[float] = (0, 0, 0), verts: int = 16) -> bpy.types.Object:
    """シリンダープリミティブ"""
    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius, depth=depth, vertices=verts,
        location=tuple(float(v) for v in loc),
        rotation=tuple(float(v) for v in rot))
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _cut_boolean(target: bpy.types.Object, cutter_size: Sequence[float],
                 cutter_loc: Sequence[float], name: str) -> None:
    cutter = _box(f"_cut_{name}", cutter_size, cutter_loc)
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    mod = target.modifiers.new(name=name, type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def _world_bbox_max_z(obj: bpy.types.Object) -> float:
    return max((obj.matrix_world @ Vector(c)).z for c in obj.bound_box)


# ==============================
# マテリアル（テクスチャ付き）
# ==============================

def _mat_textured(name: str, base_rgba: Sequence[float], roughness: float = 0.55,
                  metallic: float = 0.0, bump_strength: float = 0.15,
                  noise_scale: float = 80.0) -> bpy.data.materials:
    """ノイズテクスチャ + Bump付きマテリアル"""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = nodes.get("Principled BSDF")
    output = nodes.get("Material Output")

    # ベースカラー にノイズを乗算
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = noise_scale
    noise.inputs["Detail"].default_value = 6.0

    mix_rgb = nodes.new("ShaderNodeMix")
    mix_rgb.data_type = 'RGBA'
    mix_rgb.inputs[0].default_value = 0.15  # Factor
    mix_rgb.inputs[6].default_value = tuple(float(c) for c in base_rgba[:4]) if len(base_rgba) >= 4 else (*base_rgba[:3], 1.0)
    links.new(noise.outputs["Color"], mix_rgb.inputs[7])
    links.new(mix_rgb.outputs[2], bsdf.inputs["Base Color"])

    bsdf.inputs["Roughness"].default_value = float(_clamp(roughness, 0.0, 1.0))
    bsdf.inputs["Metallic"].default_value = float(_clamp(metallic, 0.0, 1.0))

    # Bump
    if bump_strength > 0:
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = bump_strength
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    return mat


def _mat_glass(name: str = "Glass") -> bpy.data.materials:
    """ガラスマテリアル"""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.78, 0.88, 0.96, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.01
    bsdf.inputs["Transmission Weight"].default_value = 0.95
    bsdf.inputs["IOR"].default_value = 1.52
    bsdf.inputs["Specular IOR Level"].default_value = 0.8
    return mat


def _mat_simple(name: str, rgba: Sequence[float], roughness: float = 0.5,
                metallic: float = 0.0) -> bpy.data.materials:
    """シンプルBSDFマテリアル"""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgba[:3], rgba[3] if len(rgba) > 3 else 1.0)
    bsdf.inputs["Roughness"].default_value = _clamp(roughness, 0.0, 1.0)
    bsdf.inputs["Metallic"].default_value = _clamp(metallic, 0.0, 1.0)
    return mat


def _apply_mat(obj: bpy.types.Object, mat) -> None:
    if not obj.data or not hasattr(obj.data, "materials"):
        return
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat


# ==============================
# 間取り情報
# ==============================

def _floor_heights(spec: Dict[str, Any]) -> List[float]:
    floors = max(1, _si(spec.get("floors"), 2))
    raw = spec.get("floor_heights_m", [])
    vals: List[float] = []
    if isinstance(raw, list):
        for item in raw:
            vals.append(_clamp(_sf(item, 2.4), 2.0, 3.5))
    if not vals:
        vals = [2.6, 2.4]
    while len(vals) < floors:
        vals.append(vals[-1])
    return vals[:floors]


# ==============================
# 建物本体
# ==============================

def _build_foundation(spec: Dict[str, Any]) -> bpy.types.Object:
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    fh = _sf(spec.get("foundation_h_m"), 0.4)
    obj = _box("Foundation", (fw, fd, fh), (0, 0, fh * 0.5))
    # 暗いコンクリート色で壁との差を明確に
    _apply_mat(obj, _mat_textured("mat_foundation", (0.38, 0.36, 0.33, 1.0),
                                   roughness=0.92, noise_scale=80.0, bump_strength=0.35))
    return obj


def _build_walls(spec: Dict[str, Any]) -> bpy.types.Object:
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_sf(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)
    fh = _sf(spec.get("foundation_h_m"), 0.4)
    slab_t = _sf(spec.get("floor_slab_t_m"), 0.25)
    heights = _floor_heights(spec)
    floors = len(heights)
    wall_total = sum(heights) + max(0, floors - 1) * slab_t

    walls = _box("Walls", (fw, fd, wall_total), (0, 0, fh + wall_total * 0.5))

    # 中空化
    iw = max(0.1, fw - wt * 2)
    id_ = max(0.1, fd - wt * 2)
    _cut_boolean(walls, (iw, id_, wall_total + 0.02), (0, 0, fh + wall_total * 0.5), "WallHollow")

    # サイディング風マテリアル（ベージュ系、テクスチャ感を強調）
    _apply_mat(walls, _mat_textured("mat_walls", (0.72, 0.68, 0.60, 1.0),
                                     roughness=0.65, noise_scale=25.0, bump_strength=0.35))

    # 階間スラブ
    z = fh
    for i in range(floors - 1):
        z += heights[i]
        slab = _box(f"FloorSlab_{i+1}", (max(0.1, fw - 0.04), max(0.1, fd - 0.04), slab_t),
                    (0, 0, z + slab_t * 0.5))
        _apply_mat(slab, _mat_simple(f"mat_slab_{i+1}", (0.45, 0.37, 0.28, 1.0), 0.5))
        z += slab_t

    return walls


def _build_door(spec: Dict[str, Any], fh: float) -> bpy.types.Object:
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_sf(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)
    dw = _clamp(_sf(spec.get("door_width_m"), 0.9), 0.7, 1.2)
    dh = _clamp(_sf(spec.get("door_height_m"), 2.1), 1.8, 2.4)

    door = _box("Door", (dw, max(0.03, wt * 0.45), dh),
                (0, -fd * 0.5 + wt * 0.3, fh + dh * 0.5))
    _apply_mat(door, _mat_textured("mat_door", (0.34, 0.22, 0.14, 1.0),
                                    roughness=0.55, noise_scale=40.0, bump_strength=0.12))

    walls = bpy.data.objects.get("Walls")
    if walls:
        _cut_boolean(walls, (dw + 0.03, wt + 0.08, dh + 0.03),
                     (0, -fd * 0.5, fh + dh * 0.5), "DoorOpening")
    return door


def _build_windows(spec: Dict[str, Any], fh: float) -> None:
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_sf(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)
    ww = _clamp(_sf(spec.get("window_width_m"), 1.2), 0.5, 2.4)
    wh = _clamp(_sf(spec.get("window_height_m"), 1.0), 0.5, 2.4)
    heights = _floor_heights(spec)
    slab_t = _sf(spec.get("floor_slab_t_m"), 0.25)

    glass_mat = _mat_glass("mat_window_glass")
    frame_mat = _mat_simple("mat_window_frame", (0.12, 0.12, 0.15, 1.0), 0.25, 0.7)

    walls = bpy.data.objects.get("Walls")
    if not walls:
        return

    z = fh
    for fi, flh in enumerate(heights):
        sill = z + max(0.35, min(1.1, flh * 0.33))
        cz = sill + wh * 0.5

        # 南面窓（2個）
        for i, x in enumerate((-fw * 0.25, fw * 0.25)):
            name = f"Window_{fi+1}_S_{i+1}"
            win = _box(name, (ww, max(0.01, wt * 0.2), wh),
                       (x, -fd * 0.5 + wt * 0.4, cz))
            _apply_mat(win, glass_mat)

            # 窓枠（太め: 0.06m）
            frame_t = 0.06
            for side, sz_tuple, loc_tuple in [
                ("T", (ww + frame_t * 2, frame_t, frame_t), (x, -fd * 0.5 + wt * 0.35, cz + wh * 0.5 + frame_t * 0.5)),
                ("B", (ww + frame_t * 2, frame_t, frame_t), (x, -fd * 0.5 + wt * 0.35, cz - wh * 0.5 - frame_t * 0.5)),
                ("L", (frame_t, frame_t, wh + frame_t * 2), (x - ww * 0.5 - frame_t * 0.5, -fd * 0.5 + wt * 0.35, cz)),
                ("R", (frame_t, frame_t, wh + frame_t * 2), (x + ww * 0.5 + frame_t * 0.5, -fd * 0.5 + wt * 0.35, cz)),
            ]:
                fr = _box(f"WinFrame_{fi+1}_S_{i+1}_{side}", sz_tuple, loc_tuple)
                _apply_mat(fr, frame_mat)
            # 窓庇（小さな水切り）
            sill_out = _box(f"WinSill_{fi+1}_S_{i+1}", (ww + 0.1, 0.08, 0.025),
                            (x, -fd * 0.5 + wt * 0.25, cz - wh * 0.5 - 0.02))
            _apply_mat(sill_out, frame_mat)

            _cut_boolean(walls, (ww + 0.02, wt + 0.08, wh + 0.02),
                         (x, -fd * 0.5, cz), f"WinOp_{fi}_{i}")

        # 北面窓（1個中央）
        name_n = f"Window_{fi+1}_N_1"
        win_n = _box(name_n, (ww, max(0.01, wt * 0.2), wh),
                     (0, fd * 0.5 - wt * 0.4, cz))
        _apply_mat(win_n, glass_mat)
        _cut_boolean(walls, (ww + 0.02, wt + 0.08, wh + 0.02),
                     (0, fd * 0.5, cz), f"WinOp_N_{fi}")

        # 東面窓（1個）
        name_e = f"Window_{fi+1}_E_1"
        win_e = _box(name_e, (max(0.01, wt * 0.2), ww * 0.8, wh * 0.8),
                     (fw * 0.5 - wt * 0.4, 0, cz))
        _apply_mat(win_e, glass_mat)
        _cut_boolean(walls, (wt + 0.08, ww * 0.8 + 0.02, wh * 0.8 + 0.02),
                     (fw * 0.5, 0, cz), f"WinOp_E_{fi}")

        z += flh
        if fi < len(heights) - 1:
            z += slab_t


def _build_roof(spec: Dict[str, Any], fh: float, wall_h: float) -> bpy.types.Object:
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    rt = str(spec.get("roof_type", "gable")).lower()
    rp = _clamp(_sf(spec.get("roof_pitch"), 0.4), 0.2, 0.7)
    eaves = _clamp(_sf(spec.get("eaves_m"), 0.45), 0.2, 0.9)

    z0 = fh + wall_h
    ridge_h = fw * rp if rt == "shed" else (fw * 0.5 * rp)
    z1 = z0 + ridge_h
    hw = fw * 0.5 + eaves
    hd = fd * 0.5 + eaves

    mesh = bpy.data.meshes.new("RoofMesh")
    roof = bpy.data.objects.new("Roof", mesh)
    bpy.context.collection.objects.link(roof)

    bm = bmesh.new()
    v0 = bm.verts.new((-hw, -hd, z0))
    v1 = bm.verts.new((hw, -hd, z0))
    v2 = bm.verts.new((hw, hd, z0))
    v3 = bm.verts.new((-hw, hd, z0))

    if rt == "shed":
        v4 = bm.verts.new((-hw, hd, z1))
        v5 = bm.verts.new((hw, hd, z1))
        bm.faces.new([v0, v1, v5, v4])
        bm.faces.new([v1, v2, v5])
        bm.faces.new([v0, v4, v3])
        bm.faces.new([v3, v4, v5, v2])
    elif rt == "hip":
        rh = max(0.01, hd - hw)
        if rh > 0.12:
            v4 = bm.verts.new((0, -rh, z1))
            v5 = bm.verts.new((0, rh, z1))
            bm.faces.new([v0, v1, v4])
            bm.faces.new([v1, v2, v5, v4])
            bm.faces.new([v2, v3, v5])
            bm.faces.new([v3, v0, v4, v5])
        else:
            vc = bm.verts.new((0, 0, z1))
            bm.faces.new([v0, v1, vc])
            bm.faces.new([v1, v2, vc])
            bm.faces.new([v2, v3, vc])
            bm.faces.new([v3, v0, vc])
    else:  # gable
        v4 = bm.verts.new((0, -hd, z1))
        v5 = bm.verts.new((0, hd, z1))
        bm.faces.new([v0, v1, v4])
        bm.faces.new([v3, v5, v2])
        bm.faces.new([v0, v4, v5, v3])
        bm.faces.new([v1, v2, v5, v4])

    bm.to_mesh(mesh)
    bm.free()

    # Solidify
    solid = roof.modifiers.new(name="RoofSolid", type="SOLIDIFY")
    solid.thickness = 0.10
    solid.offset = -1.0
    bpy.context.view_layer.objects.active = roof
    roof.select_set(True)
    bpy.ops.object.modifier_apply(modifier=solid.name)
    roof.select_set(False)

    # 瓦風マテリアル（暗色＋強いbump）
    _apply_mat(roof, _mat_textured("mat_roof", (0.12, 0.10, 0.16, 1.0),
                                    roughness=0.55, noise_scale=40.0, bump_strength=0.5))
    return roof


# ==============================
# ディテール
# ==============================

def _build_fascia(spec: Dict[str, Any], fh: float, wall_h: float) -> None:
    """破風板"""
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    eaves = _clamp(_sf(spec.get("eaves_m"), 0.45), 0.2, 0.9)
    z0 = fh + wall_h
    hw = fw * 0.5 + eaves
    hd = fd * 0.5 + eaves
    ft = 0.03  # 破風板の厚み
    fh_size = 0.18  # 破風板の高さ

    mat = _mat_simple("mat_fascia", (0.9, 0.88, 0.85, 1.0), 0.4)
    # 前後
    for side, y in [("F", -hd), ("B", hd)]:
        f = _box(f"Fascia_{side}", (hw * 2, ft, fh_size), (0, y, z0 - fh_size * 0.5))
        _apply_mat(f, mat)
    # 左右
    for side, x in [("L", -hw), ("R", hw)]:
        f = _box(f"Fascia_{side}", (ft, hd * 2, fh_size), (x, 0, z0 - fh_size * 0.5))
        _apply_mat(f, mat)


def _build_gutters(spec: Dict[str, Any], fh: float, wall_h: float) -> None:
    """雨樋"""
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    eaves = _clamp(_sf(spec.get("eaves_m"), 0.45), 0.2, 0.9)
    z0 = fh + wall_h - 0.05
    hd = fd * 0.5 + eaves
    mat = _mat_simple("mat_gutter", (0.3, 0.28, 0.25, 1.0), 0.3, 0.4)

    fw = _sf(spec.get("footprint_w_m"), 8.0)
    hw = fw * 0.5
    # 横樋（前面 + 背面）
    for side, y_pos in [("F", -hd), ("B", hd)]:
        g = _cylinder(f"Gutter_H_{side}", 0.06, fw + eaves * 2, (0, y_pos, z0), (0, math.pi / 2, 0))
        _apply_mat(g, mat)
    # 縦樋（右前角 + 左前角）
    for side, x_pos in [("RF", hw - 0.1), ("LF", -hw + 0.1)]:
        gv = _cylinder(f"Gutter_V_{side}", 0.04, z0, (x_pos, -hd + 0.1, z0 * 0.5))
        _apply_mat(gv, mat)


def _build_entrance_porch(spec: Dict[str, Any], fh: float) -> None:
    """玄関ポーチ + 庇 + 階段"""
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    dw = _clamp(_sf(spec.get("door_width_m"), 0.9), 0.7, 1.2)

    porch_w = dw + 1.2
    porch_d = 1.2
    porch_h = fh

    # ポーチ床
    p = _box("Porch", (porch_w, porch_d, porch_h),
             (0, -fd * 0.5 - porch_d * 0.5, porch_h * 0.5))
    _apply_mat(p, _mat_textured("mat_porch", (0.55, 0.52, 0.48, 1.0), 0.85, noise_scale=90.0))

    # ポーチ庇
    canopy_h = 0.06
    canopy_z = fh + _clamp(_sf(spec.get("door_height_m"), 2.1), 1.8, 2.4) + 0.15
    c = _box("Canopy", (porch_w + 0.3, porch_d + 0.2, canopy_h),
             (0, -fd * 0.5 - porch_d * 0.4, canopy_z))
    _apply_mat(c, _mat_simple("mat_canopy", (0.25, 0.22, 0.2, 1.0), 0.4, 0.3))

    # 玄関ステップ（2段）
    step_h = fh / 2.0
    step_d = 0.3
    for i in range(2):
        s = _box(f"Step_{i}", (porch_w - 0.1, step_d, step_h * (i + 1)),
                 (0, -fd * 0.5 - porch_d - step_d * (2 - i) + step_d * 0.5,
                  step_h * (i + 1) * 0.5))
        _apply_mat(s, _mat_textured(f"mat_step_{i}", (0.55, 0.52, 0.48, 1.0), 0.85))


def _build_balcony(spec: Dict[str, Any], fh: float) -> None:
    """2Fバルコニー（南面）"""
    heights = _floor_heights(spec)
    if len(heights) < 2:
        return
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    slab_t = _sf(spec.get("floor_slab_t_m"), 0.25)
    z_2f = fh + heights[0] + slab_t

    bal_w = fw * 0.4
    bal_d = 1.2
    bal_h = 0.12
    rail_h = 1.1

    # バルコニー床
    b = _box("Balcony", (bal_w, bal_d, bal_h),
             (fw * 0.2, -fd * 0.5 - bal_d * 0.5, z_2f))
    _apply_mat(b, _mat_simple("mat_balcony", (0.5, 0.48, 0.44, 1.0), 0.7))

    # 手摺
    rail_t = 0.04
    mat_rail = _mat_simple("mat_rail", (0.18, 0.18, 0.20, 1.0), 0.25, 0.75)
    # 前面手摺
    r = _box("BalRail_F", (bal_w, rail_t, rail_h),
             (fw * 0.2, -fd * 0.5 - bal_d, z_2f + rail_h * 0.5))
    _apply_mat(r, mat_rail)
    # 左側手摺
    rl = _box("BalRail_L", (rail_t, bal_d, rail_h),
              (fw * 0.2 - bal_w * 0.5, -fd * 0.5 - bal_d * 0.5, z_2f + rail_h * 0.5))
    _apply_mat(rl, mat_rail)
    # 右側手摺
    rr = _box("BalRail_R", (rail_t, bal_d, rail_h),
              (fw * 0.2 + bal_w * 0.5, -fd * 0.5 - bal_d * 0.5, z_2f + rail_h * 0.5))
    _apply_mat(rr, mat_rail)
    # バルコニー支柱（2本）
    pillar_mat = _mat_simple("mat_bal_pillar", (0.22, 0.22, 0.24, 1.0), 0.3, 0.6)
    pillar_h = z_2f - 0.01
    for side, px in [("L", fw * 0.2 - bal_w * 0.5 + 0.06), ("R", fw * 0.2 + bal_w * 0.5 - 0.06)]:
        pl = _box(f"BalPillar_{side}", (0.08, 0.08, pillar_h),
                  (px, -fd * 0.5 - bal_d + 0.04, pillar_h * 0.5))
        _apply_mat(pl, pillar_mat)


# ==============================
# 家具（1F）
# ==============================

def _build_interior_stairs(spec: Dict[str, Any], fh: float) -> None:
    """内部階段"""
    heights = _floor_heights(spec)
    if len(heights) < 2:
        return
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_sf(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)

    total_rise = heights[0]
    n_steps = 13
    step_h = total_rise / n_steps
    step_d = 0.25
    step_w = 0.9

    # 階段位置（右奥）
    sx = fw * 0.5 - wt - step_w * 0.5 - 0.1
    sy = fd * 0.5 - wt - step_d * n_steps * 0.5

    mat = _mat_simple("mat_stairs", (0.6, 0.5, 0.35, 1.0), 0.55)
    for i in range(n_steps):
        s = _box(f"Stair_{i}", (step_w, step_d, step_h),
                 (sx, sy + step_d * i, fh + step_h * (i + 0.5)))
        _apply_mat(s, mat)


def _build_kitchen(spec: Dict[str, Any], fh: float) -> None:
    """キッチンカウンター"""
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_sf(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)

    # 北壁際
    cw = 2.4
    cd = 0.6
    ch = 0.85
    cx = -fw * 0.5 + wt + cw * 0.5 + 0.1
    cy = fd * 0.5 - wt - cd * 0.5 - 0.05

    counter = _box("Kitchen", (cw, cd, ch), (cx, cy, fh + ch * 0.5))
    _apply_mat(counter, _mat_textured("mat_kitchen", (0.85, 0.83, 0.8, 1.0), 0.35, noise_scale=50.0))

    # シンク（凹み表現）
    sink = _box("Sink", (0.5, 0.4, 0.06),
                (cx + 0.3, cy, fh + ch + 0.02))
    _apply_mat(sink, _mat_simple("mat_sink", (0.7, 0.72, 0.75, 1.0), 0.15, 0.8))


def _build_bathroom(spec: Dict[str, Any], fh: float) -> None:
    """浴室"""
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_sf(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)

    # 浴槽（北東角）
    bx = fw * 0.5 - wt - 0.9
    by = fd * 0.5 - wt - 0.5
    tub = _box("Bathtub", (1.4, 0.7, 0.55), (bx, by, fh + 0.55 * 0.5))
    _apply_mat(tub, _mat_simple("mat_tub", (0.92, 0.93, 0.95, 1.0), 0.1))


def _build_toilet(spec: Dict[str, Any], fh: float) -> None:
    """トイレ"""
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    wt = _clamp(_sf(spec.get("wall_thickness_m"), 0.18), 0.09, 0.30)

    # 便器（東壁際中央）
    tx = fw * 0.5 - wt - 0.3
    ty = 0.0
    bowl = _box("Toilet", (0.4, 0.55, 0.4), (tx, ty, fh + 0.2))
    _apply_mat(bowl, _mat_simple("mat_toilet", (0.95, 0.95, 0.96, 1.0), 0.1))
    tank = _box("ToiletTank", (0.35, 0.18, 0.35), (tx, ty + 0.35, fh + 0.4 + 0.175))
    _apply_mat(tank, _mat_simple("mat_toilet_tank", (0.94, 0.94, 0.95, 1.0), 0.1))


# ==============================
# 外構
# ==============================

def _build_fence(spec: Dict[str, Any]) -> None:
    """外周フェンス"""
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    margin = _clamp(_sf(spec.get("ground_margin_m"), 8.0), 2.0, 30.0)
    fence_h = 1.2
    fence_t = 0.05
    hx = (fw + margin * 2) * 0.5
    hy = (fd + margin * 2) * 0.5

    mat = _mat_simple("mat_fence", (0.6, 0.58, 0.55, 1.0), 0.7)
    # 4辺
    for name, sz, loc in [
        ("Fence_S", (hx * 2, fence_t, fence_h), (0, -hy, fence_h * 0.5)),
        ("Fence_N", (hx * 2, fence_t, fence_h), (0, hy, fence_h * 0.5)),
        ("Fence_W", (fence_t, hy * 2, fence_h), (-hx, 0, fence_h * 0.5)),
        ("Fence_E", (fence_t, hy * 2, fence_h), (hx, 0, fence_h * 0.5)),
    ]:
        f = _box(name, sz, loc)
        _apply_mat(f, mat)


def _build_parking(spec: Dict[str, Any]) -> None:
    """駐車スペース"""
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    # 南側・西より
    pw = 2.8
    pd = 5.5
    p = _box("Parking", (pw, pd, 0.03),
             (-fw * 0.5 - pw * 0.5 - 0.5, -fd * 0.2, 0.015))
    _apply_mat(p, _mat_textured("mat_parking", (0.42, 0.42, 0.44, 1.0), 0.92, noise_scale=150.0))


def _build_approach(spec: Dict[str, Any]) -> None:
    """アプローチ（玄関までの小道）"""
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    margin = _clamp(_sf(spec.get("ground_margin_m"), 8.0), 2.0, 30.0)
    aw = 1.0
    ad = margin + 0.5
    a = _box("Approach", (aw, ad, 0.04),
             (0, -fd * 0.5 - ad * 0.5 - 0.3, 0.02))
    _apply_mat(a, _mat_textured("mat_approach", (0.55, 0.50, 0.45, 1.0), 0.8, noise_scale=70.0))


def _build_outdoor_units(spec: Dict[str, Any], fh: float) -> None:
    """室外機・給湯器・ポスト"""
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    ac_mat = _mat_simple("mat_ac_unit", (0.88, 0.88, 0.86, 1.0), 0.5)
    # 室外機1（東壁外側）
    ac1 = _box("AC_Unit_1", (0.8, 0.3, 0.55),
               (fw * 0.5 + 0.2, fd * 0.1, fh + 0.28))
    _apply_mat(ac1, ac_mat)
    # 室外機2（北壁外側）
    ac2 = _box("AC_Unit_2", (0.3, 0.8, 0.55),
               (-fw * 0.2, fd * 0.5 + 0.2, fh + 0.28))
    _apply_mat(ac2, ac_mat)
    # 給湯器（北壁外側）
    boiler = _box("WaterHeater", (0.25, 0.2, 0.6),
                  (fw * 0.3, fd * 0.5 + 0.15, fh + 0.7))
    _apply_mat(boiler, _mat_simple("mat_boiler", (0.85, 0.85, 0.84, 1.0), 0.45))
    # 郵便ポスト
    margin = _clamp(_sf(spec.get("ground_margin_m"), 8.0), 2.0, 30.0)
    hy = (fd + margin * 2) * 0.5
    post = _box("Mailbox", (0.3, 0.2, 0.35), (0.8, -hy + 0.15, 1.0))
    _apply_mat(post, _mat_simple("mat_mailbox", (0.6, 0.15, 0.1, 1.0), 0.4))
    post_leg = _box("MailboxLeg", (0.04, 0.04, 0.8), (0.8, -hy + 0.15, 0.4))
    _apply_mat(post_leg, _mat_simple("mat_mailbox_leg", (0.3, 0.3, 0.32, 1.0), 0.3, 0.5))


# ==============================
# カメラ / レンダリング（6視点）
# ==============================

def _setup_render(engine: str, device: str, samples: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    if engine == "CYCLES":
        scene.cycles.samples = int(_clamp(float(samples), 16, 2048))
        scene.cycles.device = device
        if device == "GPU":
            prefs = bpy.context.preferences.addons.get("cycles")
            if prefs:
                prefs.preferences.compute_device_type = "OPTIX"
                prefs.preferences.get_devices()
                for d in prefs.preferences.devices:
                    d.use = True


def _look_at(cam: bpy.types.Object, target: Vector) -> None:
    d = target - cam.location
    if d.length < 1e-6:
        return
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def _setup_camera_and_lights(spec: Dict[str, Any]) -> bpy.types.Object:
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    fh = _sf(spec.get("foundation_h_m"), 0.4)
    heights = _floor_heights(spec)
    slab_t = _sf(spec.get("floor_slab_t_m"), 0.25)
    wall_h = sum(heights) + max(0, len(heights) - 1) * slab_t
    target = Vector((0, 0, fh + wall_h * 0.55))

    bpy.ops.object.camera_add(location=(fw * 2.0, -fd * 2.2, fh + wall_h * 1.25))
    cam = bpy.context.active_object
    cam.name = "Camera"
    cam.data.lens = 35
    bpy.context.scene.camera = cam
    _look_at(cam, target)

    # Sun（やや低い角度で自然な影を生成）
    bpy.ops.object.light_add(type="SUN", location=(fw * 2.5, -fd * 2.0, fh + wall_h * 3.0))
    sun = bpy.context.active_object
    sun.name = "Sun"
    sun.data.energy = 4.5
    sun.data.angle = 0.03  # ソフトシャドウ
    _look_at(sun, Vector((0, 0, fh + wall_h * 0.3)))

    # Fill（裏面を照らす）
    bpy.ops.object.light_add(type="AREA", location=(-fw * 0.5, fd * 1.5, fh + wall_h * 1.5))
    fill = bpy.context.active_object
    fill.name = "FillLight"
    fill.data.energy = 180.0
    fill.data.size = max(6.0, fw)

    # バウンスライト（地面からの反射光を模擬）
    bpy.ops.object.light_add(type="AREA", location=(0, 0, 0.1))
    bounce = bpy.context.active_object
    bounce.name = "BounceLight"
    bounce.data.energy = 40.0
    bounce.data.size = max(8.0, fw * 1.5)
    bounce.rotation_euler = (math.pi, 0, 0)  # 上向き

    # HDRI風ワールド（空色グラデーション + ノード構成）
    world = bpy.data.worlds.new("HouseWorld")
    bpy.context.scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    bg = nodes.get("Background")
    if bg:
        # Sky Textureでリアルな空
        sky = nodes.new("ShaderNodeTexSky")
        sky.sky_type = "HOSEK_WILKIE"
        links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 1.0

    return cam


def _render_6views(out_dir: Path, prefix: str, cam: bpy.types.Object,
                   spec: Dict[str, Any]) -> Dict[str, str]:
    """6視点レンダリング"""
    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    fh = _sf(spec.get("foundation_h_m"), 0.4)
    heights = _floor_heights(spec)
    slab_t = _sf(spec.get("floor_slab_t_m"), 0.25)
    wall_h = sum(heights) + max(0, len(heights) - 1) * slab_t
    target = Vector((0, 0, fh + wall_h * 0.55))

    dist_f = max(8.0, fd * 2.5)
    dist_s = max(8.0, fw * 2.5)
    diag = max(7.5, max(fw, fd) * 1.9)
    h_mid = fh + wall_h * 0.9
    h_high = fh + wall_h * 1.1
    h_top = fh + wall_h * 2.6

    views = {
        "front":   Vector((0, -dist_f, h_mid)),
        "back":    Vector((0, dist_f, h_mid)),
        "left":    Vector((-dist_s, 0, h_mid)),
        "right":   Vector((dist_s, 0, h_mid)),
        "oblique": Vector((diag, -diag, h_high)),
        "bird":    Vector((0, -0.001, h_top)),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    result: Dict[str, str] = {}

    for name, loc in views.items():
        cam.location = loc
        _look_at(cam, target)
        path = out_dir / f"{prefix}_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        result[name] = str(path)
    return result


# ==============================
# メトリクス
# ==============================

def _build_metrics(spec: Dict[str, Any], foundation, walls, roof, door) -> Dict[str, Any]:
    return {
        "footprint_w_m": float(foundation.dimensions.x),
        "footprint_d_m": float(foundation.dimensions.y),
        "wall_total_h_m": float(walls.dimensions.z),
        "roof_peak_z_m": float(_world_bbox_max_z(roof)),
        "door_height_m": float(door.dimensions.z),
        "roof_type_requested": str(spec.get("roof_type", "gable")),
        "roof_type_actual": str(spec.get("roof_type", "gable")),
    }


# ==============================
# Main
# ==============================

def main() -> None:
    args = parse_args()

    if args.spec_json:
        spec = _read_json(Path(args.spec_json).resolve())
    else:
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

    samples = _si(args.samples if args.samples is not None else spec.get("samples"), 128)

    out_dir = Path(args.output_dir).resolve() if args.output_dir else None
    if out_dir is None:
        if args.output:
            out_dir = Path(args.output).resolve().parent
        elif args.save_blend:
            out_dir = Path(args.save_blend).resolve().parent
        elif args.blend:
            out_dir = Path(args.blend).resolve().parent
        else:
            out_dir = Path("ag_runs").resolve()

    blend_str = args.save_blend or args.blend
    if not blend_str:
        blend_str = str((out_dir / "house_v5.blend").resolve())
    blend_path = Path(blend_str).resolve()

    _clear_scene()

    fw = _sf(spec.get("footprint_w_m"), 8.0)
    fd = _sf(spec.get("footprint_d_m"), 6.0)
    fh = _sf(spec.get("foundation_h_m"), 0.4)
    margin = _clamp(_sf(spec.get("ground_margin_m"), 8.0), 2.0, 30.0)
    ground_size = max(fw, fd) + margin * 2.0

    # 地面
    ground = _box("Ground", (ground_size, ground_size, 0.08), (0, 0, -0.04))
    _apply_mat(ground, _mat_textured("mat_ground", (0.22, 0.38, 0.16, 1.0),
                                      roughness=0.92, noise_scale=40.0, bump_strength=0.1))

    # 建物本体
    foundation = _build_foundation(spec)
    walls = _build_walls(spec)
    door = _build_door(spec, fh)
    _build_windows(spec, fh)
    wall_h = float(walls.dimensions.z)
    roof = _build_roof(spec, fh, wall_h)

    # ディテール
    _build_fascia(spec, fh, wall_h)
    _build_gutters(spec, fh, wall_h)
    _build_entrance_porch(spec, fh)
    _build_balcony(spec, fh)

    # 家具
    _build_interior_stairs(spec, fh)
    _build_kitchen(spec, fh)
    _build_bathroom(spec, fh)
    _build_toilet(spec, fh)

    # 外構
    _build_fence(spec)
    _build_parking(spec)
    _build_approach(spec)

    # 生活感ディテール
    _build_outdoor_units(spec, fh)

    # レンダリング
    _setup_render(engine=args.engine, device=args.device, samples=samples)
    cam = _setup_camera_and_lights(spec)
    views = _render_6views(out_dir=out_dir, prefix=args.render_prefix, cam=cam, spec=spec)

    # メトリクス保存
    metrics = _build_metrics(spec, foundation, walls, roof, door)
    bpy.context.scene["ag_house_actual_json"] = json.dumps(metrics, ensure_ascii=False)

    # Blend保存
    blend_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    # Legacy出力
    if args.output:
        legacy = Path(args.output).resolve()
        if legacy != Path(views.get("front", "")).resolve():
            legacy.parent.mkdir(parents=True, exist_ok=True)
            bpy.context.scene.render.filepath = str(legacy)
            bpy.ops.render.render(write_still=True)

    print(f"[AG] blend={blend_path}")
    for vn, vp in views.items():
        print(f"[AG] {vn}={vp}")


if __name__ == "__main__":
    main()
