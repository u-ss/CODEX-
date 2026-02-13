"""
戸建て住宅3Dモデリングスクリプト

Blender CLIで実行して、リアルな戸建て住宅を生成する。
- 基礎・壁・屋根・窓・ドア・煙突
- マテリアル（壁色・屋根色・窓ガラス等）
- カメラ・ライト配置
- レンダリング出力

使用例:
    blender --background --factory-startup --python build_house.py -- --output house.png
"""

import sys
import math
import argparse
from pathlib import Path

import bpy
import bmesh


def parse_args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    parser = argparse.ArgumentParser(description="戸建て住宅モデリング")
    parser.add_argument("--output", "-o", default="ag_runs/house_render.png", help="レンダリング出力先")
    parser.add_argument("--samples", type=int, default=128, help="サンプル数")
    parser.add_argument("--save-blend", default="ag_runs/house_scene.blend", help=".blend保存先")
    return parser.parse_args(argv)


def clear_scene():
    """シーン初期化"""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    print("[AG] シーン初期化完了")


# ==============================================================
# マテリアル作成ユーティリティ
# ==============================================================

def make_mat(name, color, metallic=0.0, roughness=0.5, alpha=1.0):
    """Principled BSDFマテリアルを作成"""
    mat = bpy.data.materials.new(name=name)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color, alpha)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        if alpha < 1.0:
            # ガラス風の透過
            bsdf.inputs["Transmission Weight"].default_value = 0.8
            bsdf.inputs["IOR"].default_value = 1.45
            mat.blend_method = 'BLEND' if hasattr(mat, 'blend_method') else None
    return mat


def assign_mat(obj, mat):
    """オブジェクトにマテリアルを割り当て"""
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


# ==============================================================
# 住宅パーツ作成
# ==============================================================

def make_foundation():
    """基礎（コンクリート台座）"""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.15))
    obj = bpy.context.active_object
    obj.name = "Foundation"
    obj.scale = (6.0, 5.0, 0.15)
    bpy.ops.object.transform_apply(scale=True)
    mat = make_mat("Concrete", (0.6, 0.58, 0.55), roughness=0.9)
    assign_mat(obj, mat)
    print("[AG] 基礎作成完了")
    return obj


def make_walls():
    """壁（ボックスからbooleanで窓・ドアを抜く）"""
    # メイン壁体
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1.7))
    walls = bpy.context.active_object
    walls.name = "Walls"
    walls.scale = (5.8, 4.8, 1.4)
    bpy.ops.object.transform_apply(scale=True)

    # 壁の色（クリーム色）
    mat = make_mat("WallPaint", (0.92, 0.88, 0.78), roughness=0.7)
    assign_mat(walls, mat)

    # --- 窓を切り抜く ---
    windows_info = [
        # (位置, スケール, 名前)
        # 正面左窓
        ((-1.8, -2.5, 1.7), (0.9, 0.3, 0.7), "Window_Front_L"),
        # 正面右窓
        ((1.8, -2.5, 1.7), (0.9, 0.3, 0.7), "Window_Front_R"),
        # 左側面窓
        ((-3.0, -0.5, 1.7), (0.3, 0.9, 0.7), "Window_Left"),
        # 右側面窓
        ((3.0, -0.5, 1.7), (0.3, 0.9, 0.7), "Window_Right"),
        # 背面窓
        ((0, 2.5, 1.9), (1.5, 0.3, 0.5), "Window_Back"),
    ]

    for pos, scl, name in windows_info:
        bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
        cutter = bpy.context.active_object
        cutter.name = name + "_cutter"
        cutter.scale = scl
        bpy.ops.object.transform_apply(scale=True)

        # Boolean差分
        bpy.context.view_layer.objects.active = walls
        mod = walls.modifiers.new(name=name, type='BOOLEAN')
        mod.operation = 'DIFFERENCE'
        mod.object = cutter
        bpy.ops.object.modifier_apply(modifier=name)

        # カッターを非表示→削除
        bpy.data.objects.remove(cutter, do_unlink=True)

    # --- ドアを切り抜く ---
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -2.5, 1.3))
    door_cutter = bpy.context.active_object
    door_cutter.name = "Door_cutter"
    door_cutter.scale = (0.55, 0.3, 1.0)
    bpy.ops.object.transform_apply(scale=True)

    bpy.context.view_layer.objects.active = walls
    mod = walls.modifiers.new(name="Door", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = door_cutter
    bpy.ops.object.modifier_apply(modifier="Door")
    bpy.data.objects.remove(door_cutter, do_unlink=True)

    print("[AG] 壁作成完了（窓5箇所 + ドア1箇所くり抜き）")
    return walls


def make_door():
    """玄関ドア"""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -2.45, 1.3))
    door = bpy.context.active_object
    door.name = "Door"
    door.scale = (0.5, 0.05, 0.95)
    bpy.ops.object.transform_apply(scale=True)

    mat = make_mat("DoorWood", (0.35, 0.2, 0.1), roughness=0.6)
    assign_mat(door, mat)

    # ドアノブ
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.04, location=(0.2, -2.52, 1.2))
    knob = bpy.context.active_object
    knob.name = "DoorKnob"
    mat_knob = make_mat("Brass", (0.8, 0.65, 0.2), metallic=0.9, roughness=0.2)
    assign_mat(knob, mat_knob)

    print("[AG] ドア作成完了")
    return door


