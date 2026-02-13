"""
build_house_v2.py - パラメトリック戸建て住宅モデリング

リサーチ結果を反映した改善版:
1. パラメトリック設計 - 全寸法を基準パラメータから算出（ズレ防止）
2. BMesh直接構築 - 屋根をbmeshで頂点レベル制御
3. 親子階層 - 全パーツをEmptyの子にしてグループ管理
4. ベベル + スムーズシェーディング - エッジの品質向上
5. 正しいオリジン設定 - 配置精度の向上
6. HDRI環境ライティング - リアルな空と光
7. 法線の再計算 - シェーディング不具合防止

使用例:
    blender --background --factory-startup --python build_house_v2.py -- --output render.png
"""

import sys
import math
import argparse
from pathlib import Path
from dataclasses import dataclass, field

import bpy
import bmesh
from mathutils import Vector


# ==============================================================
# 1. パラメトリック設計パラメータ（全寸法の唯一の定義元）
# ==============================================================

@dataclass
class HouseParams:
    """住宅の全寸法を一元管理するデータクラス"""
    # --- 全体配置 ---
    ground_size: float = 30.0      # 地面の一辺

    # --- 基礎 ---
    foundation_w: float = 10.0     # 基礎の幅（X）
    foundation_d: float = 8.0      # 基礎の奥行き（Y）
    foundation_h: float = 0.3      # 基礎の高さ

    # --- 壁 ---
    wall_thickness: float = 0.2    # 壁の厚み
    wall_h: float = 2.8            # 壁の高さ
    wall_inset: float = 0.1        # 壁を基礎から内側にオフセット

    # --- 屋根 ---
    roof_overhang: float = 0.6     # 軒の出
    roof_ridge_h: float = 2.0      # 棟の高さ（壁上端から）
    roof_thickness: float = 0.08   # 屋根の厚み

    # --- 窓 ---
    window_w: float = 1.2          # 窓の幅
    window_h: float = 1.0          # 窓の高さ
    window_sill_h: float = 0.9     # 窓台の高さ（壁の底面から）
    window_depth: float = 0.05     # ガラスの厚み
    window_frame_w: float = 0.06   # 窓枠の幅

    # --- ドア ---
    door_w: float = 1.0            # ドアの幅
    door_h: float = 2.2            # ドアの高さ
    door_depth: float = 0.06       # ドアの厚み

    # --- ポーチ ---
    porch_w: float = 2.0           # ポーチの幅
    porch_d: float = 1.2           # ポーチの奥行き
    porch_h: float = 0.15          # ポーチの高さ

    # --- フェンス ---
    fence_h: float = 0.8           # フェンスの高さ
    fence_offset: float = 3.0      # 建物からの距離
    fence_post_size: float = 0.06  # ポストの太さ

    # --- 煙突 ---
    chimney_w: float = 0.5         # 煙突の幅
    chimney_d: float = 0.5         # 煙突の奥行き
    chimney_h: float = 1.5         # 煙突の高さ（屋根からの突出）

    # ---- 計算済みプロパティ ----
    @property
    def wall_w(self) -> float:
        """壁の外寸幅"""
        return self.foundation_w - self.wall_inset * 2

    @property
    def wall_d(self) -> float:
        """壁の外寸奥行き"""
        return self.foundation_d - self.wall_inset * 2

    @property
    def foundation_top(self) -> float:
        """基礎の上面Z座標"""
        return self.foundation_h

    @property
    def wall_top(self) -> float:
        """壁の上面Z座標"""
        return self.foundation_top + self.wall_h

    @property
    def roof_peak(self) -> float:
        """棟の最高点Z座標"""
        return self.wall_top + self.roof_ridge_h

    @property
    def roof_half_w(self) -> float:
        """屋根の半幅（軒出含む）"""
        return self.wall_w / 2 + self.roof_overhang

    @property
    def roof_half_d(self) -> float:
        """屋根の半奥行き（軒出含む）"""
        return self.wall_d / 2 + self.roof_overhang


def parse_args():
    """コマンドライン引数"""
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    parser = argparse.ArgumentParser(description="パラメトリック住宅モデリング v2")
    parser.add_argument("--output", "-o", default="ag_runs/house_v2_render.png")
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--save-blend", default="ag_runs/house_v2.blend")
    return parser.parse_args(argv)


