"""
build_house_v3.py - 2階建て戸建て住宅（実寸ベース・プロシージャルPBR）

v2からの改善:
- 2階建て化（1F:2.6m + 2F:2.4m）
- 日本住宅の実寸パラメータ
- プロシージャルPBRマテリアル（Noise/Wave/Musgrave + Bump）
- ArchVizカメラ（目線高1.6m、焦点40mm）
- ゴールデンアワーライティング
- 建築ディテール（庇・幕板・雨樋・バルコニー・玄関ポーチ）
"""

import sys
import math
import argparse
from pathlib import Path
from dataclasses import dataclass

import bpy
import bmesh
from mathutils import Vector


# ================================================================
# パラメータ
# ================================================================

@dataclass
class HP:
    """住宅パラメータ（日本木造2階建てベース）"""
    # 地面
    ground_size: float = 25.0

    # 基礎
    fw: float = 8.0;  fd: float = 6.0;  fh: float = 0.4

    # 壁
    wt: float = 0.18  # 壁厚
    f1h: float = 2.6  # 1F天井高
    f2h: float = 2.4  # 2F天井高
    floor_t: float = 0.25  # 床/天井厚

    # 屋根
    roof_pitch: float = 0.4  # 4寸勾配（10:4）
    roof_ovh: float = 0.25   # 軒の出
    roof_t: float = 0.06     # 屋根厚

    # 窓
    hw_w: float = 1.69; hw_h: float = 2.03  # 掃き出し窓
    lw_w: float = 1.2;  lw_h: float = 1.0   # 腰高窓
    lw_sill: float = 0.9  # 腰高窓の窓台
    win_depth: float = 0.04
    frame_w: float = 0.05

    # ドア
    dw: float = 0.9; dh: float = 2.1; dd: float = 0.05

    # ポーチ
    porch_w: float = 2.4; porch_d: float = 1.5; porch_h: float = 0.15
    porch_col_r: float = 0.06  # 柱の半径
    porch_roof_h: float = 2.8  # ポーチ屋根高

    # バルコニー
    bal_w: float = 3.0; bal_d: float = 1.2; bal_h: float = 1.0
    bal_floor_t: float = 0.1; bal_rail_t: float = 0.04

    # フェンス
    fence_h: float = 0.9; fence_off: float = 4.0; fence_post: float = 0.06

    # 煙突
    chim_w: float = 0.45; chim_d: float = 0.45; chim_extra_h: float = 1.2

    @property
    def wi(self): return self.fw - self.wt * 2  # 壁内寸幅
    @property
    def di(self): return self.fd - self.wt * 2  # 壁内寸奥行
    @property
    def ftop(self): return self.fh  # 基礎上面
    @property
    def f1_top(self): return self.ftop + self.f1h  # 1F上面
    @property
    def f2_base(self): return self.f1_top + self.floor_t  # 2F床面
    @property
    def f2_top(self): return self.f2_base + self.f2h  # 2F上面=壁上端
    @property
    def wall_total(self): return self.f2_top - self.ftop  # 壁の総高
    @property
    def ridge_h(self): return (self.fw / 2) * self.roof_pitch  # 壁幅ベースの棟高
    @property
    def peak_z(self): return self.f2_top + self.ridge_h  # 棟Z
    @property
    def rhw(self): return self.fw / 2 + self.roof_ovh  # 屋根半幅
    @property
    def rhd(self): return self.fd / 2 + self.roof_ovh  # 屋根半奥行


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="ag_runs/house_v3_render.png")
    p.add_argument("--samples", type=int, default=128)
    p.add_argument("--save-blend", default="ag_runs/house_v3.blend")
    return p.parse_args(argv)


# ================================================================
# ユーティリティ
# ================================================================

def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def parent(child, par):
    child.parent = par
    child.matrix_parent_inverse = par.matrix_world.inverted()

def assign(obj, mat):
    if obj.data.materials: obj.data.materials[0] = mat
    else: obj.data.materials.append(mat)

def box(name, size, loc, mat=None, bev=0.01):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = (size[0]/2, size[1]/2, size[2]/2)
    bpy.ops.object.transform_apply(scale=True)
    if mat: assign(o, mat)
    if bev > 0:
        m = o.modifiers.new("Bevel", 'BEVEL')
        m.width = bev; m.segments = 2; m.limit_method = 'ANGLE'
    return o

