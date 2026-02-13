"""
水晶（クリスタル）シーン構築＋レンダリング
ハイブリッドワークフロー方式（RPC構築 → CLI レンダ）
"""

import os
import sys
import time
from pathlib import Path

# blender_bridge をインポートできるようにパス追加
sys.path.insert(0, str(Path(__file__).parent))
from ag_rpc_client import RpcClient
from blender_cli import BlenderCLI

WORK_DIR = Path("ag_runs")
WORK_DIR.mkdir(parents=True, exist_ok=True)


def build_crystal_scene(client: RpcClient):
    """RPCで水晶シーンを構築"""

    # シーン初期化
    print("[AG] シーン初期化...")
    client.reset_scene()

    # === 水晶の結晶を exec_python で生成 ===
    print("[AG] 水晶モデル生成...")
    crystal_code = """
import bpy
import bmesh
import math
import random

random.seed(42)

def make_crystal(name, location, height, radius, taper=0.15, twist=0.1):
    \"\"\"六角柱ベースの水晶結晶を生成\"\"\"
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()

    sides = 6
    # 底面の頂点
    bottom_verts = []
    for i in range(sides):
        angle = math.radians(i * 360 / sides)
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        bottom_verts.append(bm.verts.new((x, y, 0)))

    # 中間の頂点（少し膨らませる）
    mid_h = height * 0.6
    mid_radius = radius * 1.05
    mid_verts = []
    for i in range(sides):
        angle = math.radians(i * 360 / sides + twist * 10)
        x = mid_radius * math.cos(angle)
        y = mid_radius * math.sin(angle)
        mid_verts.append(bm.verts.new((x, y, mid_h)))

    # 上部のテーパー頂点
    top_h = height * 0.85
    top_radius = radius * 0.5
    top_verts = []
    for i in range(sides):
        angle = math.radians(i * 360 / sides + twist * 20)
        x = top_radius * math.cos(angle)
        y = top_radius * math.sin(angle)
        top_verts.append(bm.verts.new((x, y, top_h)))

    # 頂点（先端）
    tip = bm.verts.new((0, 0, height))

    # 底面
    bm.faces.new(bottom_verts)

    # 側面（底面→中間）
    for i in range(sides):
        j = (i + 1) % sides
        bm.faces.new([bottom_verts[i], bottom_verts[j], mid_verts[j], mid_verts[i]])

    # 側面（中間→上部）
    for i in range(sides):
        j = (i + 1) % sides
        bm.faces.new([mid_verts[i], mid_verts[j], top_verts[j], top_verts[i]])

    # 先端面（上部→頂点）
    for i in range(sides):
        j = (i + 1) % sides
        bm.faces.new([top_verts[i], top_verts[j], tip])

    bm.to_mesh(mesh)
    bm.free()

    obj.location = location

    # スムーズシェーディング
    for poly in mesh.polygons:
        poly.use_smooth = True

    return obj

# メインの水晶（大）
crystal_main = make_crystal("Crystal_Main", (0, 0, 0), height=3.5, radius=0.45)
# 少し傾ける
crystal_main.rotation_euler = (math.radians(-5), math.radians(3), 0)

# サブ結晶1（中）
crystal_sub1 = make_crystal("Crystal_Sub1", (-0.8, 0.3, 0), height=2.2, radius=0.3)
crystal_sub1.rotation_euler = (math.radians(15), math.radians(-10), math.radians(20))

# サブ結晶2（小）
crystal_sub2 = make_crystal("Crystal_Sub2", (0.6, -0.5, 0), height=1.8, radius=0.25)
crystal_sub2.rotation_euler = (math.radians(-10), math.radians(20), math.radians(-15))

# サブ結晶3（極小）
crystal_sub3 = make_crystal("Crystal_Sub3", (0.3, 0.7, 0), height=1.2, radius=0.18)
crystal_sub3.rotation_euler = (math.radians(20), math.radians(5), math.radians(30))

# サブ結晶4（背面）
crystal_sub4 = make_crystal("Crystal_Sub4", (-0.4, -0.6, 0), height=1.5, radius=0.22)
crystal_sub4.rotation_euler = (math.radians(-15), math.radians(-20), math.radians(10))

result = "5 crystals created"
"""
    client.exec_python(crystal_code)
    print("[AG] 水晶モデル5本生成完了")

    # === 水晶マテリアル（ガラスBSDF風の透明マテリアル） ===
    print("[AG] 水晶マテリアル設定...")
    material_code = """
import bpy

def make_crystal_material(name, color, roughness=0.05, ior=1.55, transmission=0.95):
    \"\"\"水晶用の透過マテリアルを作成\"\"\"
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # 既存ノードをクリア
    for node in nodes:
        nodes.remove(node)

    # Output
    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (400, 0)

    # Principled BSDF
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 0)
    bsdf.inputs['Base Color'].default_value = color
    bsdf.inputs['Roughness'].default_value = roughness
    bsdf.inputs['IOR'].default_value = ior
    bsdf.inputs['Transmission Weight'].default_value = transmission
    bsdf.inputs['Specular IOR Level'].default_value = 0.8

    links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
    return mat

# メイン水晶マテリアル（淡い紫 - アメジスト風）
mat_main = make_crystal_material(
    "Crystal_Amethyst",
    color=(0.6, 0.3, 0.8, 1.0),
    roughness=0.03,
    ior=1.544,
    transmission=0.92
)

# サブ水晶マテリアル（透明水晶 - ロッククリスタル風）
mat_clear = make_crystal_material(
    "Crystal_Clear",
    color=(0.95, 0.95, 0.98, 1.0),
    roughness=0.02,
    ior=1.544,
    transmission=0.96
)

# 各結晶にマテリアルを割り当て
for name in ["Crystal_Main"]:
    obj = bpy.data.objects.get(name)
    if obj:
        if obj.data.materials:
            obj.data.materials[0] = mat_main
        else:
            obj.data.materials.append(mat_main)

for name in ["Crystal_Sub1", "Crystal_Sub2", "Crystal_Sub3", "Crystal_Sub4"]:
    obj = bpy.data.objects.get(name)
    if obj:
        if obj.data.materials:
            obj.data.materials[0] = mat_clear
        else:
            obj.data.materials.append(mat_clear)

# 土台（岩）を追加
bpy.ops.mesh.primitive_cube_add(size=3.0, location=(0, 0, -0.3))
rock = bpy.context.active_object
rock.name = "Rock_Base"
rock.scale = (1.5, 1.2, 0.3)

# 岩マテリアル
rock_mat = bpy.data.materials.new(name="Rock")
rock_mat.use_nodes = True
bsdf = rock_mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs['Base Color'].default_value = (0.15, 0.13, 0.12, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.9
if rock.data.materials:
    rock.data.materials[0] = rock_mat
else:
    rock.data.materials.append(rock_mat)

result = "materials applied"
"""
    client.exec_python(material_code)
    print("[AG] マテリアル適用完了")

    # === ライティング（3点照明） ===
    print("[AG] ライティング設定...")
    client.add_light(type="AREA", energy=200.0, location=(3.0, -2.0, 4.0))
    client.add_light(type="AREA", energy=80.0, location=(-3.0, 1.0, 3.0))
    client.add_light(type="POINT", energy=50.0, location=(0.0, -3.0, 1.0))

    # 環境光設定
    env_code = """
import bpy
world = bpy.context.scene.world
if not world:
    world = bpy.data.worlds.new("AG_World")
    bpy.context.scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg:
    bg.inputs['Color'].default_value = (0.02, 0.02, 0.05, 1.0)
    bg.inputs['Strength'].default_value = 0.5
result = "env set"
"""
    client.exec_python(env_code)
    print("[AG] 環境光設定完了")

    # === カメラ設定 ===
    print("[AG] カメラ配置...")
    client.add_camera(
        location=(4.5, -3.5, 3.0),
        rotation=(1.1, 0.0, 0.85)
    )

    # カメラ焦点距離設定
    cam_code = """
import bpy
cam = bpy.context.scene.camera
if cam and cam.data:
    cam.data.lens = 85
    cam.data.dof.use_dof = True
    cam.data.dof.focus_distance = 5.0
    cam.data.dof.aperture_fstop = 2.8
result = "camera set"
"""
    client.exec_python(cam_code)

    # === レンダ設定 ===
    print("[AG] レンダ設定...")
    client.set_render_settings(
        engine="CYCLES",
        device="GPU",
        resolution_x=1920,
        resolution_y=1080,
        samples=256
    )

    # 追加のレンダ設定（ガラスの透過に必要）
    render_code = """
import bpy
scene = bpy.context.scene
scene.cycles.max_bounces = 12
scene.cycles.transparent_max_bounces = 12
scene.cycles.transmission_bounces = 12
scene.cycles.glossy_bounces = 8
scene.cycles.use_denoising = True
result = "render settings updated"
"""
    client.exec_python(render_code)

    # 保存
    final_path = str(WORK_DIR / "crystal_scene.blend")
    client.save_as(final_path)
    print(f"[AG] シーン保存: {final_path}")

    return final_path