# ==============================================================
# 2. ユーティリティ関数
# ==============================================================

def clear_scene():
    """シーン完全初期化"""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    print("[AG] シーン初期化完了")


def make_mat(name: str, color: tuple, metallic=0.0, roughness=0.5,
             transmission=0.0, ior=1.45) -> bpy.types.Material:
    """Principled BSDFマテリアルを作成"""
    mat = bpy.data.materials.new(name=name)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color[:3], 1.0)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        if transmission > 0:
            bsdf.inputs["Transmission Weight"].default_value = transmission
            bsdf.inputs["IOR"].default_value = ior
    return mat


def assign_mat(obj, mat):
    """マテリアル割り当て"""
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def set_parent(child, parent):
    """親子関係を設定（ワールド位置を保持）"""
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()


def add_bevel(obj, width=0.02, segments=2):
    """ベベルモディファイアを追加"""
    mod = obj.modifiers.new(name="Bevel", type='BEVEL')
    mod.width = width
    mod.segments = segments
    mod.limit_method = 'ANGLE'
    mod.angle_limit = math.radians(60)


def set_smooth(obj):
    """スムーズシェーディング + Auto Smooth"""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.shade_smooth()
    # 法線の再計算
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)


def make_box(name: str, size: tuple, location: tuple, mat=None,
             bevel=True, smooth=True) -> bpy.types.Object:
    """汎用ボックス作成（ベベル + スムーズ対応）"""
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    bpy.ops.object.transform_apply(scale=True)
    if mat:
        assign_mat(obj, mat)
    if bevel:
        add_bevel(obj, width=0.015)
    if smooth:
        set_smooth(obj)
    return obj


# ==============================================================
# 3. マテリアルパレット（一括定義）
# ==============================================================

class MaterialPalette:
    """全マテリアルを一括管理"""
    def __init__(self):
        self.concrete = make_mat("Concrete", (0.58, 0.55, 0.52), roughness=0.9)
        self.wall = make_mat("WallPaint", (0.93, 0.89, 0.80), roughness=0.65)
        self.roof = make_mat("RoofTile", (0.40, 0.15, 0.10), roughness=0.55)
        self.door = make_mat("DoorWood", (0.38, 0.22, 0.12), roughness=0.55)
        self.brass = make_mat("Brass", (0.82, 0.68, 0.22), metallic=0.9, roughness=0.15)
        self.glass = make_mat("Glass", (0.75, 0.88, 0.95),
                              metallic=0.0, roughness=0.02, transmission=0.85)
        self.frame = make_mat("WindowFrame", (0.92, 0.92, 0.93), metallic=0.2, roughness=0.35)
        self.grass = make_mat("Grass", (0.22, 0.48, 0.18), roughness=0.95)
        self.path = make_mat("PathStone", (0.62, 0.58, 0.55), roughness=0.8)
        self.fence = make_mat("FenceWhite", (0.96, 0.94, 0.91), roughness=0.45)
        self.porch = make_mat("PorchStone", (0.55, 0.50, 0.48), roughness=0.75)
        self.brick = make_mat("Brick", (0.55, 0.28, 0.18), roughness=0.85)
        print("[AG] マテリアルパレット作成完了（12種）")


# ==============================================================
# 4. 住宅パーツ作成（全座標をHouseParamsから算出）
# ==============================================================

def make_ground(p: HouseParams, pal: MaterialPalette) -> bpy.types.Object:
    """地面"""
    obj = make_box("Ground", (p.ground_size, p.ground_size, 0.05),
                   (0, 0, -0.025), pal.grass, bevel=False, smooth=False)
    return obj


def make_foundation(p: HouseParams, pal: MaterialPalette) -> bpy.types.Object:
    """基礎"""
    z = p.foundation_h / 2
    obj = make_box("Foundation",
                   (p.foundation_w, p.foundation_d, p.foundation_h),
                   (0, 0, z), pal.concrete)
    print(f"[AG] 基礎: {p.foundation_w}x{p.foundation_d}x{p.foundation_h} @ z={z:.2f}")
    return obj


