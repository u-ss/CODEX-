# /desktop-chatgpt CDPポート分離スクリプト
"""
CDPポートブローカー: 複数エージェントの並列稼働をサポート

方針（2026-02-05 修正）:
- golden_profileを直接使用（コピーしない）= ログイン保持を確実に
- ポート排他制御で同時使用を防止
- TTLで孤児ポートを自動回収
"""

import os
import json
import time
import socket
import shutil
import atexit
from pathlib import Path
from datetime import datetime, timedelta
from filelock import FileLock, Timeout
from dataclasses import dataclass, asdict
from typing import Optional, Tuple
import subprocess

# 設定
PORT_RANGE_START = 9223
PORT_RANGE_END = 9230
LEASE_TTL_SECONDS = 300  # 5分でリース期限切れ
HEARTBEAT_INTERVAL = 60  # 1分ごとにハートビート

# パス設定
CONFIG_DIR = Path(os.environ.get("USERPROFILE", "~")) / ".chatgpt_agent"
REGISTRY_FILE = CONFIG_DIR / "port_registry.json"
REGISTRY_LOCK = CONFIG_DIR / "port_registry.lock"
GOLDEN_PROFILE = CONFIG_DIR / "golden_profile"
TEMP_PROFILES_DIR = CONFIG_DIR / "temp_profiles"


@dataclass
class PortLease:
    """ポートリース情報"""
    port: int
    agent_id: str
    pid: int
    start_ts: float
    last_heartbeat: float
    profile_path: str


