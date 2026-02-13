"""水晶シーン豪華化 — RPC経由でリアルタイム強化"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
os.environ["AG_RPC_ENABLE_EXEC_PYTHON"] = "true"
from ag_rpc_client import RpcClient

client = RpcClient("127.0.0.1", 8765, timeout=10.0, max_retries=1)

# テスト接続
r = client.ping()
print(f"[AG] RPC接続確認: Blender {r.get('blender', '?')}")

# === Step 1: 追加の小さな結晶を散りばめる ===
print("[AG] Step 1: 小結晶を追加...")
client.exec_python("""
import bpy, bmesh, math, random
random.seed(123)

def make_small_crystal(name, loc, h, r):
    mesh = bpy.data.meshes.new(f"{name}_m")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    sides = 6
    bv = [bm.verts.new((r*math.cos(math.radians(i*60)), r*math.sin(math.radians(i*60)), 0)) for i in range(sides)]
    mv = [bm.verts.new((r*0.9*math.cos(math.radians(i*60+5)), r*0.9*math.sin(math.radians(i*60+5)), h*0.6)) for i in range(sides)]
    tv = [bm.verts.new((r*0.35*math.cos(math.radians(i*60+10)), r*0.35*math.sin(math.radians(i*60+10)), h*0.85)) for i in range(sides)]
    tip = bm.verts.new((0, 0, h))
    bm.faces.new(bv)
    for i in range(sides):
        j = (i+1) % sides
        bm.faces.new([bv[i], bv[j], mv[j], mv[i]])
        bm.faces.new([mv[i], mv[j], tv[j], tv[i]])
        bm.faces.new([tv[i], tv[j], tip])
    bm.to_mesh(mesh)
    bm.free()
    obj.location = loc
    for p in mesh.polygons:
        p.use_smooth = True
    return obj

# 10本の小結晶をランダム配置
for i in range(10):
    x = random.uniform(-1.5, 1.5)
    y = random.uniform(-1.5, 1.5)
    h = random.uniform(0.4, 1.0)
    r = random.uniform(0.06, 0.15)
    rx = math.radians(random.uniform(-25, 25))
    ry = math.radians(random.uniform(-25, 25))
    rz = math.radians(random.uniform(0, 360))
    c = make_small_crystal(f"SmallCrystal_{i:02d}", (x, y, -0.1), h, r)
    c.rotation_euler = (rx, ry, rz)

result = "10 small crystals added"
""")
print("[AG] 小結晶10本追加完了")

# === Step 2: エミッション（発光）マテリアルで内側からの光 ===
print("[AG] Step 2: 発光マテリアル追加...")
client.exec_python("""
import bpy

# アメジスト発光マテリアル
mat_glow = bpy.data.materials.new(name="Crystal_Glow_Amethyst")
mat_glow.use_nodes = True
nodes = mat_glow.node_tree.nodes
links = mat_glow.node_tree.links
for n in nodes:
    nodes.remove(n)

output = nodes.new('ShaderNodeOutputMaterial')
output.location = (600, 0)

# Mix: Principled + Emission
mix = nodes.new('ShaderNodeMixShader')
mix.location = (400, 0)
mix.inputs[0].default_value = 0.15  # 15% emission

bsdf = nodes.new('ShaderNodeBsdfPrincipled')
bsdf.location = (0, 100)
bsdf.inputs['Base Color'].default_value = (0.55, 0.2, 0.85, 1.0)
bsdf.inputs['Roughness'].default_value = 0.02
bsdf.inputs['IOR'].default_value = 1.544
bsdf.inputs['Transmission Weight'].default_value = 0.92
bsdf.inputs['Specular IOR Level'].default_value = 0.8

emission = nodes.new('ShaderNodeEmission')
emission.location = (0, -100)
emission.inputs['Color'].default_value = (0.8, 0.3, 1.0, 1.0)
emission.inputs['Strength'].default_value = 3.0

links.new(bsdf.outputs['BSDF'], mix.inputs[1])
links.new(emission.outputs['Emission'], mix.inputs[2])
links.new(mix.outputs['Shader'], output.inputs['Surface'])

# メイン水晶に適用
obj = bpy.data.objects.get("Crystal_Main")
if obj:
    if obj.data.materials:
        obj.data.materials[0] = mat_glow
    else:
        obj.data.materials.append(mat_glow)

# クリアクォーツ発光マテリアル
mat_glow_clear = bpy.data.materials.new(name="Crystal_Glow_Clear")
mat_glow_clear.use_nodes = True
nodes2 = mat_glow_clear.node_tree.nodes
links2 = mat_glow_clear.node_tree.links
for n in nodes2:
    nodes2.remove(n)

