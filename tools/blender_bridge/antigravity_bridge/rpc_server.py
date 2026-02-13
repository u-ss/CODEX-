"""
Antigravity Bridge - JSON-RPC over TCP サーバー

Blender 5.0 Extensions形式のアドオン。
AIエージェントからBlenderを操作するためのRPCサーバーを提供。

安全設計:
- ネットワーク受信は別スレッド
- bpy操作は必ずメインスレッドで実行（キュー＋bpy.app.timers）
- 許可されたオペレーションのみ実行（ホワイトリスト方式）
"""

import json
import os
import queue
import socket
import socketserver
import threading
from concurrent.futures import Future
from typing import Any, Callable, Dict, Optional

import bpy


def _read_env_int(name: str, default: int, min_value: int = 1) -> int:
    """整数環境変数を安全に読む"""
    raw = os.getenv(name, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"[antigravity_bridge] 無効な環境変数 {name}='{raw}'。既定値 {default} を使用します。")
        return default
    if value < min_value:
        print(f"[antigravity_bridge] 環境変数 {name} は {min_value} 以上が必要です。既定値 {default} を使用します。")
        return default
    return value


def _read_env_bool(name: str, default: bool = False) -> bool:
    """真偽値環境変数を読む（1/true/yes/on を True と解釈）"""
    raw = os.getenv(name, "")
    if raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_env_float(name: str, default: float, min_value: float = 0.0) -> float:
    """浮動小数環境変数を安全に読む"""
    raw = os.getenv(name, "")
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        print(f"[antigravity_bridge] 無効な環境変数 {name}='{raw}'。既定値 {default} を使用します。")
        return default
    if value < min_value:
        print(f"[antigravity_bridge] 環境変数 {name} は {min_value} 以上が必要です。既定値 {default} を使用します。")
        return default
    return value


_AG_RPC_TOKEN = os.getenv("AG_RPC_TOKEN", "")
_AG_RPC_MAX_QUEUE = _read_env_int("AG_RPC_MAX_QUEUE", 50, min_value=1)
_AG_RPC_MAX_CONNECTIONS = _read_env_int("AG_RPC_MAX_CONNECTIONS", 1, min_value=1)
_AG_RPC_ENABLE_EXEC_PYTHON = _read_env_bool("AG_RPC_ENABLE_EXEC_PYTHON", default=False)
_AG_RPC_CONNECTION_WAIT_MS = _read_env_float("AG_RPC_CONNECTION_WAIT_MS", 150.0, min_value=0.0)
_AG_RPC_READ_TIMEOUT_S = _read_env_float("AG_RPC_READ_TIMEOUT_S", 30.0, min_value=0.1)
_AG_RPC_REQUEST_BACKLOG = _read_env_int("AG_RPC_REQUEST_BACKLOG", 16, min_value=1)

# ---- メインスレッド実行キュー ----
_main_thread_q: "queue.Queue[tuple[Callable[..., Any], dict, Future]]" = queue.Queue(maxsize=_AG_RPC_MAX_QUEUE)
_connection_semaphore = threading.BoundedSemaphore(_AG_RPC_MAX_CONNECTIONS)
_server = None
_server_thread = None


def _pump_main_thread() -> float:
    """Blenderのメインスレッドでキューを処理する（タイマーコールバック）"""
    processed = 0
    while processed < 50:  # 1tickで処理しすぎない
        try:
            fn, params, fut = _main_thread_q.get_nowait()
        except queue.Empty:
            break
        try:
            result = fn(**params)
            fut.set_result(result)
        except Exception as e:
            fut.set_exception(e)
        processed += 1
    # 0.01秒ごとに再実行（継続）
    return 0.01


# ---- 許可されたオペレーション（ホワイトリスト） ----

def _op_ping() -> dict:
    """疎通確認"""
    return {"ok": True, "blender": bpy.app.version_string}


def _op_reset_scene() -> dict:
    """シーンを初期化（空シーン）"""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return {"ok": True}


def _op_add_cube(size: float = 2.0, location: tuple = (0, 0, 0)) -> dict:
    """キューブを追加"""
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    return {"ok": True, "name": obj.name}


