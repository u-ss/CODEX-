# cdp_session.py - CDPセッション管理
# 複数エージェントの並列実行のためのポート分離処理

import os
import socket
import subprocess
import json
import time
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict

# セッション情報の保存先
SESSION_DIR = Path(os.environ.get('USERPROFILE', '')) / '.cdp_sessions'


@dataclass
class CDPSession:
    """CDPセッション情報"""
    port: int
    pid: int
    browser: str
    url: str
    created_at: float


def get_free_port(start: int = 9223, end: int = 9230) -> int:
    """空きポートを取得"""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # ポートが使用されていなければ0以外が返る
            if s.connect_ex(('localhost', port)) != 0:
                return port
    raise RuntimeError(f"ポート {start}-{end} は全て使用中です")


def _get_browser_path(browser: str = 'edge') -> str:
    """ブラウザのパスを取得"""
    paths = {
        'edge': r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        'brave': r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
    }
    path = paths.get(browser.lower())
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"ブラウザが見つかりません: {browser}")
    return path


def _load_sessions() -> Dict[int, dict]:
    """保存されたセッション情報を読み込み"""
    SESSION_DIR.mkdir(exist_ok=True)
    sessions = {}
    for f in SESSION_DIR.glob('*.json'):
        try:
            data = json.loads(f.read_text())
            sessions[data['port']] = data
        except:
            pass
    return sessions


def _save_session(session: CDPSession) -> None:
    """セッション情報を保存"""
    SESSION_DIR.mkdir(exist_ok=True)
    path = SESSION_DIR / f'{session.port}.json'
    path.write_text(json.dumps(asdict(session)))


def _remove_session(port: int) -> None:
    """セッション情報を削除"""
    path = SESSION_DIR / f'{port}.json'
    if path.exists():
        path.unlink()


def _is_process_running(pid: int) -> bool:
    """プロセスが実行中か確認"""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
    except:
        pass
    return False


def cleanup_dead_sessions() -> int:
    """終了済みセッションをクリーンアップ"""
    cleaned = 0
    for f in SESSION_DIR.glob('*.json'):
        try:
            data = json.loads(f.read_text())
            if not _is_process_running(data['pid']):
                f.unlink()
                cleaned += 1
        except:
            f.unlink()
            cleaned += 1
    return cleaned


def start_browser(
    url: str = 'https://chatgpt.com',
    browser: str = 'edge',
    profile_dir: Optional[str] = None
) -> CDPSession:
    """
    CDPモードでブラウザを起動
    
    Args:
        url: 開くURL
        browser: 'edge' or 'brave'
        profile_dir: プロファイルディレクトリ（Noneでデフォルト）
    
    Returns:
        CDPSession: セッション情報
    """
    # 終了済みセッションをクリーンアップ
    cleanup_dead_sessions()
    
    # 空きポートを取得
    port = get_free_port()
    
    # ブラウザパス
    browser_path = _get_browser_path(browser)
    
    # プロファイルディレクトリ
    if profile_dir is None:
        profile_dir = os.path.join(os.environ['USERPROFILE'], '.chatgpt_agent_profile')
    
    # ブラウザ起動
    args = [
        browser_path,
        f'--remote-debugging-port={port}',
        f'--user-data-dir={profile_dir}',
        url
    ]
    
    proc = subprocess.Popen(args, shell=False)
    time.sleep(2)  # 起動待ち
    
    # セッション情報を保存
    session = CDPSession(
        port=port,
        pid=proc.pid,
        browser=browser,
        url=url,
        created_at=time.time()
    )
    _save_session(session)
    
    print(f"✅ ブラウザ起動完了")
    print(f"   ポート: {port}")
    print(f"   PID: {proc.pid}")
    print(f"   URL: {url}")
    
    return session


def get_active_sessions() -> List[CDPSession]:
    """アクティブなセッション一覧を取得"""
    cleanup_dead_sessions()
    sessions = []
    for data in _load_sessions().values():
        if _is_process_running(data['pid']):
            sessions.append(CDPSession(**data))
    return sessions


def list_sessions() -> None:
    """セッション一覧を表示"""
    sessions = get_active_sessions()
    if not sessions:
        print("アクティブなセッションはありません")
        return
    
    print(f"アクティブなセッション: {len(sessions)}件")
    print("-" * 50)
    for s in sessions:
        print(f"  ポート: {s.port} | PID: {s.pid} | {s.browser} | {s.url}")


def close_session(port: int) -> bool:
    """指定ポートのセッションを終了"""
    sessions = _load_sessions()
    if port not in sessions:
        print(f"ポート {port} のセッションは見つかりません")
        return False
    
    data = sessions[port]
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(1, False, data['pid'])  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 0)
            kernel32.CloseHandle(handle)
    except:
        pass
    
    _remove_session(port)
    print(f"✅ セッション終了: ポート {port}")
    return True


def _cli_main() -> int:
    import sys

    if len(sys.argv) < 2:
        print("使用方法:")
        print("  --start [url]  : ブラウザを起動")
        print("  --list         : セッション一覧")
        print("  --close [port] : セッション終了")
        return 0

    cmd = sys.argv[1]

    if cmd == '--start':
        url = sys.argv[2] if len(sys.argv) > 2 else 'https://chatgpt.com'
        start_browser(url=url)
        return 0

    if cmd == '--list':
        list_sessions()
        return 0

    if cmd == '--close':
        if len(sys.argv) < 3:
            print("ポート番号を指定してください")
            return 1
        port = int(sys.argv[2])
        close_session(port)
        return 0

    print(f"不明なコマンド: {cmd}")
    return 1


if __name__ == '__main__':
    import sys as _sys
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve()
    for _parent in _here.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in _sys.path:
                _sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("desktop", "cdp_session", _cli_main, phase_name="CDP_SESSION_RUN"))