def init_config_dir():
    """設定ディレクトリを初期化"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_FILE.exists():
        REGISTRY_FILE.write_text("{}")


def _load_registry() -> dict:
    """レジストリ読み込み"""
    if REGISTRY_FILE.exists():
        try:
            return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}


def _save_registry(registry: dict):
    """レジストリ保存"""
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


def _is_process_alive(pid: int) -> bool:
    """プロセス生存確認"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_port_in_use(port: int) -> bool:
    """ポート使用中確認"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0


def _check_cdp_endpoint(port: int) -> bool:
    """CDPエンドポイント疎通確認"""
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2) as r:
            return r.status == 200
    except:
        return False


def cleanup_stale_leases(registry: dict) -> dict:
    """期限切れリースをクリーンアップ"""
    now = time.time()
    cleaned = {}
    
    for port_str, lease_data in registry.items():
        lease = PortLease(**lease_data)
        
        # TTL期限切れチェック
        if now - lease.last_heartbeat > LEASE_TTL_SECONDS:
            print(f"[PortBroker] TTL期限切れ: port={lease.port}, agent={lease.agent_id}")
            _cleanup_profile(lease.profile_path)
            continue
        
        # プロセス生存確認
        if not _is_process_alive(lease.pid):
            print(f"[PortBroker] 孤児リース回収: port={lease.port}, pid={lease.pid}")
            _cleanup_profile(lease.profile_path)
            continue
        
        cleaned[port_str] = lease_data
    
    return cleaned


def _cleanup_profile(profile_path: str):
    """一時プロファイルをクリーンアップ"""
    try:
        p = Path(profile_path)
        if p.exists() and str(TEMP_PROFILES_DIR) in str(p):
            shutil.rmtree(p, ignore_errors=True)
    except:
        pass


def acquire_port(agent_id: str) -> Tuple[int, str]:
    """
    ポートを取得し、専用プロファイルを準備
    
    Args:
        agent_id: エージェント識別子
    
    Returns:
        (port, profile_path)
    """
    init_config_dir()
    
    lock = FileLock(str(REGISTRY_LOCK), timeout=10)
    
    try:
        with lock:
            registry = _load_registry()
            registry = cleanup_stale_leases(registry)
            
            # 空きポートを探索
            used_ports = {int(k) for k in registry.keys()}
            available_port = None
            
            for port in range(PORT_RANGE_START, PORT_RANGE_END + 1):
                if port not in used_ports and not _is_port_in_use(port):
                    available_port = port
                    break
            
            if available_port is None:
                raise RuntimeError(f"利用可能なポートがありません (範囲: {PORT_RANGE_START}-{PORT_RANGE_END})")
            
            # プロファイルをコピー
            profile_path = _prepare_profile(agent_id, available_port)
            
            # リース登録
            lease = PortLease(
                port=available_port,
                agent_id=agent_id,
                pid=os.getpid(),
                start_ts=time.time(),
                last_heartbeat=time.time(),
                profile_path=profile_path
            )
            registry[str(available_port)] = asdict(lease)
            _save_registry(registry)
            
            print(f"[PortBroker] ポート取得: port={available_port}, agent={agent_id}")
            return available_port, profile_path
            
    except Timeout:
        raise RuntimeError("レジストリロック取得タイムアウト")


def _prepare_profile(agent_id: str, port: int) -> str:
    """Goldenプロファイルを直接使用（コピーしない）"""
    # golden_profileを直接使用してログイン保持を確実にする
    if not GOLDEN_PROFILE.exists():
        # 初回のみ作成
        GOLDEN_PROFILE.mkdir(parents=True, exist_ok=True)
        print(f"[PortBroker] 初回: golden_profileを作成。ブラウザでログインしてください: {GOLDEN_PROFILE}")
    else:
        print(f"[PortBroker] golden_profile使用（ログイン保持）: {GOLDEN_PROFILE}")
    
    return str(GOLDEN_PROFILE)


def update_heartbeat(port: int):
    """ハートビート更新"""
    lock = FileLock(str(REGISTRY_LOCK), timeout=5)
    
    try:
        with lock:
            registry = _load_registry()
            if str(port) in registry:
                registry[str(port)]["last_heartbeat"] = time.time()
                _save_registry(registry)
    except:
        pass


def release_port(port: int):
    """ポート解放"""
    lock = FileLock(str(REGISTRY_LOCK), timeout=5)
    
    try:
        with lock:
            registry = _load_registry()
            if str(port) in registry:
                lease_data = registry.pop(str(port))
                _cleanup_profile(lease_data.get("profile_path", ""))
                _save_registry(registry)
                print(f"[PortBroker] ポート解放: port={port}")
    except:
        pass


def launch_browser(port: int, profile_path: str, url: str = "https://chatgpt.com") -> subprocess.Popen:
    """ブラウザをCDPモードで起動"""
    edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    brave_path = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    
    browser_path = edge_path if Path(edge_path).exists() else brave_path
    
    args = [
        browser_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_path}",
        url
    ]
    
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[PortBroker] ブラウザ起動: port={port}, pid={proc.pid}")
    return proc


def save_to_golden_profile(source_profile: str):
    """現在のプロファイルをGoldenとして保存（ログイン後に実行）"""
    if GOLDEN_PROFILE.exists():
        shutil.rmtree(GOLDEN_PROFILE, ignore_errors=True)
    shutil.copytree(source_profile, GOLDEN_PROFILE, dirs_exist_ok=True)
    print(f"[PortBroker] Goldenプロファイル更新: {GOLDEN_PROFILE}")


# === 便利関数 ===

def start_chatgpt_session(agent_id: str) -> Tuple[int, str, subprocess.Popen]:
    """
    ChatGPTセッションを開始（ポート取得 + ブラウザ起動）
    
    Returns:
        (port, profile_path, browser_process)
    """
    port, profile_path = acquire_port(agent_id)
    
    # 終了時に自動解放
    atexit.register(release_port, port)
    
    proc = launch_browser(port, profile_path)
    time.sleep(3)  # ブラウザ起動待ち
    
    return port, profile_path, proc


def get_active_sessions() -> dict:
    """アクティブセッション一覧を取得"""
    init_config_dir()
    lock = FileLock(str(REGISTRY_LOCK), timeout=5)
    
    with lock:
        registry = _load_registry()
        return cleanup_stale_leases(registry)


# === CLI ===

def _cli_main() -> int:
    import sys

    if len(sys.argv) < 2:
        print("Usage: cdp_port_broker.py <command> [args]")
        print("Commands:")
        print("  acquire <agent_id>  - ポート取得")
        print("  release <port>      - ポート解放")
        print("  list                - アクティブセッション一覧")
        print("  start <agent_id>    - セッション開始（ブラウザ起動）")
        print("  save-golden <path>  - Goldenプロファイル保存")
        return 1

    cmd = sys.argv[1]

    if cmd == "acquire":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else f"agent_{os.getpid()}"
        port, profile = acquire_port(agent_id)
        print(f"PORT={port}")
        print(f"PROFILE={profile}")
        return 0

    if cmd == "release":
        port = int(sys.argv[2])
        release_port(port)
        return 0

    if cmd == "list":
        sessions = get_active_sessions()
        print(json.dumps(sessions, indent=2, ensure_ascii=False))
        return 0

    if cmd == "start":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else f"agent_{os.getpid()}"
        port, profile, proc = start_chatgpt_session(agent_id)
        print(f"PORT={port}")
        print(f"PROFILE={profile}")
        print(f"PID={proc.pid}")
        print("Press Ctrl+C to stop...")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            release_port(port)
        return 0

    if cmd == "save-golden":
        path = sys.argv[2]
        save_to_golden_profile(path)
        return 0

    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    import sys

    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        raise SystemExit(_cli_main())
    raise SystemExit(run_logged_main("desktop", "cdp_port_broker", _cli_main))