def make_windows_glass():
    """窓ガラス（半透明）"""
    glass_mat = make_mat("Glass", (0.7, 0.85, 0.95), metallic=0.0, roughness=0.05, alpha=0.3)
    frame_mat = make_mat("WindowFrame", (0.9, 0.9, 0.92), metallic=0.3, roughness=0.4)

    glass_info = [
        ((-1.8, -2.42, 1.7), (0.85, 0.02, 0.65), "Glass_FL"),
        ((1.8, -2.42, 1.7), (0.85, 0.02, 0.65), "Glass_FR"),
        ((-2.92, -0.5, 1.7), (0.02, 0.85, 0.65), "Glass_L"),
        ((2.92, -0.5, 1.7), (0.02, 0.85, 0.65), "Glass_R"),
        ((0, 2.42, 1.9), (1.45, 0.02, 0.45), "Glass_Back"),
    ]

    # 窓枠情報（窓ガラスより少し大きい）
    frame_info = [
        ((-1.8, -2.44, 1.7), (0.92, 0.03, 0.72), "Frame_FL"),
        ((1.8, -2.44, 1.7), (0.92, 0.03, 0.72), "Frame_FR"),
        ((-2.94, -0.5, 1.7), (0.03, 0.92, 0.72), "Frame_L"),
        ((2.94, -0.5, 1.7), (0.03, 0.92, 0.72), "Frame_R"),
        ((0, 2.44, 1.9), (1.52, 0.03, 0.52), "Frame_Back"),
    ]

    for pos, scl, name in glass_info:
        bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
        g = bpy.context.active_object
        g.name = name
        g.scale = scl
        bpy.ops.object.transform_apply(scale=True)
        assign_mat(g, glass_mat)

    for pos, scl, name in frame_info:
        bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
        f = bpy.context.active_object
        f.name = name
        f.scale = scl
        bpy.ops.object.transform_apply(scale=True)
        assign_mat(f, frame_mat)

    print("[AG] 窓ガラス＋窓枠作成完了")


def make_roof():
    """切妻屋根（三角柱）"""
    # bmeshで三角柱を作成
    mesh = bpy.data.meshes.new("RoofMesh")
    obj = bpy.data.objects.new("Roof", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj

    bm = bmesh.new()

    # 切妻屋根の頂点（前面）
    overhang = 0.6  # 軒の出
    ridge_h = 1.8   # 棟の高さ（壁上端からの追加高さ）
    wall_top = 3.1  # 壁の上端
    half_w = 3.0 + overhang
    half_d = 2.4 + overhang

    # 前面三角形
    v0 = bm.verts.new((-half_w, -half_d, wall_top))
    v1 = bm.verts.new((half_w, -half_d, wall_top))
    v2 = bm.verts.new((0, -half_d, wall_top + ridge_h))

    # 背面三角形
    v3 = bm.verts.new((-half_w, half_d, wall_top))
    v4 = bm.verts.new((half_w, half_d, wall_top))
    v5 = bm.verts.new((0, half_d, wall_top + ridge_h))

    # 面を作成
    bm.faces.new([v0, v1, v2])          # 前面
    bm.faces.new([v3, v5, v4])          # 背面
    bm.faces.new([v0, v2, v5, v3])      # 左屋根面
    bm.faces.new([v1, v4, v5, v2])      # 右屋根面
    bm.faces.new([v0, v3, v4, v1])      # 底面（天井）

    bm.to_mesh(mesh)
    bm.free()

    # 屋根色（濃い赤茶/瓦色）
    mat = make_mat("RoofTile", (0.45, 0.18, 0.12), roughness=0.6)
    assign_mat(obj, mat)

    print("[AG] 切妻屋根作成完了")
    return obj


def make_chimney():
    """煙突"""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(1.5, 0.5, 4.2))
    chimney = bpy.context.active_object
    chimney.name = "Chimney"
    chimney.scale = (0.25, 0.25, 0.6)
    bpy.ops.object.transform_apply(scale=True)

    mat = make_mat("Brick", (0.55, 0.25, 0.15), roughness=0.85)
    assign_mat(chimney, mat)
    print("[AG] 煙突作成完了")
    return chimney


def make_porch():
    """玄関ポーチ"""
    # ポーチ床
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -3.0, 0.2))
    porch = bpy.context.active_object
    porch.name = "Porch"
    porch.scale = (1.2, 0.6, 0.1)
    bpy.ops.object.transform_apply(scale=True)
    mat = make_mat("PorchStone", (0.55, 0.52, 0.48), roughness=0.8)
    assign_mat(porch, mat)

    # ステップ
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -3.5, 0.1))
    step = bpy.context.active_object
    step.name = "PorchStep"
    step.scale = (1.0, 0.3, 0.05)
    bpy.ops.object.transform_apply(scale=True)
    assign_mat(step, mat)

    print("[AG] 玄関ポーチ作成完了")


