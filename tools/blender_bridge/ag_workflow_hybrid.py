"""
ハイブリッドワークフロー - RPC構築 → .blend保存 → CLIレンダ

エージェントが以下の流れでBlender操作を行う:
(a) RPC常駐Blenderに接続してシーンを段階的に構築
(b) チェックポイント.blendを保存
(c) 別プロセスのCLIでレンダリング投入
(d) エラー時はチェックポイントから再開

使用例:
    from ag_workflow_hybrid import HybridWorkflow
    wf = HybridWorkflow()
    wf.run()
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from ag_rpc_client import RpcClient, RpcError
from blender_cli import BlenderCLI, BlenderCLIError
from error_recovery import classify_failure, get_fallback_policy


# デフォルト設定
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_BLENDER_EXE = os.environ.get(
    "BLENDER_EXE",
    r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
)


class HybridWorkflow:
    """RPC構築 + CLIレンダリングのハイブリッドワークフロー"""

    def __init__(
        self,
        blender_exe: str = DEFAULT_BLENDER_EXE,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        work_dir: str = "ag_runs",
        sys_ext_root: Optional[str] = None,
    ):
        self.blender_exe = blender_exe
        self.host = host
        self.port = port
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.sys_ext_root = sys_ext_root
        self.cli = BlenderCLI(blender_exe)
        self._proc: Optional[subprocess.Popen] = None

    def start_rpc_blender(self, log_file: Optional[str] = None) -> subprocess.Popen:
        """RPCサーバー付きでBlenderを起動"""
        env = os.environ.copy()
        if self.sys_ext_root:
            env["BLENDER_SYSTEM_EXTENSIONS"] = self.sys_ext_root

        # RPCサーバー起動スクリプト
        # 重要: --background モードでは bpy.app.timers のイベントループが
        # 動作しないため、RPCキュー処理(_pump_main_thread)が呼ばれない。
        # そのため --background を使わず、最小ウィンドウでGUIモード起動する。
        # スクリプト完了後もBlenderのイベントループが継続するため、
        # persistent=True の timer が正常動作する。
        py = f"""