def smooth(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.shade_smooth()
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)

def cut(target, pos, size, name):
    c = box(f"_cut_{name}", size, pos, bev=0)
    bpy.context.view_layer.objects.active = target
    m = target.modifiers.new(name, 'BOOLEAN')
    m.operation = 'DIFFERENCE'; m.object = c
    bpy.ops.object.modifier_apply(modifier=name)
    bpy.data.objects.remove(c, do_unlink=True)


# ================================================================
# プロシージャルPBRマテリアル
# ================================================================

def _pbr(name, color, rough=0.5, metal=0.0, trans=0.0):
    """基本PBRマテリアル"""
    mat = bpy.data.materials.new(name)
    n = mat.node_tree.nodes
    b = n.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if trans > 0:
        b.inputs["Transmission Weight"].default_value = trans
        b.inputs["IOR"].default_value = 1.45
    return mat

def _add_bump(mat, scale=5.0, strength=0.15, tex_type='NOISE'):
    """Noiseベースのバンプを追加"""
    tree = mat.node_tree
    nodes = tree.nodes; links = tree.links
    bsdf = nodes.get("Principled BSDF")
    out_node = nodes.get("Material Output")

    tc = nodes.new('ShaderNodeTexCoord')
    mp = nodes.new('ShaderNodeMapping')
    mp.inputs['Scale'].default_value = (scale, scale, scale)
    links.new(tc.outputs['Object'], mp.outputs[0] if False else mp.inputs['Vector'])

    if tex_type == 'NOISE':
        tex = nodes.new('ShaderNodeTexNoise')
        tex.inputs['Scale'].default_value = scale
        tex.inputs['Detail'].default_value = 6.0
    elif tex_type == 'WAVE':
        tex = nodes.new('ShaderNodeTexWave')
        tex.inputs['Scale'].default_value = scale
        tex.inputs['Distortion'].default_value = 2.0
    elif tex_type == 'MUSGRAVE':
        tex = nodes.new('ShaderNodeTexNoise')  # Musgrave deprecated in 5.0
        tex.inputs['Scale'].default_value = scale
        tex.inputs['Detail'].default_value = 8.0

    links.new(mp.outputs['Vector'], tex.inputs['Vector'])

    bump = nodes.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = strength
    links.new(tex.outputs['Fac'], bump.inputs['Height'])
    links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])

    # Roughness変化
    cr = nodes.new('ShaderNodeValToRGB')
    cr.color_ramp.elements[0].color = (0.4, 0.4, 0.4, 1)
    cr.color_ramp.elements[1].color = (0.7, 0.7, 0.7, 1)
    links.new(tex.outputs['Fac'], cr.inputs['Fac'])

    return mat


class Pal:
    """マテリアルパレット"""
    def __init__(self):
        # 壁（漆喰調 + Bump）
        self.wall = _pbr("Wall_Plaster", (0.90, 0.86, 0.78), rough=0.65)
        _add_bump(self.wall, scale=8.0, strength=0.08, tex_type='NOISE')

        # 基礎（コンクリート + Bump）
        self.base = _pbr("Concrete", (0.52, 0.50, 0.48), rough=0.85)
        _add_bump(self.base, scale=12.0, strength=0.12, tex_type='NOISE')

        # 屋根（瓦調 + Wave Bump）
        self.roof = _pbr("Roof_Tile", (0.32, 0.12, 0.08), rough=0.5)
        _add_bump(self.roof, scale=15.0, strength=0.2, tex_type='WAVE')

        # ドア（木目 + Wave Bump）
        self.door = _pbr("Door_Wood", (0.35, 0.20, 0.10), rough=0.5)
        _add_bump(self.door, scale=20.0, strength=0.1, tex_type='WAVE')

        # 窓ガラス
        self.glass = _pbr("Glass", (0.8, 0.9, 0.95), rough=0.02, trans=0.85)
        # 窓枠
        self.frame = _pbr("Frame", (0.88, 0.88, 0.90), rough=0.3, metal=0.15)
        # 真鍮
        self.brass = _pbr("Brass", (0.8, 0.65, 0.2), rough=0.15, metal=0.9)

        # 地面（芝生 + Bump）
        self.grass = _pbr("Grass", (0.18, 0.42, 0.12), rough=0.95)
        _add_bump(self.grass, scale=30.0, strength=0.05, tex_type='NOISE')

        # 小道
        self.path = _pbr("Path", (0.58, 0.54, 0.50), rough=0.8)
        _add_bump(self.path, scale=10.0, strength=0.1, tex_type='NOISE')

        # フェンス
        self.fence = _pbr("Fence", (0.94, 0.92, 0.88), rough=0.4)
        # ポーチ
        self.porch = _pbr("Porch", (0.50, 0.46, 0.42), rough=0.75)
        _add_bump(self.porch, scale=8.0, strength=0.08, tex_type='NOISE')
        # 煉瓦
        self.brick = _pbr("Brick", (0.50, 0.25, 0.15), rough=0.8)
        _add_bump(self.brick, scale=6.0, strength=0.15, tex_type='NOISE')
        # 幕板
        self.trim = _pbr("Trim", (0.85, 0.82, 0.78), rough=0.4)
        # 雨樋
        self.gutter = _pbr("Gutter", (0.30, 0.28, 0.26), rough=0.3, metal=0.3)