out2 = nodes2.new('ShaderNodeOutputMaterial')
out2.location = (600, 0)
mix2 = nodes2.new('ShaderNodeMixShader')
mix2.location = (400, 0)
mix2.inputs[0].default_value = 0.08

bsdf2 = nodes2.new('ShaderNodeBsdfPrincipled')
bsdf2.location = (0, 100)
bsdf2.inputs['Base Color'].default_value = (0.92, 0.92, 1.0, 1.0)
bsdf2.inputs['Roughness'].default_value = 0.01
bsdf2.inputs['IOR'].default_value = 1.544
bsdf2.inputs['Transmission Weight'].default_value = 0.96
bsdf2.inputs['Specular IOR Level'].default_value = 0.9

em2 = nodes2.new('ShaderNodeEmission')
em2.location = (0, -100)
em2.inputs['Color'].default_value = (0.7, 0.85, 1.0, 1.0)
em2.inputs['Strength'].default_value = 1.5

links2.new(bsdf2.outputs['BSDF'], mix2.inputs[1])
links2.new(em2.outputs['Emission'], mix2.inputs[2])
links2.new(mix2.outputs['Shader'], out2.inputs['Surface'])

# サブ水晶＋小結晶に適用
for name in ["Crystal_Sub1", "Crystal_Sub2", "Crystal_Sub3", "Crystal_Sub4"] + [f"SmallCrystal_{i:02d}" for i in range(10)]:
    obj = bpy.data.objects.get(name)
    if obj:
        if obj.data.materials:
            obj.data.materials[0] = mat_glow_clear
        else:
            obj.data.materials.append(mat_glow_clear)

result = "glow materials applied"
""")
print("[AG] 発光マテリアル適用完了")

# === Step 3: 土台の岩を改善 + 金の鉱脈テクスチャ ===
print("[AG] Step 3: 岩テクスチャ強化...")
client.exec_python("""
import bpy

rock = bpy.data.objects.get("Rock_Base")
if rock:
    # サブディビジョンモディファイアで滑らかに
    sub = rock.modifiers.new("Subdivision", 'SUBSURF')
    sub.levels = 2
    sub.render_levels = 3

    # ディスプレイスメントで岩のゴツゴツ感
    disp = rock.modifiers.new("Displace", 'DISPLACE')
    tex = bpy.data.textures.new("RockNoise", type='VORONOI')
    tex.noise_scale = 0.5
    tex.intensity = 0.8
    disp.texture = tex
    disp.strength = 0.15

    # 金の鉱脈入り岩マテリアル
    mat = bpy.data.materials.new(name="Rock_Gold_Vein")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    for n in nodes:
        nodes.remove(n)

    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (800, 0)

    mix_shader = nodes.new('ShaderNodeMixShader')
    mix_shader.location = (600, 0)

    # 岩のベース
    rock_bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    rock_bsdf.location = (200, 200)
    rock_bsdf.inputs['Base Color'].default_value = (0.08, 0.07, 0.06, 1.0)
    rock_bsdf.inputs['Roughness'].default_value = 0.95

    # 金の鉱脈
    gold_bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    gold_bsdf.location = (200, -200)
    gold_bsdf.inputs['Base Color'].default_value = (0.95, 0.75, 0.2, 1.0)
    gold_bsdf.inputs['Metallic'].default_value = 1.0
    gold_bsdf.inputs['Roughness'].default_value = 0.3

    # ノイズテクスチャでマスク
    noise = nodes.new('ShaderNodeTexNoise')
    noise.location = (-200, 0)
    noise.inputs['Scale'].default_value = 8.0
    noise.inputs['Detail'].default_value = 12.0

    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.location = (0, 0)
    ramp.color_ramp.elements[0].position = 0.55
    ramp.color_ramp.elements[1].position = 0.6

    links.new(noise.outputs['Fac'], ramp.inputs['Fac'])
    links.new(ramp.outputs['Color'], mix_shader.inputs[0])
    links.new(rock_bsdf.outputs['BSDF'], mix_shader.inputs[1])
    links.new(gold_bsdf.outputs['BSDF'], mix_shader.inputs[2])
    links.new(mix_shader.outputs['Shader'], output.inputs['Surface'])

    if rock.data.materials:
        rock.data.materials[0] = mat
    else:
        rock.data.materials.append(mat)

result = "rock enhanced"
""")
print("[AG] 岩テクスチャ強化完了")

# === Step 4: ボリュームライト（ゴッドレイ風） ===
print("[AG] Step 4: ボリュームライト追加...")
client.exec_python("""
import bpy

