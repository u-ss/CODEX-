# -*- coding: utf-8 -*-
"""
Desktop Tools - スクリーンショットモジュール

マルチモニター対応のSS撮影ツール。
"""

from pathlib import Path
from datetime import datetime
from typing import List, Optional

try:
    from ..core.runtime_paths import get_screenshot_dir
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.runtime_paths import get_screenshot_dir

try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False


# デフォルト保存先
DEFAULT_SS_DIR = get_screenshot_dir()


def capture_all_monitors(
    output_dir: Optional[Path] = None,
    prefix: str = "monitor"
) -> List[Path]:
    """
    全モニターのSSを個別に撮影
    
    Args:
        output_dir: 保存先ディレクトリ（Noneでデフォルト）
        prefix: ファイル名プレフィックス
        
    Returns:
        保存されたSSファイルパスのリスト
        
    Raises:
        ImportError: mssがインストールされていない場合
    """
    if not HAS_MSS:
        raise ImportError("mssがインストールされていません: pip install mss")
    
    if output_dir is None:
        output_dir = DEFAULT_SS_DIR
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_paths = []
    
    with mss.mss() as sct:
        # monitors[0]は全モニター結合なのでスキップ
        for i, monitor in enumerate(sct.monitors[1:], 1):
            filename = f"{prefix}_{i}_{timestamp}.png"
            filepath = output_dir / filename
            
            # 撮影
            screenshot = sct.grab(monitor)
            
            # 保存
            mss.tools.to_png(screenshot.rgb, screenshot.size, output=str(filepath))
            saved_paths.append(filepath)
    
    return saved_paths


def capture_monitor(
    monitor_index: int,
    output_path: Optional[Path] = None
) -> Path:
    """
    特定モニターのみ撮影
    
    Args:
        monitor_index: モニター番号（1から開始）
        output_path: 保存先パス（Noneで自動生成）
        
    Returns:
        保存されたSSファイルパス
        
    Raises:
        ImportError: mssがインストールされていない場合
        IndexError: モニター番号が範囲外
    """
    if not HAS_MSS:
        raise ImportError("mssがインストールされていません: pip install mss")
    
    with mss.mss() as sct:
        if monitor_index < 1 or monitor_index >= len(sct.monitors):
            raise IndexError(f"モニター番号{monitor_index}は範囲外です（1〜{len(sct.monitors)-1}）")
        
        monitor = sct.monitors[monitor_index]
        
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = DEFAULT_SS_DIR / f"monitor_{monitor_index}_{timestamp}.png"
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        screenshot = sct.grab(monitor)
        mss.tools.to_png(screenshot.rgb, screenshot.size, output=str(output_path))
        
        return output_path


def get_monitor_count() -> int:
    """
    接続されているモニター数を取得
    
    Returns:
        モニター数
    """
    if not HAS_MSS:
        raise ImportError("mssがインストールされていません: pip install mss")
    
    with mss.mss() as sct:
        # monitors[0]は全モニター結合なので-1
        return len(sct.monitors) - 1


# === CLI ===

def _cli_main() -> int:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "count":
        print(f"Monitor count: {get_monitor_count()}")
        return 0

    print("Capturing all monitors...")
    paths = capture_all_monitors()
    print(f"Saved {len(paths)} screenshots:")
    for p in paths:
        print(f"  - {p}")
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path

    _repo_root = _Path(__file__).resolve()
    for _parent in _repo_root.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in sys.path:
                sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("desktop", "screenshot_tool", _cli_main, phase_name="SCREENSHOT_TOOL_CLI"))