# ================================================================
# 住宅パーツ
# ================================================================

def make_ground(p, pal):
    return box("Ground", (p.ground_size, p.ground_size, 0.05), (0,0,-0.025), pal.grass, bev=0)

def make_foundation(p, pal):
    o = box("Foundation", (p.fw, p.fd, p.fh), (0,0,p.fh/2), pal.base)
    smooth(o); return o

def make_walls(p, pal):
    """2階建ての壁体（Boolean空洞化）"""
    h = p.wall_total
    z = p.ftop + h/2
    outer = box("Walls", (p.fw, p.fd, h), (0,0,z), pal.wall, bev=0)
    inner = box("_inner", (p.wi, p.di, h+0.1), (0,0,z), bev=0)
    bpy.context.view_layer.objects.active = outer
    m = outer.modifiers.new("Hollow", 'BOOLEAN')
    m.operation = 'DIFFERENCE'; m.object = inner
    bpy.ops.object.modifier_apply(modifier="Hollow")
    bpy.data.objects.remove(inner, do_unlink=True)

    fy = -p.fd/2; by = p.fd/2; lx = -p.fw/2; rx = p.fw/2

    # === 1F 窓（正面: 掃き出し×2 ドア左右）===
    wz1 = p.ftop + p.hw_h/2
    for sx in [-p.fw/4 - 0.3, p.fw/4 + 0.3]:
        cut(outer, (sx, fy, wz1), (p.hw_w, p.wt+0.1, p.hw_h), f"1F_F_{sx:.0f}")

    # 1F 正面ドア
    cut(outer, (0, fy, p.ftop + p.dh/2), (p.dw, p.wt+0.1, p.dh), "Door")

    # 1F 側面窓
    wz_l = p.ftop + p.lw_sill + p.lw_h/2
    for side, x in [("L", lx), ("R", rx)]:
        cut(outer, (x, 0, wz_l), (p.wt+0.1, p.lw_w, p.lw_h), f"1F_{side}")

    # 1F 背面窓
    cut(outer, (0, by, wz_l), (p.hw_w, p.wt+0.1, p.lw_h), "1F_B")

    # === 2F 窓 ===
    wz2 = p.f2_base + p.lw_sill + p.lw_h/2
    for sx in [-p.fw/4, 0, p.fw/4]:
        cut(outer, (sx, fy, wz2), (p.lw_w, p.wt+0.1, p.lw_h), f"2F_F_{sx:.0f}")

    for side, x in [("L", lx), ("R", rx)]:
        cut(outer, (x, 0, wz2), (p.wt+0.1, p.lw_w, p.lw_h), f"2F_{side}")

    cut(outer, (0, by, wz2), (p.lw_w, p.wt+0.1, p.lw_h), "2F_B")

    smooth(outer)
    print(f"[AG] 壁体: 2階建て 総高{h:.2f}m 壁上端Z={p.f2_top:.2f}")
    return outer