# スポットライトを追加（ゴッドレイの光源）
bpy.ops.object.light_add(type='SPOT', location=(2.0, -1.5, 5.0))
spot = bpy.context.active_object
spot.name = "GodRay_Spot"
spot.data.energy = 500
spot.data.spot_size = 0.6  # 約34度
spot.data.spot_blend = 0.3
spot.data.shadow_soft_size = 0.5
spot.data.color = (0.9, 0.8, 1.0)

# スポットライトをシーン中心に向ける
from mathutils import Vector
direction = Vector((0, 0, 1.5)) - spot.location
spot.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

# ワールドボリューム（霧のような散乱）
world = bpy.context.scene.world
if world and world.use_nodes:
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    
    output = None
    for n in nodes:
        if n.type == 'OUTPUT_WORLD':
            output = n
            break
    
    if output:
        vol_scatter = nodes.new('ShaderNodeVolumeScatter')
        vol_scatter.location = (-200, -200)
        vol_scatter.inputs['Color'].default_value = (0.6, 0.5, 0.8, 1.0)
        vol_scatter.inputs['Density'].default_value = 0.02
        vol_scatter.inputs['Anisotropy'].default_value = 0.6
        links.new(vol_scatter.outputs['Volume'], output.inputs['Volume'])

result = "volumetric lighting added"
""")
print("[AG] ボリュームライト追加完了")

# === Step 5: 背景グラデーション強化 ===
print("[AG] Step 5: 背景強化...")
client.exec_python("""
import bpy

world = bpy.context.scene.world
if world and world.use_nodes:
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    
    bg = nodes.get("Background")
    if bg:
        # グラデーションテクスチャ
        tex_coord = nodes.new('ShaderNodeTexCoord')
        tex_coord.location = (-600, 300)
        
        gradient = nodes.new('ShaderNodeTexGradient')
        gradient.location = (-400, 300)
        gradient.gradient_type = 'SPHERICAL'
        
        ramp = nodes.new('ShaderNodeValToRGB')
        ramp.location = (-200, 300)
        ramp.color_ramp.elements[0].color = (0.005, 0.005, 0.02, 1.0)
        ramp.color_ramp.elements[0].position = 0.0
        ramp.color_ramp.elements[1].color = (0.05, 0.02, 0.1, 1.0)
        ramp.color_ramp.elements[1].position = 1.0
        
        links.new(tex_coord.outputs['Generated'], gradient.inputs['Vector'])
        links.new(gradient.outputs['Fac'], ramp.inputs['Fac'])
        links.new(ramp.outputs['Color'], bg.inputs['Color'])
        bg.inputs['Strength'].default_value = 1.0

result = "background enhanced"
""")
print("[AG] 背景グラデーション適用完了")

# === Step 6: レンダ設定強化 ===
print("[AG] Step 6: レンダ品質向上...")
client.exec_python("""
import bpy
scene = bpy.context.scene

# ボリュメトリクス有効化
scene.cycles.max_bounces = 16
scene.cycles.transparent_max_bounces = 16
scene.cycles.transmission_bounces = 16
scene.cycles.glossy_bounces = 12
scene.cycles.volume_bounces = 4
scene.cycles.use_denoising = True
scene.cycles.samples = 256

# Bloom風のグレア（コンポジター）
scene.use_nodes = True
tree = scene.node_tree
nodes = tree.nodes
links = tree.links

# 既存ノードをクリーンアップ
for n in nodes:
    nodes.remove(n)

# Render Layers
rl = nodes.new('CompositorNodeRLayers')
rl.location = (0, 0)

# Glare（ゴースト/フォググロー）
glare = nodes.new('CompositorNodeGlare')
glare.location = (300, 0)
glare.glare_type = 'FOG_GLOW'
glare.quality = 'HIGH'
glare.threshold = 0.8
glare.size = 7

# Composite出力
comp = nodes.new('CompositorNodeComposite')
comp.location = (600, 0)

# Viewer
viewer = nodes.new('CompositorNodeViewer')
viewer.location = (600, -200)

links.new(rl.outputs['Image'], glare.inputs['Image'])
links.new(glare.outputs['Image'], comp.inputs['Image'])
links.new(glare.outputs['Image'], viewer.inputs['Image'])

result = "render quality enhanced with bloom"
""")
print("[AG] レンダ品質＋Bloom設定完了")

# 保存
print("[AG] シーン保存...")
save_path = str(Path("ag_runs/crystal_scene_deluxe.blend").resolve()).replace("\\", "\\\\")
client.exec_python(f'bpy.ops.wm.save_as_mainfile(filepath=r"{save_path}")\nresult = "saved"')
print(f"[AG] 保存完了: {save_path}")
print("[AG] ✨ 豪華化完了！Blenderでプレビューしてください")