def make_ground():
    """地面（芝生）"""
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "Ground"
    mat = make_mat("Grass", (0.2, 0.45, 0.15), roughness=0.95)
    assign_mat(ground, mat)
    print("[AG] 地面作成完了")
    return ground


def make_pathway():
    """小道（玄関からの道）"""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -6.0, 0.02))
    path = bpy.context.active_object
    path.name = "Pathway"
    path.scale = (0.8, 3.0, 0.01)
    bpy.ops.object.transform_apply(scale=True)
    mat = make_mat("PathStone", (0.6, 0.58, 0.55), roughness=0.8)
    assign_mat(path, mat)
    print("[AG] 小道作成完了")


def make_fence():
    """フェンス（前庭）"""
    fence_mat = make_mat("FenceWhite", (0.95, 0.93, 0.9), roughness=0.5)

    # ポスト
    posts_x = [-6, -4, -2, 2, 4, 6]
    for x in posts_x:
        bpy.ops.mesh.primitive_cube_add(size=1, location=(x, -8.5, 0.4))
        p = bpy.context.active_object
        p.name = f"FencePost_{x}"
        p.scale = (0.05, 0.05, 0.4)
        bpy.ops.object.transform_apply(scale=True)
        assign_mat(p, fence_mat)

    # 横棒（上下2段）
    for z in [0.3, 0.55]:
        bpy.ops.mesh.primitive_cube_add(size=1, location=(-4, -8.5, z))
        r = bpy.context.active_object
        r.name = f"FenceRail_L_{z}"
        r.scale = (2.0, 0.02, 0.03)
        bpy.ops.object.transform_apply(scale=True)
        assign_mat(r, fence_mat)

        bpy.ops.mesh.primitive_cube_add(size=1, location=(4, -8.5, z))
        r2 = bpy.context.active_object
        r2.name = f"FenceRail_R_{z}"
        r2.scale = (2.0, 0.02, 0.03)
        bpy.ops.object.transform_apply(scale=True)
        assign_mat(r2, fence_mat)

    print("[AG] フェンス作成完了")


# ==============================================================
# カメラ・ライト・レンダリング設定
# ==============================================================

def setup_camera():
    """カメラ配置（斜め上から見下ろし）"""
    bpy.ops.object.camera_add(
        location=(12, -10, 7),
        rotation=(math.radians(60), 0, math.radians(50))
    )
    cam = bpy.context.active_object
    cam.name = "HouseCamera"
    cam.data.lens = 35
    bpy.context.scene.camera = cam
    print("[AG] カメラ配置完了")
    return cam


def setup_lighting():
    """ライティング（太陽光 + 環境光）"""
    # サンライト
    bpy.ops.object.light_add(
        type='SUN',
        location=(5, -5, 10),
        rotation=(math.radians(45), math.radians(15), math.radians(30))
    )
    sun = bpy.context.active_object
    sun.name = "Sunlight"
    sun.data.energy = 4.0
    sun.data.color = (1.0, 0.95, 0.85)

    # 環境光（ワールド設定）
    world = bpy.data.worlds.new("HouseWorld")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.55, 0.7, 0.9, 1.0)
        bg.inputs["Strength"].default_value = 0.8

    print("[AG] ライティング設定完了")


def setup_render(args):
    """レンダリング設定"""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = args.output

    # Cycles設定
    scene.cycles.device = "GPU"
    scene.cycles.samples = args.samples

    # OptiX設定
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs:
        prefs.preferences.compute_device_type = "OPTIX"
        prefs.preferences.get_devices()
        for d in prefs.preferences.devices:
            d.use = True
        print("[AG] OptiX (RTX 5080) 設定完了")
    else:
        print("[AG] Cycles設定にアクセスできません、CPUフォールバック")
        scene.cycles.device = "CPU"

    print(f"[AG] レンダ設定: Cycles GPU, {args.samples}spp, 1920x1080")


def main():
    args = parse_args()
    print("=" * 60)
    print("[AG] 戸建て住宅モデリング開始")
    print("=" * 60)

    clear_scene()

    # 住宅パーツ作成
    make_ground()
    make_foundation()
    make_walls()
    make_door()
    make_windows_glass()
    make_roof()
    make_chimney()
    make_porch()
    make_pathway()
    make_fence()

    # カメラ・ライト
    setup_camera()
    setup_lighting()

    # レンダリング設定
    setup_render(args)

    # .blend保存
    if args.save_blend:
        blend_dir = Path(args.save_blend).parent
        blend_dir.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=args.save_blend)
        print(f"[AG] .blend保存: {args.save_blend}")

    # レンダリング
    out_dir = Path(args.output).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[AG] レンダリング開始...")
    bpy.ops.render.render(write_still=True)
    print(f"[AG] レンダリング完了: {args.output}")

    print("=" * 60)
    print("[AG] 戸建て住宅モデリング完了！")
    print("=" * 60)


if __name__ == "__main__":
    main()