def _op_add_sphere(radius: float = 1.0, segments: int = 32, location: tuple = (0, 0, 0)) -> dict:
    """UV球を追加"""
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=segments, location=location)
    obj = bpy.context.active_object
    return {"ok": True, "name": obj.name}


def _op_add_cylinder(radius: float = 1.0, depth: float = 2.0, location: tuple = (0, 0, 0)) -> dict:
    """シリンダーを追加"""
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=location)
    obj = bpy.context.active_object
    return {"ok": True, "name": obj.name}


def _op_add_plane(size: float = 2.0, location: tuple = (0, 0, 0)) -> dict:
    """平面を追加"""
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)
    obj = bpy.context.active_object
    return {"ok": True, "name": obj.name}


def _op_delete_object(name: str) -> dict:
    """オブジェクトを削除"""
    obj = bpy.data.objects.get(name)
    if not obj:
        return {"ok": False, "error": f"オブジェクト '{name}' が見つかりません"}
    bpy.data.objects.remove(obj, do_unlink=True)
    return {"ok": True}


def _op_transform(name: str, location: Optional[tuple] = None,
                  rotation: Optional[tuple] = None, scale: Optional[tuple] = None) -> dict:
    """オブジェクトのトランスフォームを設定"""
    obj = bpy.data.objects.get(name)
    if not obj:
        return {"ok": False, "error": f"オブジェクト '{name}' が見つかりません"}
    if location is not None:
        obj.location = location
    if rotation is not None:
        obj.rotation_euler = rotation
    if scale is not None:
        obj.scale = scale
    return {"ok": True, "name": obj.name}


def _op_get_object_info(name: str) -> dict:
    """オブジェクト情報を取得"""
    obj = bpy.data.objects.get(name)
    if not obj:
        return {"ok": False, "error": f"オブジェクト '{name}' が見つかりません"}
    materials = []
    data = getattr(obj, "data", None)
    if data is not None and hasattr(data, "materials"):
        for mat in data.materials:
            if mat:
                materials.append(mat.name)
    return {
        "ok": True,
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "dimensions": list(obj.dimensions),
        "materials": materials,
    }


def _op_duplicate_object(name: str, new_name: Optional[str] = None, offset: tuple = (0.5, 0.0, 0.0)) -> dict:
    """オブジェクトを複製"""
    obj = bpy.data.objects.get(name)
    if not obj:
        return {"ok": False, "error": f"オブジェクト '{name}' が見つかりません"}

    dup = obj.copy()
    if getattr(obj, "data", None) is not None:
        dup.data = obj.data.copy()
    dup.name = new_name or f"{obj.name}_copy"
    try:
        dx, dy, dz = float(offset[0]), float(offset[1]), float(offset[2])
    except Exception:
        dx, dy, dz = 0.5, 0.0, 0.0
    dup.location = (obj.location.x + dx, obj.location.y + dy, obj.location.z + dz)
    bpy.context.collection.objects.link(dup)
    return {"ok": True, "name": dup.name}


def _op_set_material_color(material_name: str, rgba: tuple = (0.8, 0.2, 0.2, 1.0)) -> dict:
    """マテリアル色を変更"""
    mat = bpy.data.materials.get(material_name)
    if not mat:
        return {"ok": False, "error": f"マテリアル '{material_name}' が見つかりません"}

    try:
        r, g, b, a = float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3])
    except Exception:
        return {"ok": False, "error": f"rgba '{rgba}' が不正です"}

    if hasattr(mat, "diffuse_color"):
        mat.diffuse_color = (r, g, b, a)

    if hasattr(mat, "node_tree") and mat.node_tree:
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (r, g, b, a)

    return {"ok": True, "material": mat.name}


def _op_undo_step() -> dict:
    """1手取り消し"""
    bpy.ops.ed.undo()
    return {"ok": True}


def _op_redo_step() -> dict:
    """1手やり直し"""
    bpy.ops.ed.redo()
    return {"ok": True}


def _op_make_material(name: str = "AG_Mat", base_color: tuple = (0.8, 0.2, 0.2, 1.0)) -> dict:
    """マテリアルを作成"""
    mat = bpy.data.materials.new(name=name)
    # Blender 5.0以降はuse_nodesは常にTrue（6.0で削除予定）
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = base_color
    return {"ok": True, "material": mat.name}


