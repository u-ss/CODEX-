"""
Antigravity Bridge - Blender 5.0 Extension

AIエージェントがBlenderをRPC経由で操作するためのブリッジアドオン。
Extensions形式（blender_manifest.toml）で配布。
"""

import os

import bpy
from . import rpc_server


def _read_env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_env_port(name: str, default: int = 8765) -> int:
    raw = os.getenv(name, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"[antigravity_bridge] invalid {name}='{raw}', fallback={default}")
        return default
    if value < 1 or value > 65535:
        print(f"[antigravity_bridge] out-of-range {name}={value}, fallback={default}")
        return default
    return value


class AG_OT_start_rpc(bpy.types.Operator):
    """Antigravity RPCサーバーを開始"""
    bl_idname = "antigravity.start_rpc"
    bl_label = "Start Antigravity RPC"
    bl_description = "AIエージェント用のRPCサーバーを開始します"

    host: bpy.props.StringProperty(default="127.0.0.1")  # type: ignore
    port: bpy.props.IntProperty(default=8765, min=1, max=65535)  # type: ignore

    def execute(self, context):
        rpc_server.start(self.host, self.port)
        self.report({"INFO"}, f"RPC開始: {self.host}:{self.port}")
        return {"FINISHED"}


class AG_OT_stop_rpc(bpy.types.Operator):
    """Antigravity RPCサーバーを停止"""
    bl_idname = "antigravity.stop_rpc"
    bl_label = "Stop Antigravity RPC"
    bl_description = "AIエージェント用のRPCサーバーを停止します"

    def execute(self, context):
        rpc_server.stop()
        self.report({"INFO"}, "RPC停止")
        return {"FINISHED"}


def register():
    bpy.utils.register_class(AG_OT_start_rpc)
    bpy.utils.register_class(AG_OT_stop_rpc)
    # 既定は自動起動ON。不要なら AG_RPC_AUTO_START=0 で無効化できる。
    if _read_env_bool("AG_RPC_AUTO_START", default=True):
        host = (os.getenv("AG_RPC_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
        port = _read_env_port("AG_RPC_PORT", 8765)
        try:
            rpc_server.start(host, port)
        except Exception as exc:
            print(f"[antigravity_bridge] auto-start failed: {exc}")


def unregister():
    try:
        rpc_server.stop()
    except Exception:
        pass
    bpy.utils.unregister_class(AG_OT_stop_rpc)
    bpy.utils.unregister_class(AG_OT_start_rpc)
