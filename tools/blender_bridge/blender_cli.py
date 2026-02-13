"""
Blender CLI ラッパーモジュール

Antigravityエージェントから Blender 5.0 を --background モードで
安全に呼び出すためのユーティリティ。

使用例:
    from blender_cli import BlenderCLI
    cli = BlenderCLI()
    cli.run_script("path/to/script.py")
    cli.render("scene.blend", "output.png", frame=1)
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Optional


# デフォルトのBlenderパス（環境変数で上書き可能）
DEFAULT_BLENDER_EXE = os.environ.get(
    "BLENDER_EXE",
    r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
)


class BlenderCLIError(RuntimeError):
    """Blender CLI実行時のエラー"""
    def __init__(self, message: str, returncode: int = -1, stdout: str = "", log_path: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.log_path = log_path


class BlenderCLI:
    """Blender CLIラッパー"""

    def __init__(self, blender_exe: str = DEFAULT_BLENDER_EXE):
        self.blender_exe = blender_exe
        self._validate_exe()

    def _validate_exe(self):
        """Blender実行ファイルの存在確認"""
        if not Path(self.blender_exe).exists():
            raise FileNotFoundError(f"Blenderが見つかりません: {self.blender_exe}")

    def version(self) -> str:
        """Blenderバージョンを取得"""
        result = subprocess.run(
            [self.blender_exe, "--version"],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip().split("\n")[0]

    def run_script(
        self,
        script_path: str,
        blend_file: Optional[str] = None,
        args: Optional[list] = None,
        log_file: Optional[str] = None,
        timeout: int = 300,
        factory_startup: bool = True,
        env_extra: Optional[dict] = None,
    ) -> subprocess.CompletedProcess:
        """
        Blender --background でPythonスクリプトを実行

        Args:
            script_path: 実行するPythonスクリプトのパス
            blend_file: 開く.blendファイル（省略時は空シーン）
            args: スクリプトに渡す追加引数（-- の後に追加される）
            log_file: ログ出力先ファイル
            timeout: タイムアウト秒数
            factory_startup: 工場出荷時設定で起動するか
            env_extra: 追加環境変数
        """
        cmd = [self.blender_exe, "--background"]

        if factory_startup:
            cmd.append("--factory-startup")

        if blend_file:
            cmd.append(blend_file)

        if log_file:
            cmd.extend(["--log-file", log_file])

        cmd.extend(["--python", script_path])

        # スクリプトへの追加引数
        if args:
            cmd.append("--")
            cmd.extend(args)

        # 環境変数
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, env=env,
            encoding="utf-8", errors="replace"
        )

        if result.returncode != 0:
            raise BlenderCLIError(
                f"スクリプト実行失敗 (rc={result.returncode}): {script_path}",
                returncode=result.returncode,
                stdout=result.stdout + "\n" + result.stderr,
                log_path=log_file or ""
            )

        return result

    def run_python_expr(
        self,
        expr: str,
        blend_file: Optional[str] = None,
        log_file: Optional[str] = None,
        timeout: int = 60,
        factory_startup: bool = True,
        env_extra: Optional[dict] = None,
    ) -> subprocess.CompletedProcess:
        """
        Blender --background --python-expr でPython式を実行

        Args:
            expr: 実行するPython式（複数行可）
        """
        cmd = [self.blender_exe, "--background"]

        if factory_startup:
            cmd.append("--factory-startup")

        if blend_file:
            cmd.append(blend_file)

        if log_file:
            cmd.extend(["--log-file", log_file])

        cmd.extend(["--python-expr", expr])

        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, env=env,
            encoding="utf-8", errors="replace"
        )

        if result.returncode != 0:
            raise BlenderCLIError(
                f"Python式実行失敗 (rc={result.returncode})",
                returncode=result.returncode,
                stdout=result.stdout + "\n" + result.stderr,
                log_path=log_file or ""
            )

        return result

    def render(
        self,
        blend_file: str,
        output_path: str,
        frame: int = 1,
        engine: Optional[str] = None,
        device: str = "GPU",
        log_file: Optional[str] = None,
        timeout: int = 600,
        env_extra: Optional[dict] = None,
    ) -> Path:
        """
        Blender CLIでレンダリング実行

        Args:
            blend_file: レンダリング対象の.blendファイル
            output_path: 出力先パス（####でフレーム番号置換）
            frame: レンダリングするフレーム番号
            engine: レンダリングエンジン（CYCLES, BLENDER_EEVEE_NEXT等）
            device: デバイス（GPU/CPU）
            log_file: ログ出力先
            timeout: タイムアウト秒数

        Returns:
            出力ファイルのPath
        """
        # 出力パスを絶対パスに変換
        output_path = str(Path(output_path).resolve())

        # 出力ディレクトリ作成
        out_dir = Path(output_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)

        # Blenderのfilepathは拡張子なしのベース名を設定する
        # Blenderが自動でフレーム番号と拡張子を付加する
        output_stem = Path(output_path).stem  # 例: render_0001
        output_base = str(out_dir / output_stem)  # 例: C:\...\ag_runs\render_0001

        # レンダ設定をPythonで注入
        setup_expr = f"""