def _op_assign_material(obj_name: str, material_name: str) -> dict:
    """オブジェクトにマテリアルを割り当て"""
    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return {"ok": False, "error": f"オブジェクト '{obj_name}' が見つかりません"}
    mat = bpy.data.materials.get(material_name)
    if not mat:
        return {"ok": False, "error": f"マテリアル '{material_name}' が見つかりません"}
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return {"ok": True}


def _op_add_camera(location: tuple = (7.36, -6.93, 4.96),
                   rotation: tuple = (1.1, 0, 0.81)) -> dict:
    """カメラを追加"""
    bpy.ops.object.camera_add(location=location, rotation=rotation)
    cam = bpy.context.active_object
    bpy.context.scene.camera = cam
    return {"ok": True, "name": cam.name}


def _op_add_light(type: str = "SUN", energy: float = 5.0,
                  location: tuple = (4.07, 1.0, 5.9)) -> dict:
    """ライトを追加"""
    bpy.ops.object.light_add(type=type, location=location)
    light = bpy.context.active_object
    light.data.energy = energy
    return {"ok": True, "name": light.name}


def _op_set_render_settings(engine: str = "CYCLES", device: str = "GPU",
                             resolution_x: int = 1920, resolution_y: int = 1080,
                             samples: int = 128) -> dict:
    """レンダリング設定を変更"""
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.render.resolution_x = resolution_x
    scene.render.resolution_y = resolution_y

    if engine == "CYCLES":
        scene.cycles.device = device
        scene.cycles.samples = samples
        # OptiX設定（RTX 5080向け）
        prefs = bpy.context.preferences.addons.get("cycles")
        if prefs:
            prefs.preferences.compute_device_type = "OPTIX"
            prefs.preferences.get_devices()
            for d in prefs.preferences.devices:
                d.use = True

    return {"ok": True}


def _op_list_objects() -> dict:
    """シーン内のオブジェクト一覧を取得"""
    objects = []
    for obj in bpy.data.objects:
        objects.append({
            "name": obj.name,
            "type": obj.type,
            "location": list(obj.location),
        })
    return {"ok": True, "objects": objects}


def _op_save_as(path: str) -> dict:
    """指定パスに.blendファイルを保存"""
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return {"ok": True, "path": path}


def _op_open(path: str) -> dict:
    """指定パスの.blendファイルを開く"""
    bpy.ops.wm.open_mainfile(filepath=path)
    return {"ok": True, "path": path}


def _op_exec_python(code: str) -> dict:
    """任意のPythonコードを実行（上級者向け）"""
    # globalsとlocalsに同じ辞書を使用する
    # 別辞書だとトップレベルのimportがlocalsに入り、
    # exec内で定義された関数からはglobalsを参照するため見えなくなる
    ns: Dict[str, Any] = {"bpy": bpy, "__builtins__": __builtins__}
    exec(code, ns)
    # 結果を返せるようにresult変数があれば返す
    result = ns.get("result", None)
    return {"ok": True, "result": str(result) if result is not None else None}


def _op_shutdown() -> dict:
    """Blenderを終了"""
    bpy.ops.wm.quit_blender()
    return {"ok": True}


# ---- メソッド登録テーブル ----
_METHODS = {
    "ping": _op_ping,
    "reset_scene": _op_reset_scene,
    "add_cube": _op_add_cube,
    "add_sphere": _op_add_sphere,
    "add_cylinder": _op_add_cylinder,
    "add_plane": _op_add_plane,
    "delete_object": _op_delete_object,
    "transform": _op_transform,
    "get_object_info": _op_get_object_info,
    "duplicate_object": _op_duplicate_object,
    "set_material_color": _op_set_material_color,
    "undo_step": _op_undo_step,
    "redo_step": _op_redo_step,
    "make_material": _op_make_material,
    "assign_material": _op_assign_material,
    "add_camera": _op_add_camera,
    "add_light": _op_add_light,
    "set_render_settings": _op_set_render_settings,
    "list_objects": _op_list_objects,
    "save_as": _op_save_as,
    "open": _op_open,
    "exec_python": _op_exec_python,
    "shutdown": _op_shutdown,
}


