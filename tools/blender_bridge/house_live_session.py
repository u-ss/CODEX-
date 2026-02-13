"""
house_live_session.py - Blender GUIライブ編集セッション管理
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ag_rpc_client import RpcClient
from blender_cli import DEFAULT_BLENDER_EXE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_open_port(host: str, preferred: int, retries: int = 30) -> int:
    for i in range(retries):
        port = preferred + i
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"利用可能なポートが見つかりません: {host}:{preferred}+{retries}")


def _build_expr(host: str, port: int, bridge_dir: Path) -> str:
    return f"""
import sys
sys.path.insert(0, r"{bridge_dir}")
from antigravity_bridge import rpc_server
rpc_server.start("{host}", {port})
print("[AG] LIVE RPC READY")
"""


def _wait_ready(host: str, port: int, token: str, timeout_s: float) -> None:
    client = RpcClient(host=host, port=port, timeout=2.0, token=token, max_retries=0)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            result = client.ping()
            if isinstance(result, dict) and result.get("ok"):
                return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"ライブRPCが {timeout_s:.1f}s 以内に応答しません")


def load_session(session_path: Path) -> Dict[str, Any]:
    return _read_json(session_path)


def save_session(session_path: Path, session: Dict[str, Any]) -> None:
    session["updated_at"] = _now_iso()
    _write_json(session_path, session)


def create_checkpoint(client: RpcClient, session_path: Path, label: str = "edit") -> Path:
    session = load_session(session_path)
    idx = int(session.get("next_checkpoint_index", 1))
    ckpt_dir = Path(session["checkpoint_dir"])
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = ckpt_dir / f"{label}_{idx:04d}.blend"
    client.save_as(str(ckpt_path))
    session["next_checkpoint_index"] = idx + 1
    session["latest_checkpoint"] = str(ckpt_path)
    save_session(session_path, session)
    return ckpt_path


def finalize_session(client: RpcClient, session_path: Path, final_name: str = "final_live.blend") -> Path:
    session = load_session(session_path)
    run_dir = Path(session["run_dir"])
    final_path = run_dir / final_name
    client.save_as(str(final_path))
    session["latest_checkpoint"] = str(final_path)
    session["status"] = "FINALIZED"
    save_session(session_path, session)
    return final_path


def create_live_session(
    blender_exe: str,
    blend_file: Path,
    run_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: Optional[str] = None,
    timeout_s: float = 45.0,
) -> Path:
    blender_exe = str(Path(blender_exe).resolve())
    blend_file = Path(blend_file).resolve()
    run_dir = Path(run_dir).resolve()
    if not Path(blender_exe).exists():
        raise FileNotFoundError(f"Blenderが見つかりません: {blender_exe}")
    if not blend_file.exists():
        raise FileNotFoundError(f"対象blendが見つかりません: {blend_file}")

    live_dir = run_dir / "live_session"
    live_dir.mkdir(parents=True, exist_ok=True)

    chosen_port = _find_open_port(host, port)
    resolved_token = token if token is not None else (os.getenv("AG_RPC_TOKEN", "") or secrets.token_hex(16))
    log_path = live_dir / "live_session.log"
    session_path = live_dir / "session.json"
    ckpt_dir = live_dir / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["AG_RPC_TOKEN"] = resolved_token
    env["AG_RPC_ENABLE_EXEC_PYTHON"] = "0"

    bridge_dir = Path(__file__).resolve().parent / "antigravity_bridge"
    expr = _build_expr(host=host, port=chosen_port, bridge_dir=bridge_dir)

    cmd = [blender_exe, str(blend_file), "--python-expr", expr]
    log_fh = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, env=env, stdout=log_fh, stderr=subprocess.STDOUT)
    log_fh.close()

    try:
        _wait_ready(host=host, port=chosen_port, token=resolved_token, timeout_s=timeout_s)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise

    session = {
        "session_id": f"live_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": "ACTIVE",
        "run_dir": str(run_dir),
        "blend_path": str(blend_file),
        "host": host,
        "port": chosen_port,
        "token": resolved_token,
        "pid": proc.pid,
        "log_path": str(log_path),
        "checkpoint_dir": str(ckpt_dir),
        "next_checkpoint_index": 1,
        "latest_checkpoint": str(blend_file),
        "live_edit_log": str(live_dir / "live_edit_log.jsonl"),
    }
    _write_json(session_path, session)
    return session_path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--work-dir", required=True)
    p.add_argument("--blender-exe", default=DEFAULT_BLENDER_EXE)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--token", default="")
    p.add_argument("--timeout", type=float, default=45.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        session_path = create_live_session(
            blender_exe=args.blender_exe,
            blend_file=Path(args.blend),
            run_dir=Path(args.work_dir),
            host=args.host,
            port=args.port,
            token=args.token or None,
            timeout_s=args.timeout,
        )
        print(session_path)
        return 0
    except Exception as e:
        print(f"[AG] live session start failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