def render_crystal(blend_file: str):
    """CLIでレンダリング"""
    cli = BlenderCLI()
    output_path = str((WORK_DIR / "crystal_render.png").resolve())

    print("[AG] レンダリング開始 (Cycles 256 samples)...")
    try:
        result = cli.render(
            blend_file=blend_file,
            output_path=output_path,
            engine="CYCLES",
            device="GPU",
            log_file=str(WORK_DIR / "crystal_render.log"),
            timeout=600,
        )
        print(f"[AG] レンダリング完了: {result}")
        return result
    except Exception as e:
        print(f"[AG] GPU レンダ失敗、CPUにフォールバック: {e}")
        result = cli.render(
            blend_file=blend_file,
            output_path=output_path,
            engine="CYCLES",
            device="CPU",
            log_file=str(WORK_DIR / "crystal_render_cpu.log"),
            timeout=600,
        )
        print(f"[AG] CPUレンダリング完了: {result}")
        return result


def main():
    print("=" * 60)
    print("[AG] 水晶シーン ハイブリッドワークフロー開始")
    print("=" * 60)

    # Step 1: RPC Blender起動（修正済みワークフロー方式）
    from ag_workflow_hybrid import HybridWorkflow
    wf = HybridWorkflow()
    wf.start_rpc_blender()
    wf.wait_rpc_ready(timeout_s=60)

    # Step 2: RPCでシーン構築
    client = RpcClient("127.0.0.1", 8765)
    blend_file = build_crystal_scene(client)

    # Step 3: RPC Blender停止
    try:
        client.shutdown()
    except Exception:
        pass

    time.sleep(1)

    # Step 4: CLIでレンダリング
    result = render_crystal(blend_file)

    print("=" * 60)
    print(f"[AG] 水晶レンダリング完了: {result}")
    print("=" * 60)
    return result


if __name__ == "__main__":
    main()
