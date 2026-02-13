"""
RPCクライアント - Antigravity Bridge用

BlenderのRPCサーバーに接続し、JSON-RPC over TCPで操作を送信する。

使用例:
    from ag_rpc_client import RpcClient
    client = RpcClient()
    client.ping()
    client.add_cube(size=2.0)
    client.save_as("scene.blend")
"""

import json
import os
import socket
import time
from typing import Any, Dict, Optional


class RpcError(RuntimeError):
    """RPCエラー"""
    def __init__(self, error_data: dict):
        self.code = error_data.get("code", -1)
        self.rpc_message = error_data.get("message", "Unknown error")
        super().__init__(f"RPC Error [{self.code}]: {self.rpc_message}")


class _TransientReceiveError(RuntimeError):
    """一時的な受信不整合を表す内部例外"""


class RpcClient:
    """Antigravity Bridge RPCクライアント"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, timeout: float = 30.0,
                 token: Optional[str] = None, max_retries: int = 3, retry_backoff: float = 0.25,
                 busy_retries: int = 8, busy_backoff: float = 0.12, reuse_connection: bool = False):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.token = os.getenv("AG_RPC_TOKEN", "") if token is None else token
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.busy_retries = max(0, int(busy_retries))
        self.busy_backoff = max(0.01, float(busy_backoff))
        self.reuse_connection = bool(reuse_connection)
        self._req_id = 0
        self._sock: Optional[socket.socket] = None

    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        return sock

    def _disconnect(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close()
        except Exception:
            pass
        self._sock = None

    def close(self) -> None:
        self._disconnect()

    def __enter__(self) -> "RpcClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _send_and_recv(self, msg: bytes) -> Dict[str, Any]:
        if self.reuse_connection:
            if self._sock is None:
                self._sock = self._connect()
            sock = self._sock
            sock.sendall(msg)
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    if not data:
                        raise _TransientReceiveError("empty response")
                    break
                data += chunk
        else:
            with self._connect() as sock:
                sock.sendall(msg)
                data = b""
                while not data.endswith(b"\n"):
                    chunk = sock.recv(65536)
                    if not chunk:
                        if not data:
                            raise _TransientReceiveError("empty response")
                        break
                    data += chunk

        try:
            return json.loads(data.decode("utf-8", "replace"))
        except json.JSONDecodeError as e:
            raise _TransientReceiveError(f"invalid JSON response: {e}") from e

    def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """RPCメソッドを呼び出す"""
        self._req_id += 1
        payload = {
            "id": self._req_id,
            "method": method,
            "params": params or {}
        }
        if self.token:
            payload["token"] = self.token

        msg = (json.dumps(payload) + "\n").encode("utf-8")

        last_exc: Optional[Exception] = None
        attempts = self.max_retries + 1
        transport_attempt = 0
        busy_attempt = 0

        while True:
            try:
                resp = self._send_and_recv(msg)

                if "error" in resp:
                    rpc_error = RpcError(resp["error"])
                    if rpc_error.code in (-32041, -32002) and busy_attempt < self.busy_retries:
                        last_exc = rpc_error
                        delay = min(2.0, self.busy_backoff * (2 ** busy_attempt))
                        time.sleep(delay)
                        busy_attempt += 1
                        continue
                    raise rpc_error
                return resp.get("result")

            except RpcError:
                raise
            except (socket.timeout, ConnectionRefusedError, TimeoutError, OSError, _TransientReceiveError) as e:
                last_exc = e
                if self.reuse_connection:
                    self._disconnect()
                if transport_attempt >= self.max_retries:
                    break
                delay = self.retry_backoff * (2 ** transport_attempt)
                if delay > 0:
                    time.sleep(delay)
                transport_attempt += 1

        raise RuntimeError(f"RPC call failed after {attempts} attempts: {last_exc}") from last_exc

    # ---- 便利メソッド ----

    def ping(self) -> dict:
        """疎通確認"""
        return self._call("ping")

    def reset_scene(self) -> dict:
        """シーン初期化"""
        return self._call("reset_scene")

    def add_cube(self, size: float = 2.0, location: tuple = (0, 0, 0)) -> dict:
        """キューブ追加"""
        return self._call("add_cube", {"size": size, "location": list(location)})

    def add_sphere(self, radius: float = 1.0, segments: int = 32, location: tuple = (0, 0, 0)) -> dict:
        """球追加"""
        return self._call("add_sphere", {"radius": radius, "segments": segments, "location": list(location)})

    def add_cylinder(self, radius: float = 1.0, depth: float = 2.0, location: tuple = (0, 0, 0)) -> dict:
        """シリンダー追加"""
        return self._call("add_cylinder", {"radius": radius, "depth": depth, "location": list(location)})

    def add_plane(self, size: float = 2.0, location: tuple = (0, 0, 0)) -> dict:
        """平面追加"""
        return self._call("add_plane", {"size": size, "location": list(location)})

    def delete_object(self, name: str) -> dict:
        """オブジェクト削除"""
        return self._call("delete_object", {"name": name})

    def transform(self, name: str, location: Optional[tuple] = None,
                  rotation: Optional[tuple] = None, scale: Optional[tuple] = None) -> dict:
        """トランスフォーム設定"""
        params = {"name": name}
        if location is not None:
            params["location"] = list(location)
        if rotation is not None:
            params["rotation"] = list(rotation)
        if scale is not None:
            params["scale"] = list(scale)
        return self._call("transform", params)

    def make_material(self, name: str = "AG_Mat", base_color: tuple = (0.8, 0.2, 0.2, 1.0)) -> dict:
        """マテリアル作成"""
        return self._call("make_material", {"name": name, "base_color": list(base_color)})

    def assign_material(self, obj_name: str, material_name: str) -> dict:
        """マテリアル割り当て"""
        return self._call("assign_material", {"obj_name": obj_name, "material_name": material_name})

    def add_camera(self, location: tuple = (7.36, -6.93, 4.96), rotation: tuple = (1.1, 0, 0.81)) -> dict:
        """カメラ追加"""
        return self._call("add_camera", {"location": list(location), "rotation": list(rotation)})

    def add_light(self, type: str = "SUN", energy: float = 5.0, location: tuple = (4.07, 1.0, 5.9)) -> dict:
        """ライト追加"""
        return self._call("add_light", {"type": type, "energy": energy, "location": list(location)})

    def set_render_settings(self, engine: str = "CYCLES", device: str = "GPU",
                            resolution_x: int = 1920, resolution_y: int = 1080,
                            samples: int = 128) -> dict:
        """レンダ設定"""
        return self._call("set_render_settings", {
            "engine": engine, "device": device,
            "resolution_x": resolution_x, "resolution_y": resolution_y,
            "samples": samples
        })

    def list_objects(self) -> dict:
        """オブジェクト一覧"""
        return self._call("list_objects")

    def get_object_info(self, name: str) -> dict:
        """オブジェクト情報取得"""
        return self._call("get_object_info", {"name": name})

    def duplicate_object(self, name: str, new_name: Optional[str] = None,
                         offset: tuple = (0.5, 0.0, 0.0)) -> dict:
        """オブジェクト複製"""
        params = {"name": name, "offset": list(offset)}
        if new_name:
            params["new_name"] = new_name
        return self._call("duplicate_object", params)

    def set_material_color(self, material_name: str, rgba: tuple) -> dict:
        """マテリアル色変更"""
        return self._call("set_material_color", {"material_name": material_name, "rgba": list(rgba)})

    def undo_step(self) -> dict:
        """1手取り消し"""
        return self._call("undo_step")

    def redo_step(self) -> dict:
        """1手やり直し"""
        return self._call("redo_step")

    def save_as(self, path: str) -> dict:
        """blendファイル保存"""
        return self._call("save_as", {"path": path})

    def open(self, path: str) -> dict:
        """blendファイルを開く"""
        return self._call("open", {"path": path})

    def exec_python(self, code: str) -> dict:
        """任意のPythonコードを実行"""
        return self._call("exec_python", {"code": code})

    def shutdown(self) -> dict:
        """Blenderを終了"""
        return self._call("shutdown")