def make_wall_box(p: HouseParams, pal: MaterialPalette) -> bpy.types.Object:
    """壁体（外壁ボックス - 内部を空洞化）"""
    # 外箱
    z = p.foundation_top + p.wall_h / 2
    outer = make_box("Walls_Outer",
                     (p.wall_w, p.wall_d, p.wall_h),
                     (0, 0, z), pal.wall, bevel=False, smooth=False)

    # 内箱（くり抜き用）
    inner_w = p.wall_w - p.wall_thickness * 2
    inner_d = p.wall_d - p.wall_thickness * 2
    inner = make_box("Walls_Inner",
                     (inner_w, inner_d, p.wall_h + 0.1),
                     (0, 0, z), bevel=False, smooth=False)

    # Boolean差分で空洞化
    bpy.context.view_layer.objects.active = outer
    mod = outer.modifiers.new(name="Hollow", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = inner
    bpy.ops.object.modifier_apply(modifier="Hollow")
    bpy.data.objects.remove(inner, do_unlink=True)

    print(f"[AG] 壁体: 外寸{p.wall_w}x{p.wall_d}, 厚み{p.wall_thickness}")
    return outer


def cut_opening(walls_obj, position: tuple, size: tuple, name: str):
    """壁に開口部（窓/ドア）をBoolean差分で切り抜く"""
    cutter = make_box(f"{name}_cutter", size, position, bevel=False, smooth=False)
    bpy.context.view_layer.objects.active = walls_obj
    mod = walls_obj.modifiers.new(name=name, type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    bpy.ops.object.modifier_apply(modifier=name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def make_walls_with_openings(p: HouseParams, pal: MaterialPalette) -> bpy.types.Object:
    """壁体 + 窓/ドア開口部を作成"""
    walls = make_wall_box(p, pal)

    # 壁の各面の中心Y/X座標
    front_y = -p.wall_d / 2  # 正面（-Y側）
    back_y = p.wall_d / 2    # 背面（+Y側）
    left_x = -p.wall_w / 2   # 左面（-X側）
    right_x = p.wall_w / 2   # 右面（+X側）

    # 窓のZ中心
    win_z = p.foundation_top + p.window_sill_h + p.window_h / 2

    # 正面の窓（左右対称に2つ）
    win_offset_x = p.wall_w / 4  # 幅の1/4位置
    for side, sx in [("FL", -win_offset_x), ("FR", win_offset_x)]:
        cut_opening(walls,
                    (sx, front_y, win_z),
                    (p.window_w, p.wall_thickness + 0.1, p.window_h),
                    f"Window_{side}")

    # 側面の窓（各1つ中央）
    for side, x_pos in [("Left", left_x), ("Right", right_x)]:
        cut_opening(walls,
                    (x_pos, 0, win_z),
                    (p.wall_thickness + 0.1, p.window_w, p.window_h),
                    f"Window_{side}")

    # 背面の窓（中央に1つ、大きめ）
    cut_opening(walls,
                (0, back_y, win_z + 0.2),
                (p.window_w * 1.5, p.wall_thickness + 0.1, p.window_h * 0.8),
                "Window_Back")

    # 正面ドア（中央）
    door_z = p.foundation_top + p.door_h / 2
    cut_opening(walls,
                (0, front_y, door_z),
                (p.door_w, p.wall_thickness + 0.1, p.door_h),
                "Door_Opening")

    # ベベルとスムーズ適用
    add_bevel(walls, width=0.01, segments=1)
    set_smooth(walls)

    print("[AG] 壁開口部: 窓5箇所 + ドア1箇所")
    return walls


def make_windows(p: HouseParams, pal: MaterialPalette) -> list:
    """窓ガラス + 窓枠を作成"""
    created = []
    front_y = -p.wall_d / 2
    back_y = p.wall_d / 2
    left_x = -p.wall_w / 2
    right_x = p.wall_w / 2
    win_z = p.foundation_top + p.window_sill_h + p.window_h / 2
    win_offset_x = p.wall_w / 4

    # 窓定義: (名前, 位置, ガラスサイズ, 枠方向)
    windows = [
        ("FL", (- win_offset_x, front_y, win_z),
         (p.window_w - 0.08, p.window_depth, p.window_h - 0.08), "Y"),
        ("FR", (win_offset_x, front_y, win_z),
         (p.window_w - 0.08, p.window_depth, p.window_h - 0.08), "Y"),
        ("Left", (left_x, 0, win_z),
         (p.window_depth, p.window_w - 0.08, p.window_h - 0.08), "X"),
        ("Right", (right_x, 0, win_z),
         (p.window_depth, p.window_w - 0.08, p.window_h - 0.08), "X"),
        ("Back", (0, back_y, win_z + 0.2),
         (p.window_w * 1.5 - 0.08, p.window_depth, p.window_h * 0.8 - 0.08), "Y"),
    ]

    for name, pos, glass_size, orient in windows:
        # ガラス
        glass = make_box(f"Glass_{name}", glass_size, pos, pal.glass,
                        bevel=False, smooth=True)
        created.append(glass)

        # 窓枠（ガラスよりわずかに大きい）
        fw = p.window_frame_w
        if orient == "Y":
            frame_size = (glass_size[0] + fw * 2, glass_size[1] + 0.02, glass_size[2] + fw * 2)
        else:
            frame_size = (glass_size[0] + 0.02, glass_size[1] + fw * 2, glass_size[2] + fw * 2)
        frame = make_box(f"Frame_{name}", frame_size, pos, pal.frame,
                        bevel=True, smooth=True)
        created.append(frame)

        # 窓の桟（十字）
        if orient == "Y":
            # 縦桟
            h_bar = make_box(f"Bar_H_{name}",
                            (glass_size[0], 0.02, 0.03),
                            pos, pal.frame, bevel=False, smooth=False)
            v_bar = make_box(f"Bar_V_{name}",
                            (0.03, 0.02, glass_size[2]),
                            pos, pal.frame, bevel=False, smooth=False)
        else:
            h_bar = make_box(f"Bar_H_{name}",
                            (0.02, glass_size[1], 0.03),
                            pos, pal.frame, bevel=False, smooth=False)
            v_bar = make_box(f"Bar_V_{name}",
                            (0.02, 0.03, glass_size[2]),
                            pos, pal.frame, bevel=False, smooth=False)
        created.extend([h_bar, v_bar])

    print(f"[AG] 窓作成完了: {len(windows)}箇所（ガラス+枠+桟）")
    return created


def make_door(p: HouseParams, pal: MaterialPalette) -> list:
    """玄関ドア + ドアノブ"""
    front_y = -p.wall_d / 2
    door_z = p.foundation_top + p.door_h / 2

    # ドア本体（壁面よりわずかに奥）
    door = make_box("Door",
                    (p.door_w - 0.04, p.door_depth, p.door_h - 0.02),
                    (0, front_y + 0.01, door_z),
                    pal.door, bevel=True, smooth=True)

    # ドアノブ
    knob_x = p.door_w / 2 - 0.15
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=0.04,
        location=(knob_x, front_y - 0.03, p.foundation_top + 1.0)
    )
    knob = bpy.context.active_object
    knob.name = "DoorKnob"
    assign_mat(knob, pal.brass)
    set_smooth(knob)

    print("[AG] ドア作成完了")
    return [door, knob]


def make_roof_bmesh(p: HouseParams, pal: MaterialPalette) -> bpy.types.Object:
    """切妻屋根をBMeshで正確に構築（壁上端にぴったり接合）"""
    mesh = bpy.data.meshes.new("RoofMesh")
    obj = bpy.data.objects.new("Roof", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj

    bm = bmesh.new()

    hw = p.roof_half_w  # 半幅
    hd = p.roof_half_d  # 半奥行き
    wt = p.wall_top     # 壁上端Z
    pk = p.roof_peak    # 棟Z
    th = p.roof_thickness

    # ---- 外面の頂点（上面） ----
    # 前面（-Y）
    v0 = bm.verts.new((-hw, -hd, wt))        # 前左下
    v1 = bm.verts.new((hw, -hd, wt))         # 前右下
    v2 = bm.verts.new((0, -hd, pk))           # 前棟
    # 後面（+Y）
    v3 = bm.verts.new((-hw, hd, wt))         # 後左下
    v4 = bm.verts.new((hw, hd, wt))          # 後右下
    v5 = bm.verts.new((0, hd, pk))            # 後棟

    # ---- 内面の頂点（厚み分内側） ----
    inset = th / math.cos(math.atan2(p.roof_ridge_h, p.wall_w / 2 + p.roof_overhang))
    v0i = bm.verts.new((-hw + th, -hd + th, wt))
    v1i = bm.verts.new((hw - th, -hd + th, wt))
    v2i = bm.verts.new((0, -hd + th, pk - inset))
    v3i = bm.verts.new((-hw + th, hd - th, wt))
    v4i = bm.verts.new((hw - th, hd - th, wt))
    v5i = bm.verts.new((0, hd - th, pk - inset))

    # ---- 外側面 ----
    bm.faces.new([v0, v2, v5, v3])    # 左屋根面（外）
    bm.faces.new([v2, v1, v4, v5])    # 右屋根面（外）
    bm.faces.new([v0, v1, v2])        # 前妻面（外）
    bm.faces.new([v3, v5, v4])        # 後妻面（外）

    # ---- 内側面 ----
    bm.faces.new([v3i, v5i, v2i, v0i])  # 左屋根面（内）
    bm.faces.new([v5i, v4i, v1i, v2i])  # 右屋根面（内）
    bm.faces.new([v2i, v1i, v0i])       # 前妻面（内）
    bm.faces.new([v4i, v5i, v3i])       # 後妻面（内）

    # ---- 縁（外と内を接続）----
    bm.faces.new([v0, v3, v3i, v0i])   # 左下縁
    bm.faces.new([v1, v0, v0i, v1i])   # 前下縁
    bm.faces.new([v4, v1, v1i, v4i])   # 右下縁
    bm.faces.new([v3, v4, v4i, v3i])   # 後下縁

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    # 法線を外向きに統一
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)

    assign_mat(obj, pal.roof)
    set_smooth(obj)

    print(f"[AG] 切妻屋根: 半幅{hw:.1f}, 棟高{pk:.1f}, 壁上端{wt:.1f}")
    return obj


def make_chimney(p: HouseParams, pal: MaterialPalette) -> bpy.types.Object:
    """煙突（屋根の横に配置）"""
    # 屋根の斜面上、右寄りに配置
    chimney_x = p.wall_w / 4
    # 屋根斜面のZ座標を計算
    slope = p.roof_ridge_h / (p.wall_w / 2 + p.roof_overhang)
    dist_from_ridge = abs(chimney_x)
    roof_z_at_pos = p.roof_peak - slope * dist_from_ridge
    chimney_z = roof_z_at_pos + p.chimney_h / 2

    obj = make_box("Chimney",
                   (p.chimney_w, p.chimney_d, p.chimney_h + (roof_z_at_pos - p.wall_top)),
                   (chimney_x, 0, chimney_z),
                   pal.brick)

    print(f"[AG] 煙突: x={chimney_x:.1f}, z={chimney_z:.1f}")
    return obj


def make_porch(p: HouseParams, pal: MaterialPalette) -> list:
    """玄関ポーチ + ステップ（壁面にぴったり接合）"""
    front_y = -p.wall_d / 2
    porch_y = front_y - p.porch_d / 2
    porch_z = p.foundation_top / 2

    # ポーチ本体
    porch = make_box("Porch",
                     (p.porch_w, p.porch_d, p.porch_h),
                     (0, porch_y, porch_z),
                     pal.porch)

    # ステップ（ポーチの手前）
    step_y = porch_y - p.porch_d / 2 - 0.2
    step = make_box("PorchStep",
                    (p.porch_w * 0.85, 0.4, p.porch_h * 0.6),
                    (0, step_y, p.porch_h * 0.3),
                    pal.porch)

    print(f"[AG] ポーチ: y={porch_y:.2f}")
    return [porch, step]


def make_pathway(p: HouseParams, pal: MaterialPalette) -> bpy.types.Object:
    """小道（ポーチから前方へ）"""
    front_y = -p.wall_d / 2
    path_start = front_y - p.porch_d - 0.5
    path_length = p.fence_offset + 2.0
    path_y = path_start - path_length / 2

    obj = make_box("Pathway",
                   (1.2, path_length, 0.03),
                   (0, path_y, 0.015),
                   pal.path, bevel=False, smooth=False)

    print(f"[AG] 小道: y={path_y:.1f}, 長さ={path_length:.1f}")
    return obj


def make_fence(p: HouseParams, pal: MaterialPalette) -> list:
    """フェンス（建物前方に配置、正確な間隔）"""
    created = []
    front_y = -p.wall_d / 2
    fence_y = front_y - p.porch_d - p.fence_offset
    fence_width = p.wall_w + 2.0  # 建物幅 + 余裕

    # ポスト（等間隔で配置）
    post_spacing = 2.0
    gate_half = 0.7  # 門の半幅
    num_posts = int(fence_width / post_spacing) + 1
    post_xs = []

    for i in range(num_posts):
        x = -fence_width / 2 + i * post_spacing
        if abs(x) < gate_half:
            continue  # 門の位置にはポストを置かない
        post_xs.append(x)

    for i, x in enumerate(post_xs):
        post = make_box(f"FencePost_{i}",
                        (p.fence_post_size, p.fence_post_size, p.fence_h),
                        (x, fence_y, p.fence_h / 2),
                        pal.fence, bevel=True, smooth=True)
        created.append(post)

    # 横棒（ポスト間を接続、上下2段）
    for z_ratio in [0.3, 0.7]:
        rail_z = p.fence_h * z_ratio
        # 左セクション
        left_posts = [x for x in post_xs if x < -gate_half]
        if len(left_posts) >= 2:
            left_start = min(left_posts)
            left_end = max(left_posts)
            left_len = left_end - left_start
            left_cx = (left_start + left_end) / 2
            rail = make_box(f"FenceRail_L_{z_ratio}",
                           (left_len, 0.03, 0.04),
                           (left_cx, fence_y, rail_z),
                           pal.fence, bevel=False, smooth=False)
            created.append(rail)

        # 右セクション
        right_posts = [x for x in post_xs if x > gate_half]
        if len(right_posts) >= 2:
            right_start = min(right_posts)
            right_end = max(right_posts)
            right_len = right_end - right_start
            right_cx = (right_start + right_end) / 2
            rail = make_box(f"FenceRail_R_{z_ratio}",
                           (right_len, 0.03, 0.04),
                           (right_cx, fence_y, rail_z),
                           pal.fence, bevel=False, smooth=False)
            created.append(rail)

    print(f"[AG] フェンス: y={fence_y:.1f}, ポスト{len(post_xs)}本")
    return created


def make_simple_tree(name: str, location: tuple, pal: MaterialPalette) -> list:
    """簡易的な木（幹+葉の球）"""
    trunk_h = 1.5
    x, y, z = location

    # 幹
    bpy.ops.mesh.primitive_cylinder_add(radius=0.1, depth=trunk_h,
                                        location=(x, y, z + trunk_h / 2))
    trunk = bpy.context.active_object
    trunk.name = f"{name}_Trunk"
    trunk_mat = make_mat(f"{name}_TrunkMat", (0.4, 0.28, 0.15), roughness=0.8)
    assign_mat(trunk, trunk_mat)
    set_smooth(trunk)

    # 葉（球体）
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.8,
                                          location=(x, y, z + trunk_h + 0.3))
    leaf = bpy.context.active_object
    leaf.name = f"{name}_Leaves"
    leaf_mat = make_mat(f"{name}_LeafMat", (0.15, 0.42, 0.12), roughness=0.9)
    assign_mat(leaf, leaf_mat)
    set_smooth(leaf)

    return [trunk, leaf]


# ==============================================================
# 5. カメラ・ライト・レンダリング
# ==============================================================

def setup_camera(p: HouseParams):
    """カメラ配置（Track To で建物中心を自動照準）"""
    # 注視ターゲット（建物の中心）
    target_z = (p.wall_top + p.roof_peak) / 2  # 壁と棟の中間
    bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, target_z))
    target = bpy.context.active_object
    target.name = "CameraTarget"

    # カメラ位置（十分な距離を確保）
    cam_x = 15.0
    cam_y = -12.0
    cam_z = 9.0

    bpy.ops.object.camera_add(location=(cam_x, cam_y, cam_z))
    cam = bpy.context.active_object
    cam.name = "HouseCamera"
    cam.data.lens = 32
    cam.data.clip_end = 500
    bpy.context.scene.camera = cam

    # Track To コンストレイント（カメラがターゲットを常に向く）
    constraint = cam.constraints.new(type='TRACK_TO')
    constraint.target = target
    constraint.track_axis = 'TRACK_NEGATIVE_Z'
    constraint.up_axis = 'UP_Y'

    print(f"[AG] カメラ: ({cam_x}, {cam_y}, {cam_z}) → ターゲット z={target_z:.1f}")