class _JsonLineHandler(socketserver.StreamRequestHandler):
    """1行JSON = 1リクエストのハンドラ"""

    def _send_json(self, payload: Dict[str, Any]) -> None:
        """改行終端JSONを送信してflushする"""
        try:
            self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            # クライアント切断時の送信失敗は無視
            pass

    def _handle_request_json(self, req: Dict[str, Any]) -> Dict[str, Any]:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        token = req.get("token")
        resp: Dict[str, Any] = {"id": req_id}

        if _AG_RPC_TOKEN and token != _AG_RPC_TOKEN:
            resp["error"] = {"code": -32040, "message": "UNAUTHORIZED"}
            return resp

        if method not in _METHODS:
            resp["error"] = {"code": -32601, "message": f"未知のメソッド: {method}"}
            return resp

        if method == "exec_python" and not _AG_RPC_ENABLE_EXEC_PYTHON:
            resp["error"] = {"code": -32020, "message": "DEBUG_DISABLED"}
            return resp

        if _main_thread_q.qsize() >= _AG_RPC_MAX_QUEUE:
            resp["error"] = {"code": -32002, "message": "BUSY_QUEUE_FULL"}
            return resp

        fut: Future = Future()
        try:
            _main_thread_q.put_nowait((_METHODS[method], params, fut))
        except queue.Full:
            resp["error"] = {"code": -32002, "message": "BUSY_QUEUE_FULL"}
            return resp

        try:
            result = fut.result(timeout=60)  # タイムアウトはメソッドに応じて調整可能
            resp["result"] = result
        except Exception as e:
            resp["error"] = {"code": -32000, "message": str(e)}
        return resp

    def handle(self):
        wait_s = max(0.0, float(_AG_RPC_CONNECTION_WAIT_MS) / 1000.0)
        acquired = _connection_semaphore.acquire(timeout=wait_s) if wait_s > 0 else _connection_semaphore.acquire(blocking=False)
        if not acquired:
            resp = {"id": None, "error": {"code": -32041, "message": "TOO_MANY_CONNECTIONS"}}
            self._send_json(resp)
            return

        try:
            self.connection.settimeout(float(_AG_RPC_READ_TIMEOUT_S))
            # Keep the same connection open to process multiple JSON-RPC lines.
            while True:
                raw_line = self.rfile.readline()
                if not raw_line:
                    break
                raw = raw_line.decode("utf-8", "replace").strip()
                if not raw:
                    continue

                try:
                    req = json.loads(raw)
                except json.JSONDecodeError as e:
                    self._send_json({"id": None, "error": {"code": -32700, "message": f"JSONパースエラー: {e}"}})
                    continue

                resp = self._handle_request_json(req)
                self._send_json(resp)
        except (TimeoutError, socket.timeout):
            resp = {"id": None, "error": {"code": -32042, "message": "CONNECTION_TIMEOUT"}}
            self._send_json(resp)
        except OSError as e:
            resp = {"id": None, "error": {"code": -32043, "message": f"SOCKET_ERROR: {e}"}}
            self._send_json(resp)
        finally:
            try:
                _connection_semaphore.release()
            except ValueError:
                pass


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass):
        self.request_queue_size = int(_AG_RPC_REQUEST_BACKLOG)
        super().__init__(server_address, RequestHandlerClass)


def start(host: str = "127.0.0.1", port: int = 8765):
    """RPCサーバーを開始"""
    global _server, _server_thread

    if _server is not None:
        print(f"[antigravity_bridge] サーバーは既に起動中 ({host}:{port})")
        return

    # メインスレッドポンプ開始
    bpy.app.timers.register(_pump_main_thread, persistent=True)

    _server = _ThreadingTCPServer((host, port), _JsonLineHandler)

    def _run():
        _server.serve_forever(poll_interval=0.2)

    _server_thread = threading.Thread(target=_run, name="antigravity_rpc", daemon=True)
    _server_thread.start()
    print(f"[antigravity_bridge] RPCサーバー開始: {host}:{port}")
    print(f"[AG] RPC READY {host}:{port}")


def stop():
    """RPCサーバーを停止"""
    global _server, _server_thread
    if _server is None:
        return
    _server.shutdown()
    _server.server_close()
    _server = None
    _server_thread = None
    print("[antigravity_bridge] RPCサーバー停止")