import bpy
scene = bpy.context.scene

# 出力設定（拡張子なしのベース名。Blenderがフレーム番号と拡張子を追加）
scene.render.filepath = r"{output_base}"
scene.render.image_settings.file_format = "PNG"

# エンジン設定
{"scene.render.engine = '" + engine + "'" if engine else "# エンジンはblendファイルの設定を使用"}

# デバイス設定（Cyclesの場合）
if scene.render.engine == "CYCLES":
    scene.cycles.device = "{device}"
    # OptiX設定（RTX 5080向け）
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs:
        prefs.preferences.compute_device_type = "OPTIX"
        # 利用可能なデバイスを有効化
        prefs.preferences.get_devices()
        for d in prefs.preferences.devices:
            d.use = True

print("[AG] レンダ設定完了")
"""

        cmd = [
            self.blender_exe,
            "--background",
            blend_file,
        ]

        if log_file:
            cmd.extend(["--log-file", log_file])

        cmd.extend([
            "--python-expr", setup_expr,
            "--render-frame", str(frame),
        ])

        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, env=env,
            encoding="utf-8", errors="replace"
        )

        if result.returncode != 0:
            raise BlenderCLIError(
                f"レンダリング失敗 (rc={result.returncode})",
                returncode=result.returncode,
                stdout=result.stdout + "\n" + result.stderr,
                log_path=log_file or ""
            )

        # 出力確認 — Blenderの出力ファイル名パターンを探す
        # Blenderは "{filepath}{frame:04d}.png" 形式で保存する
        candidates = [
            Path(f"{output_base}{frame:04d}.png"),  # render_00010001.png
            Path(output_path),                       # render_0001.png（直接指定）
            Path(output_path.replace("####", f"{frame:04d}")),  # ####パターン
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.stat().st_size > 1000:
                return candidate

        raise BlenderCLIError(
            f"レンダ出力が見つからないか、サイズが小さすぎます。候補: {[str(c) for c in candidates]}",
            log_path=log_file or ""
        )

    def smoke_test(self) -> dict:
        """
        Blenderの基本動作確認

        Returns:
            {"version": str, "python": str, "ok": bool}
        """
        expr = """
import bpy
import sys
print(f"BLENDER_VERSION={bpy.app.version_string}")
print(f"PYTHON_VERSION={sys.version.split()[0]}")
print("SMOKE_TEST_OK=1")
"""
        try:
            result = self.run_python_expr(expr, timeout=30)
            info = {}
            for line in result.stdout.split("\n"):
                if "=" in line and line.startswith(("BLENDER_VERSION", "PYTHON_VERSION", "SMOKE_TEST_OK")):
                    k, v = line.strip().split("=", 1)
                    info[k] = v
            return {
                "version": info.get("BLENDER_VERSION", "unknown"),
                "python": info.get("PYTHON_VERSION", "unknown"),
                "ok": info.get("SMOKE_TEST_OK") == "1"
            }
        except Exception as e:
            return {"version": "unknown", "python": "unknown", "ok": False, "error": str(e)}


if __name__ == "__main__":
    # スモークテスト実行
    cli = BlenderCLI()
    result = cli.smoke_test()
    print(f"Blender: {result['version']}")
    print(f"Python:  {result['python']}")
    print(f"OK:      {result['ok']}")