def setup_lighting():
    """太陽光 + 環境光"""
    # サンライト
    bpy.ops.object.light_add(
        type='SUN',
        location=(5, -5, 15),
        rotation=(math.radians(40), math.radians(10), math.radians(25))
    )
    sun = bpy.context.active_object
    sun.name = "Sunlight"
    sun.data.energy = 3.5
    sun.data.color = (1.0, 0.95, 0.88)

    # 環境光（空の色）
    world = bpy.data.worlds.new("HouseWorld")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.50, 0.65, 0.85, 1.0)
        bg.inputs["Strength"].default_value = 0.7

    print("[AG] ライティング設定完了")


def setup_render(args):
    """Cycles + OptiX レンダリング設定"""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.image_settings.file_format = "PNG"

    output_path = str(Path(args.output).resolve())
    scene.render.filepath = output_path

    scene.cycles.device = "GPU"
    scene.cycles.samples = args.samples

    # OptiX設定
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs:
        prefs.preferences.compute_device_type = "OPTIX"
        prefs.preferences.get_devices()
        for d in prefs.preferences.devices:
            d.use = True
        print("[AG] OptiX設定完了")
    else:
        scene.cycles.device = "CPU"
        print("[AG] CPU フォールバック")

    print(f"[AG] レンダ設定: {args.samples}spp, 1920x1080, 出力={output_path}")


