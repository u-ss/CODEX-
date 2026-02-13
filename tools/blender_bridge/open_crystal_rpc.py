"""RPCでBlenderを起動し、水晶シーンを開く"""
import os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ["AG_RPC_ENABLE_EXEC_PYTHON"] = "true"

from ag_rpc_client import RpcClient
import subprocess

# Blenderを通常ウィンドウで起動（--backgroundなし、最小化なし）
blender_exe = os.environ.get("BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe")
bridge_dir = str(Path(__file__).parent)

py_script = f'''
import bpy, sys
sys.path.insert(0, r"{bridge_dir}")
from antigravity_bridge import rpc_server
rpc_server.start("127.0.0.1", 8765)
print("[AG] RPC READY", flush=True)
'''

env = os.environ.copy()
cmd = [blender_exe, "--factory-startup", "--python-expr", py_script]

# 通常ウィンドウで起動（stdoutはログへ）
log_path = str(Path("ag_runs") / "blender_rpc_open.log")
stdout_file = open(log_path, "w", encoding="utf-8", errors="replace")
proc = subprocess.Popen(cmd, env=env, stdout=stdout_file, stderr=subprocess.STDOUT)

print(f"[AG] Blender起動中 (PID={proc.pid})...")

# RPC接続待機
client = RpcClient("127.0.0.1", 8765, timeout=3.0, max_retries=0)
for i in range(60):
    try:
        r = client.ping()
        if r.get("ok"):
            print(f"[AG] RPC接続成功: Blender {r.get('blender', '?')}")
            break
    except Exception:
        time.sleep(0.5)
else:
    print("[AG] RPC接続タイムアウト")
    sys.exit(1)

# 水晶シーンを開く
blend_path = str(Path("ag_runs/crystal_scene.blend").resolve()).replace("\\", "\\\\")
print(f"[AG] シーン読み込み: {blend_path}")
client.exec_python(f'bpy.ops.wm.open_mainfile(filepath=r"{blend_path}")\nresult = "opened"')
print("[AG] 水晶シーンをBlenderで開きました！")
print(f"[AG] Blender PID: {proc.pid} — RPC接続中 (127.0.0.1:8765)")
