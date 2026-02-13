"""水晶シーン豪華化 Step 5-6 修正版"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
os.environ["AG_RPC_ENABLE_EXEC_PYTHON"] = "true"
from ag_rpc_client import RpcClient

client = RpcClient("127.0.0.1", 8765, timeout=10.0, max_retries=1)
print(f"[AG] RPC接続: {client.ping()}")

# === Step 5: 背景グラデーション（修正版） ===
print("[AG] Step 5: 背景グラデーション...")
try:
    client.exec_python("""
import bpy

# 新しいワールドを作成
world = bpy.data.worlds.new("CrystalWorld")
bpy.context.scene.world = world
world.use_nodes = True
nodes = world.node_tree.nodes
links = world.node_tree.links

# 既存ノードクリア
for n in list(nodes):
    nodes.remove(n)

# Background
bg = nodes.new('ShaderNodeBackground')
bg.location = (0, 0)
bg.inputs['Strength'].default_value = 1.0

# Output
output = nodes.new('ShaderNodeOutputWorld')
output.location = (400, 0)

# グラデーション用カラーランプ  
ramp = nodes.new('ShaderNodeValToRGB')
ramp.location = (-200, 0)
ramp.color_ramp.elements[0].color = (0.003, 0.003, 0.015, 1.0)
ramp.color_ramp.elements[0].position = 0.0
ramp.color_ramp.elements[1].color = (0.04, 0.015, 0.08, 1.0)
ramp.color_ramp.elements[1].position = 1.0

# テクスチャ座標
tex_coord = nodes.new('ShaderNodeTexCoord')
tex_coord.location = (-600, 0)

sep = nodes.new('ShaderNodeSeparateXYZ')
sep.location = (-400, 0)

links.new(tex_coord.outputs['Generated'], sep.inputs['Vector'])
links.new(sep.outputs['Z'], ramp.inputs['Fac'])
links.new(ramp.outputs['Color'], bg.inputs['Color'])
links.new(bg.outputs['Background'], output.inputs['Surface'])

# ボリューム散乱も追加
vol = nodes.new('ShaderNodeVolumeScatter')
vol.location = (0, -200)
vol.inputs['Color'].default_value = (0.5, 0.4, 0.7, 1.0)
vol.inputs['Density'].default_value = 0.015
vol.inputs['Anisotropy'].default_value = 0.5
links.new(vol.outputs['Volume'], output.inputs['Volume'])

result = "world created"
""")
    print("[AG] 背景グラデーション完了")
except Exception as e:
    print(f"[AG] Step 5 スキップ: {e}")

# === Step 6: Bloom（コンポジター）===
print("[AG] Step 6: Bloom設定...")
try:
    client.exec_python("""
import bpy
scene = bpy.context.scene

# レンダ品質
scene.cycles.max_bounces = 16
scene.cycles.transparent_max_bounces = 16
scene.cycles.transmission_bounces = 16
scene.cycles.glossy_bounces = 12
scene.cycles.volume_bounces = 4
scene.cycles.use_denoising = True
scene.cycles.samples = 256

# コンポジター有効化
scene.use_nodes = True
tree = scene.node_tree
nodes = tree.nodes
links = tree.links

for n in list(nodes):
    nodes.remove(n)

rl = nodes.new('CompositorNodeRLayers')
rl.location = (0, 0)

glare = nodes.new('CompositorNodeGlare')
glare.location = (300, 0)
glare.glare_type = 'FOG_GLOW'
glare.quality = 'HIGH'
glare.threshold = 0.8
glare.size = 7

comp = nodes.new('CompositorNodeComposite')
comp.location = (600, 0)

links.new(rl.outputs['Image'], glare.inputs['Image'])
links.new(glare.outputs['Image'], comp.inputs['Image'])

result = "bloom compositing set"
""")
    print("[AG] Bloom設定完了")
except Exception as e:
    print(f"[AG] Step 6 スキップ: {e}")

# 保存
print("[AG] 保存中...")
save_path = str(Path("ag_runs/crystal_scene_deluxe.blend").resolve()).replace("\\", "\\\\")
client.exec_python(f'import bpy; bpy.ops.wm.save_as_mainfile(filepath=r"{save_path}")\nresult = "saved"')
print(f"[AG] 保存完了!")

# レンダリング
print("[AG] 豪華版レンダリング開始...")
from blender_cli import BlenderCLI
cli = BlenderCLI()
blend = str(Path("ag_runs/crystal_scene_deluxe.blend").resolve())
out = str(Path("ag_runs/crystal_deluxe_render.png").resolve())

try:
    result = cli.render(
        blend_file=blend, output_path=out,
        engine="CYCLES", device="GPU",
        log_file=str(Path("ag_runs/crystal_deluxe_render.log").resolve()),
        timeout=600,
    )
    print(f"[AG] ✨ レンダリング完了: {result}")
except Exception as e:
    print(f"[AG] GPU失敗、CPUフォールバック: {e}")
    result = cli.render(
        blend_file=blend, output_path=out,
        engine="CYCLES", device="CPU",
        log_file=str(Path("ag_runs/crystal_deluxe_cpu.log").resolve()),
        timeout=600,
    )
    print(f"[AG] ✨ CPUレンダリング完了: {result}")