# ==============================================================
# 6. メインパイプライン
# ==============================================================

def main():
    args = parse_args()
    p = HouseParams()

    print("=" * 60)
    print("[AG] パラメトリック住宅モデリング v2 開始")
    print(f"[AG] 基礎: {p.foundation_w}x{p.foundation_d}")
    print(f"[AG] 壁高: {p.wall_h}m, 壁上端Z: {p.wall_top:.2f}m")
    print(f"[AG] 屋根棟: {p.roof_peak:.2f}m")
    print("=" * 60)

    clear_scene()

    # マテリアルパレット
    pal = MaterialPalette()

    # ---- ルートEmpty（全パーツの親）----
    bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
    root = bpy.context.active_object
    root.name = "House_Root"

    # ---- 住宅パーツ作成 ----
    ground = make_ground(p, pal)

    foundation = make_foundation(p, pal)
    set_parent(foundation, root)

    walls = make_walls_with_openings(p, pal)
    set_parent(walls, root)

    windows = make_windows(p, pal)
    for w in windows:
        set_parent(w, root)

    door_parts = make_door(p, pal)
    for d in door_parts:
        set_parent(d, root)

    roof = make_roof_bmesh(p, pal)
    set_parent(roof, root)

    chimney = make_chimney(p, pal)
    set_parent(chimney, root)

    porch_parts = make_porch(p, pal)
    for pp in porch_parts:
        set_parent(pp, root)

    pathway = make_pathway(p, pal)

    fence_parts = make_fence(p, pal)
    for fp in fence_parts:
        set_parent(fp, root)

    # 庭木
    tree1 = make_simple_tree("Tree_L", (-p.wall_w / 2 - 1.5, -1, 0), pal)
    tree2 = make_simple_tree("Tree_R", (p.wall_w / 2 + 2.0, 1, 0), pal)

    # カメラ・ライト
    setup_camera(p)
    setup_lighting()
    setup_render(args)

    # .blend保存
    if args.save_blend:
        blend_path = str(Path(args.save_blend).resolve())
        Path(blend_path).parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print(f"[AG] .blend保存: {blend_path}")

    # レンダリング
    out_dir = Path(args.output).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    print("[AG] レンダリング開始...")
    bpy.ops.render.render(write_still=True)
    print(f"[AG] レンダリング完了: {args.output}")

    print("=" * 60)
    print("[AG] パラメトリック住宅モデリング v2 完了！")
    print("=" * 60)


if __name__ == "__main__":
    main()