def make_windows(p, pal):
    """窓ガラス+枠+桟"""
    objs = []
    fy = -p.fd/2; by = p.fd/2; lx = -p.fw/2; rx = p.fw/2

    def _win(name, pos, gsz, orient):
        g = box(f"G_{name}", gsz, pos, pal.glass, bev=0)
        objs.append(g)
        fw = p.frame_w
        if orient == "Y":
            fsz = (gsz[0]+fw*2, gsz[1]+0.02, gsz[2]+fw*2)
        else:
            fsz = (gsz[0]+0.02, gsz[1]+fw*2, gsz[2]+fw*2)
        f = box(f"F_{name}", fsz, pos, pal.frame, bev=0.005)
        smooth(f); objs.append(f)

    # 1F 正面掃き出し
    wz1 = p.ftop + p.hw_h/2
    for i, sx in enumerate([-p.fw/4 - 0.3, p.fw/4 + 0.3]):
        _win(f"1FF{i}", (sx, fy, wz1),
             (p.hw_w-0.06, p.win_depth, p.hw_h-0.06), "Y")

    # 1F 側面
    wz_l = p.ftop + p.lw_sill + p.lw_h/2
    _win("1FL", (lx, 0, wz_l), (p.win_depth, p.lw_w-0.06, p.lw_h-0.06), "X")
    _win("1FR", (rx, 0, wz_l), (p.win_depth, p.lw_w-0.06, p.lw_h-0.06), "X")
    _win("1FB", (0, by, wz_l), (p.hw_w-0.06, p.win_depth, p.lw_h-0.06), "Y")

    # 2F
    wz2 = p.f2_base + p.lw_sill + p.lw_h/2
    for i, sx in enumerate([-p.fw/4, 0, p.fw/4]):
        _win(f"2FF{i}", (sx, fy, wz2),
             (p.lw_w-0.06, p.win_depth, p.lw_h-0.06), "Y")
    _win("2FL", (lx, 0, wz2), (p.win_depth, p.lw_w-0.06, p.lw_h-0.06), "X")
    _win("2FR", (rx, 0, wz2), (p.win_depth, p.lw_w-0.06, p.lw_h-0.06), "X")
    _win("2FB", (0, by, wz2), (p.lw_w-0.06, p.win_depth, p.lw_h-0.06), "Y")

    return objs

def make_door(p, pal):
    fy = -p.fd/2
    d = box("Door", (p.dw-0.03, p.dd, p.dh-0.02),
            (0, fy+0.01, p.ftop+p.dh/2), pal.door)
    smooth(d)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.03,
        location=(p.dw/2-0.12, fy-0.02, p.ftop+1.0))
    k = bpy.context.active_object; k.name = "Knob"
    assign(k, pal.brass); smooth(k)
    return [d, k]