import bpy
import sys
sys.path.insert(0, r"{Path(__file__).parent}")
from antigravity_bridge import rpc_server
rpc_server.start("{self.host}", {self.port})
print("[AG] RPC READY", flush=True)
# スクリプト終了後、Blenderのイベントループが継続し
# bpy.app.timers で登録された _pump_main_thread が動作する
"""
        log = log_file or str(self.work_dir / "blender_rpc.log")
        stdout_log = str(self.work_dir / "blender_rpc_stdout.log")

        cmd = [
            self.blender_exe,
            # --background を使わない（timersイベントループを有効にするため）
            "--factory-startup",
            "--window-geometry", "0", "0", "1", "1",  # 最小ウィンドウ
            "--log-file", log,
            "--python-expr", py,
        ]

        # stdout=PIPE はBlenderの大量初期化出力でバッファデッドロックを起こすため
        # ファイルにリダイレクトする
        self._stdout_file = open(stdout_log, "w", encoding="utf-8", errors="replace")
        self._proc = subprocess.Popen(
            cmd, env=env,
            stdout=self._stdout_file, stderr=subprocess.STDOUT
        )
        return self._proc

    def wait_rpc_ready(self, timeout_s: float = 30.0) -> None:
        """RPCサーバーの起動を待機"""
        # 接続待機用に短いタイムアウト・最小リトライのクライアントを使用
        client = RpcClient(self.host, self.port, timeout=3.0, max_retries=0)
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            # Blenderプロセスが異常終了していないか確認
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(
                    f"Blenderプロセスが終了しました (rc={self._proc.returncode})。"
                    f"ログを確認: {self.work_dir / 'blender_rpc_stdout.log'}"
                )
            try:
                result = client.ping()
                if result.get("ok"):
                    print(f"[AG] RPC接続成功: Blender {result.get('blender', '?')}")
                    return
            except Exception:
                time.sleep(0.5)
        raise TimeoutError(f"RPCサーバーが{timeout_s}秒以内に応答しませんでした")

    def build_scene_via_rpc(self, client: RpcClient) -> dict:
        """RPCを通じてシーンを構築（チェックポイント付き）"""
        checkpoints = {}

        # ステップ1: シーン初期化
        client.reset_scene()
        ckpt0 = str(self.work_dir / "scene_step_000_init.blend")
        client.save_as(ckpt0)
        checkpoints["init"] = ckpt0
        print("[AG] チェックポイント0: シーン初期化")

        # ステップ2: オブジェクト配置
        cube = client.add_cube(size=2.0, location=(0, 0, 1))
        floor = client.add_plane(size=20.0, location=(0, 0, 0))
        ckpt1 = str(self.work_dir / "scene_step_010_geometry.blend")
        client.save_as(ckpt1)
        checkpoints["geometry"] = ckpt1
        print(f"[AG] チェックポイント1: ジオメトリ配置 ({cube['name']}, {floor['name']})")

        # ステップ3: マテリアル
        mat = client.make_material(name="AG_Blue", base_color=(0.1, 0.4, 0.9, 1.0))
        client.assign_material(cube["name"], mat["material"])
        floor_mat = client.make_material(name="AG_Floor", base_color=(0.8, 0.8, 0.8, 1.0))
        client.assign_material(floor["name"], floor_mat["material"])
        ckpt2 = str(self.work_dir / "scene_step_020_material.blend")
        client.save_as(ckpt2)
        checkpoints["material"] = ckpt2
        print("[AG] チェックポイント2: マテリアル適用")

        # ステップ4: カメラ＋ライト
        client.add_camera()
        client.add_light(type="SUN", energy=5.0)
        ckpt3 = str(self.work_dir / "scene_step_030_lighting.blend")
        client.save_as(ckpt3)
        checkpoints["lighting"] = ckpt3
        print("[AG] チェックポイント3: カメラ＋ライト配置")

        # ステップ5: レンダ設定
        client.set_render_settings(
            engine="CYCLES", device="GPU",
            resolution_x=1920, resolution_y=1080,
            samples=128
        )
        final = str(self.work_dir / "scene_final.blend")
        client.save_as(final)
        checkpoints["final"] = final
        print("[AG] 最終チェックポイント: シーン完了")

        return checkpoints

    def render_via_cli(
        self,
        blend_file: str,
        output_path: str,
        log_file: Optional[str] = None,
        max_retries: int = 3,
    ) -> Path:
        """CLIでレンダリング（リトライ＋フォールバック付き）"""
        log = log_file or str(self.work_dir / "blender_render.log")
        policy = {"device": "GPU", "samples": 128}

        for attempt in range(1, max_retries + 1):
            try:
                print(f"[AG] レンダ試行 {attempt}/{max_retries}: "
                      f"device={policy['device']}, samples={policy['samples']}")

                result = self.cli.render(
                    blend_file=blend_file,
                    output_path=output_path,
                    engine="CYCLES",
                    device=policy["device"],
                    log_file=log,
                    timeout=600,
                )
                print(f"[AG] レンダ成功: {result}")
                return result

            except BlenderCLIError as e:
                print(f"[AG] レンダ失敗 (試行{attempt}): {e}")

                if attempt < max_retries:
                    # ログからエラー分類→フォールバックポリシー適用
                    failure_type = classify_failure(log)
                    policy = get_fallback_policy(policy, failure_type)
                    print(f"[AG] フォールバック: {failure_type} → {policy}")
                else:
                    raise

    def run(self) -> Path:
        """ハイブリッドワークフロー全体を実行"""
        print("=" * 60)
        print("[AG] ハイブリッドワークフロー開始")
        print("=" * 60)

        try:
            # (a) RPC常駐Blender起動
            self.start_rpc_blender()
            self.wait_rpc_ready(timeout_s=40)

            # (b) RPCでシーン構築
            client = RpcClient(self.host, self.port)
            checkpoints = self.build_scene_via_rpc(client)

            # RPCサーバー停止
            try:
                client.shutdown()
            except Exception:
                pass  # shutdown後は接続が切れるので無視

        except (RpcError, TimeoutError) as e:
            print(f"[AG] RPC構築失敗: {e}")
            if self._proc and self._proc.poll() is None:
                self._proc.kill()
            raise RuntimeError(f"RPC構築フェーズ失敗: {e}") from e

        # (c) CLIでレンダリング
        final_blend = checkpoints["final"]
        render_out = str(self.work_dir / "render_0001.png")

        result = self.render_via_cli(
            blend_file=final_blend,
            output_path=render_out,
        )

        print("=" * 60)
        print(f"[AG] ワークフロー完了: {result}")
        print("=" * 60)
        return result


if __name__ == "__main__":
    wf = HybridWorkflow()
    wf.run()
