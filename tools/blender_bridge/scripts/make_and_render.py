"""
make_and_render.py - Blenderパイプラインスクリプト

Blender内で実行され、以下の処理を行う:
1. シーン初期化
2. オブジェクト作成（キューブ＋マテリアル）
3. カメラ＋ライト配置
4. レンダリング設定（Cycles, OptiX, RTX 5080向け）
5. レンダリング実行

使用例:
    blender --background --factory-startup --python make_and_render.py -- --output render.png
"""

import sys
import argparse
from pathlib import Path

import bpy


def parse_args():
    """コマンドライン引数をパース（-- の後の引数を処理）"""
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(description="Blender パイプラインスクリプト")
    parser.add_argument("--output", "-o", default="render_####.png", help="出力パス")
    parser.add_argument("--frame", "-f", type=int, default=1, help="レンダリングフレーム")
    parser.add_argument("--engine", default="CYCLES", help="レンダリングエンジン")
    parser.add_argument("--device", default="GPU", help="デバイス (GPU/CPU)")
    parser.add_argument("--samples", type=int, default=128, help="サンプル数")
    parser.add_argument("--resolution-x", type=int, default=1920, help="解像度X")
    parser.add_argument("--resolution-y", type=int, default=1080, help="解像度Y")
    parser.add_argument("--save-blend", default=None, help=".blendファイルの保存先")
    return parser.parse_args(argv)


def setup_scene():
    """シーンを初期化してオブジェクトを配置"""
    # シーン初期化
    bpy.ops.wm.read_factory_settings(use_empty=True)
    print("[AG] シーン初期化完了")

    # オブジェクト作成: キューブ
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1))
    cube = bpy.context.active_object
    cube.name = "AG_Cube"
    print(f"[AG] キューブ作成: {cube.name}")

    # マテリアル作成
    mat = bpy.data.materials.new(name="AG_Material")
    # Blender 5.0以降はuse_nodesは常にTrue（6.0で削除予定）
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        # 鮮やかなブルー
        bsdf.inputs["Base Color"].default_value = (0.1, 0.4, 0.9, 1.0)
        bsdf.inputs["Metallic"].default_value = 0.3
        bsdf.inputs["Roughness"].default_value = 0.2
    cube.data.materials.append(mat)
    print("[AG] マテリアル適用完了")

    # 床面追加
    bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "AG_Floor"
    floor_mat = bpy.data.materials.new(name="AG_Floor_Mat")
    floor_mat.use_nodes = True
    floor_bsdf = floor_mat.node_tree.nodes.get("Principled BSDF")
    if floor_bsdf:
        floor_bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
        floor_bsdf.inputs["Roughness"].default_value = 0.9
    floor.data.materials.append(floor_mat)

    # カメラ追加
    bpy.ops.object.camera_add(
        location=(7.36, -6.93, 4.96),
        rotation=(1.1, 0.0, 0.81)
    )
    camera = bpy.context.active_object
    camera.name = "AG_Camera"
    bpy.context.scene.camera = camera
    print("[AG] カメラ配置完了")

    # サンライト追加
    bpy.ops.object.light_add(type="SUN", location=(4.07, 1.0, 5.9))
    sun = bpy.context.active_object
    sun.name = "AG_Sun"
    sun.data.energy = 5.0
    print("[AG] ライト配置完了")

    return cube


def setup_render(args):
    """レンダリング設定"""
    scene = bpy.context.scene

    # エンジン設定
    scene.render.engine = args.engine
    scene.render.resolution_x = args.resolution_x
    scene.render.resolution_y = args.resolution_y
    scene.render.image_settings.file_format = "PNG"

    # 出力パス
    output_dir = Path(args.output).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = args.output

    # Cycles固有設定
    if args.engine == "CYCLES":
        scene.cycles.device = args.device
        scene.cycles.samples = args.samples

        # OptiX設定（RTX 5080向け）
        if args.device == "GPU":
            prefs = bpy.context.preferences.addons.get("cycles")
            if prefs:
                prefs.preferences.compute_device_type = "OPTIX"
                prefs.preferences.get_devices()
                for d in prefs.preferences.devices:
                    d.use = True
                print("[AG] OptiX (RTX 5080) 設定完了")
            else:
                print("[AG] 警告: Cycles設定にアクセスできません。CPUフォールバック")
                scene.cycles.device = "CPU"

    print(f"[AG] レンダ設定: engine={args.engine}, device={args.device}, "
          f"samples={args.samples}, res={args.resolution_x}x{args.resolution_y}")


def main():
    args = parse_args()

    # シーン構築
    setup_scene()

    # レンダ設定
    setup_render(args)

    # .blend保存（チェックポイント）
    if args.save_blend:
        blend_dir = Path(args.save_blend).parent
        blend_dir.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=args.save_blend)
        print(f"[AG] .blend保存: {args.save_blend}")

    # レンダリング実行
    print(f"[AG] レンダリング開始: frame={args.frame}")
    bpy.ops.render.render(write_still=True)
    print(f"[AG] レンダリング完了: {args.output}")


if __name__ == "__main__":
    main()