def make_roof(p, pal):
    """BMesh切妻屋根（4寸勾配）"""
    mesh = bpy.data.meshes.new("RoofMesh")
    obj = bpy.data.objects.new("Roof", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj

    bm = bmesh.new()
    hw, hd = p.rhw, p.rhd
    wt, pk = p.f2_top, p.peak_z
    th = p.roof_t

    # 外面
    v = [bm.verts.new(c) for c in [
        (-hw,-hd,wt), (hw,-hd,wt), (0,-hd,pk),  # 前: 0,1,2
        (-hw,hd,wt),  (hw,hd,wt),  (0,hd,pk),    # 後: 3,4,5
    ]]
    # 内面（厚み分内側）
    ins = th / math.cos(math.atan2(p.ridge_h, p.rhw))
    vi = [bm.verts.new(c) for c in [
        (-hw+th,-hd+th,wt), (hw-th,-hd+th,wt), (0,-hd+th,pk-ins),
        (-hw+th,hd-th,wt),  (hw-th,hd-th,wt),  (0,hd-th,pk-ins),
    ]]

    bm.faces.new([v[0],v[2],v[5],v[3]])  # 左外
    bm.faces.new([v[2],v[1],v[4],v[5]])  # 右外
    bm.faces.new([v[0],v[1],v[2]])        # 前妻外
    bm.faces.new([v[3],v[5],v[4]])        # 後妻外
    bm.faces.new([vi[3],vi[5],vi[2],vi[0]])  # 左内
    bm.faces.new([vi[5],vi[4],vi[1],vi[2]])  # 右内
    bm.faces.new([vi[2],vi[1],vi[0]])    # 前妻内
    bm.faces.new([vi[4],vi[5],vi[3]])    # 後妻内
    # 縁
    bm.faces.new([v[0],v[3],vi[3],vi[0]])
    bm.faces.new([v[1],v[0],vi[0],vi[1]])
    bm.faces.new([v[4],v[1],vi[1],vi[4]])
    bm.faces.new([v[3],v[4],vi[4],vi[3]])

    bm.normal_update(); bm.to_mesh(mesh); bm.free()

    bpy.context.view_layer.objects.active = obj; obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT'); obj.select_set(False)
    assign(obj, pal.roof); smooth(obj)
    print(f"[AG] 屋根: 4寸勾配 棟Z={pk:.2f}")
    return obj

def make_fascia(p, pal):
    """幕板（1F/2F境界ライン）"""
    z = p.f1_top + p.floor_t/2
    parts = []
    for name, sz, pos in [
        ("Fascia_F", (p.fw+0.02, 0.04, 0.12), (0, -p.fd/2, z)),
        ("Fascia_B", (p.fw+0.02, 0.04, 0.12), (0, p.fd/2, z)),
        ("Fascia_L", (0.04, p.fd+0.02, 0.12), (-p.fw/2, 0, z)),
        ("Fascia_R", (0.04, p.fd+0.02, 0.12), (p.fw/2, 0, z)),
    ]:
        o = box(name, sz, pos, pal.trim, bev=0.005)
        parts.append(o)
    return parts

def make_awnings(p, pal):
    """窓上の庇"""
    parts = []
    fy = -p.fd/2
    # 1F掃き出し窓の庇
    aw_z = p.ftop + p.hw_h + 0.05
    for sx in [-p.fw/4, p.fw/4]:
        if abs(sx) < p.dw: continue
        o = box(f"Awning_1F_{sx:.0f}",
                (p.hw_w+0.1, 0.3, 0.04),
                (sx, fy-0.12, aw_z), pal.trim, bev=0.005)
        parts.append(o)
    # ドア庇
    o = box("Awning_Door", (p.dw+0.3, 0.35, 0.04),
            (0, fy-0.15, p.ftop+p.dh+0.05), pal.trim, bev=0.005)
    parts.append(o)
    return parts

def make_gutter(p, pal):
    """雨樋（正面右コーナー）"""
    fy = -p.fd/2
    gx = p.fw/2 + 0.05  # 壁外側
    gz_top = p.f2_top
    parts = []
    # 縦パイプ
    bpy.ops.mesh.primitive_cylinder_add(radius=0.025, depth=gz_top,
        location=(gx, fy, gz_top/2))
    pipe = bpy.context.active_object; pipe.name = "Gutter_V"
    assign(pipe, pal.gutter); smooth(pipe); parts.append(pipe)
    # 横樋（屋根際）
    bpy.ops.mesh.primitive_cylinder_add(radius=0.03, depth=p.fd+0.3,
        location=(gx, 0, gz_top), rotation=(math.radians(90), 0, 0))
    h = bpy.context.active_object; h.name = "Gutter_H"
    assign(h, pal.gutter); smooth(h); parts.append(h)
    return parts

def make_balcony(p, pal):
    """2F正面バルコニー"""
    fy = -p.fd/2
    bz = p.f2_base
    by = fy - p.bal_d/2
    parts = []
    # 床
    fl = box("Bal_Floor", (p.bal_w, p.bal_d, p.bal_floor_t),
             (0, by, bz), pal.porch, bev=0.005)
    parts.append(fl)
    # 手すり（3面）
    for name, sz, pos in [
        ("Bal_F", (p.bal_w, p.bal_rail_t, p.bal_h), (0, fy-p.bal_d, bz+p.bal_h/2)),
        ("Bal_L", (p.bal_rail_t, p.bal_d, p.bal_h), (-p.bal_w/2, by, bz+p.bal_h/2)),
        ("Bal_R", (p.bal_rail_t, p.bal_d, p.bal_h), (p.bal_w/2, by, bz+p.bal_h/2)),
    ]:
        r = box(name, sz, pos, pal.fence, bev=0.005)
        parts.append(r)
    return parts

def make_porch(p, pal):
    """玄関ポーチ（床+柱2本+庇）"""
    fy = -p.fd/2
    py = fy - p.porch_d/2
    parts = []
    # 床
    fl = box("Porch_Floor", (p.porch_w, p.porch_d, p.porch_h),
             (0, py, p.fh/2), pal.porch)
    parts.append(fl)
    # ステップ
    st = box("Porch_Step", (p.porch_w*0.8, 0.35, p.porch_h*0.6),
             (0, py-p.porch_d/2-0.2, p.porch_h*0.3), pal.porch)
    parts.append(st)
    # 柱
    for sx in [-p.porch_w/2+0.1, p.porch_w/2-0.1]:
        bpy.ops.mesh.primitive_cylinder_add(radius=p.porch_col_r,
            depth=p.porch_roof_h, location=(sx, fy-p.porch_d, p.ftop+p.porch_roof_h/2))
        col = bpy.context.active_object; col.name = f"Porch_Col_{sx:.1f}"
        assign(col, pal.fence); smooth(col); parts.append(col)
    # 庇
    roof = box("Porch_Roof", (p.porch_w+0.2, p.porch_d+0.2, 0.06),
               (0, py, p.ftop+p.porch_roof_h+0.03), pal.roof, bev=0.008)
    parts.append(roof)
    return parts

def make_pathway(p, pal):
    fy = -p.fd/2
    pl = p.fence_off + 2.0
    py = fy - p.porch_d - pl/2
    return box("Pathway", (1.0, pl, 0.03), (0, py, 0.015), pal.path, bev=0)

def make_fence(p, pal):
    fy = -p.fd/2
    fey = fy - p.porch_d - p.fence_off
    fw = p.fw + 3.0; gate = 0.6
    parts = []
    xs = [x for x in [fw/2*(-1)+i*1.8 for i in range(int(fw/1.8)+1)] if abs(x) > gate]
    for i, x in enumerate(xs):
        parts.append(box(f"FP_{i}", (p.fence_post, p.fence_post, p.fence_h),
                         (x, fey, p.fence_h/2), pal.fence, bev=0.005))
    # 横棒
    for zr in [0.3, 0.7]:
        for side_xs in [sorted([x for x in xs if x < -gate]),
                        sorted([x for x in xs if x > gate])]:
            if len(side_xs) >= 2:
                s, e = side_xs[0], side_xs[-1]
                parts.append(box(f"FR_{zr}_{s:.0f}", (e-s, 0.03, 0.04),
                                 ((s+e)/2, fey, p.fence_h*zr), pal.fence, bev=0))
    return parts

def make_tree(name, loc, pal):
    """木: テーパー幹 + ICO球クラスター"""
    x, y, _ = loc; parts = []
    trunk_h = 2.0
    bpy.ops.mesh.primitive_cone_add(radius1=0.12, radius2=0.05, depth=trunk_h,
        location=(x, y, trunk_h/2))
    t = bpy.context.active_object; t.name = f"{name}_Trunk"
    assign(t, _pbr(f"{name}_Bark", (0.35, 0.22, 0.12), rough=0.85))
    smooth(t); parts.append(t)
    # 葉のクラスター（ICO球3つ）
    leaf_mat = _pbr(f"{name}_Leaf", (0.12, 0.38, 0.10), rough=0.9)
    for dz, r in [(1.8, 0.9), (2.3, 0.7), (2.0, 0.6)]:
        bpy.ops.mesh.primitive_ico_sphere_add(radius=r, subdivisions=2,
            location=(x+r*0.2, y-r*0.1, dz))
        l = bpy.context.active_object; l.name = f"{name}_Leaf_{dz}"
        assign(l, leaf_mat); smooth(l); parts.append(l)
    return parts

def make_chimney(p, pal):
    slope = p.ridge_h / p.rhw
    cx = p.fw/4
    rz = p.peak_z - slope * abs(cx)
    h = p.chim_extra_h + (rz - p.f2_top)
    o = box("Chimney", (p.chim_w, p.chim_d, h),
            (cx, 0, rz + p.chim_extra_h/2), pal.brick)
    smooth(o); return o


# ================================================================
# カメラ・ライト
# ================================================================

def setup_camera(p):
    """ArchVizカメラ: 目線高1.6m、焦点40mm、Three-quarter angle"""
    tgt_z = (p.ftop + p.peak_z) / 2  # 基礎～棟の中間
    bpy.ops.object.empty_add(type='PLAIN_AXES',
        location=(0, 0, tgt_z))
    tgt = bpy.context.active_object; tgt.name = "CamTarget"

    # Three-quarter angle: 十分離れて建物全体を収める
    cam_x = 18.0; cam_y = -14.0; cam_z = 5.0
    bpy.ops.object.camera_add(location=(cam_x, cam_y, cam_z))
    cam = bpy.context.active_object; cam.name = "Camera"
    cam.data.lens = 40; cam.data.clip_end = 500
    bpy.context.scene.camera = cam

    c = cam.constraints.new(type='TRACK_TO')
    c.target = tgt; c.track_axis = 'TRACK_NEGATIVE_Z'; c.up_axis = 'UP_Y'
    print(f"[AG] カメラ: ({cam_x},{cam_y},{cam_z}) lens=40mm")

def setup_lighting():
    """ゴールデンアワーライティング"""
    # サンライト（午後の斜光）
    bpy.ops.object.light_add(type='SUN', location=(5, -8, 12),
        rotation=(math.radians(35), math.radians(10), math.radians(225)))
    sun = bpy.context.active_object; sun.name = "Sun"
    sun.data.energy = 4.0
    sun.data.color = (1.0, 0.92, 0.80)  # 暖色

    # 環境（Sky Texture）
    world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    tree = world.node_tree; nodes = tree.nodes; links = tree.links
    bg = nodes.get("Background")
    sky = nodes.new('ShaderNodeTexSky')
    sky.sky_type = 'HOSEK_WILKIE'
    sky.sun_direction = Vector((
        math.cos(math.radians(225)) * math.cos(math.radians(25)),
        math.sin(math.radians(225)) * math.cos(math.radians(25)),
        math.sin(math.radians(25))
    ))
    links.new(sky.outputs['Color'], bg.inputs['Color'])
    bg.inputs['Strength'].default_value = 1.0
    print("[AG] ライティング: ゴールデンアワー + Nishita Sky")


def setup_render(args):
    s = bpy.context.scene
    s.render.engine = "CYCLES"
    s.render.resolution_x = 1920; s.render.resolution_y = 1080
    s.render.image_settings.file_format = "PNG"
    out = str(Path(args.output).resolve())
    s.render.filepath = out
    s.cycles.device = "GPU"; s.cycles.samples = args.samples
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs:
        prefs.preferences.compute_device_type = "OPTIX"
        prefs.preferences.get_devices()
        for d in prefs.preferences.devices: d.use = True
    print(f"[AG] Render: {args.samples}spp OptiX 1920x1080")


# ================================================================
# メイン
# ================================================================

def main():
    args = parse_args(); p = HP()
    print("=" * 60)
    print("[AG] 戸建て住宅 v3（2階建て・実寸ベース）")
    print(f"[AG] 基礎: {p.fw}x{p.fd}x{p.fh}")
    print(f"[AG] 1F高: {p.f1h}m  2F高: {p.f2h}m  壁上端: {p.f2_top:.2f}m")
    print(f"[AG] 屋根: 4寸勾配  棟Z: {p.peak_z:.2f}m")
    print("=" * 60)

    clear()
    pal = Pal()

    bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0,0,0))
    root = bpy.context.active_object; root.name = "House"

    gnd = make_ground(p, pal)
    fnd = make_foundation(p, pal); parent(fnd, root)
    walls = make_walls(p, pal); parent(walls, root)
    for o in make_windows(p, pal): parent(o, root)
    for o in make_door(p, pal): parent(o, root)
    rf = make_roof(p, pal); parent(rf, root)
    chm = make_chimney(p, pal); parent(chm, root)
    for o in make_fascia(p, pal): parent(o, root)
    for o in make_awnings(p, pal): parent(o, root)
    for o in make_gutter(p, pal): parent(o, root)
    for o in make_balcony(p, pal): parent(o, root)
    for o in make_porch(p, pal): parent(o, root)
    make_pathway(p, pal)
    for o in make_fence(p, pal): parent(o, root)

    for o in make_tree("Tree_L", (-p.fw/2-2, -1, 0), pal): pass
    for o in make_tree("Tree_R", (p.fw/2+2.5, 1.5, 0), pal): pass
    for o in make_tree("Tree_B", (-2, p.fd/2+2, 0), pal): pass

    setup_camera(p); setup_lighting(); setup_render(args)

    if args.save_blend:
        bp = str(Path(args.save_blend).resolve())
        Path(bp).parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=bp)
        print(f"[AG] Saved: {bp}")

    Path(args.output).resolve().parent.mkdir(parents=True, exist_ok=True)
    print("[AG] レンダリング開始...")
    bpy.ops.render.render(write_still=True)
    print(f"[AG] 完了: {args.output}")
    print("=" * 60)

if __name__ == "__main__":
    main()
