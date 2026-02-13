"""
SS Cleanup - 古いスクリーンショットを自動削除

使用方法:
    python ss_cleanup.py [--max-age-minutes 30] [--max-count 20]

デフォルト:
    - 30分以上前のSSを削除
    - 最大20枚を保持（古い順に削除）
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import argparse


def cleanup_screenshots(
    ss_dir: Path,
    max_age_minutes: int = 30,
    max_count: int = 20
) -> dict:
    """古いスクリーンショットを削除"""
    
    if not ss_dir.exists():
        return {"total": 0, "deleted": 0, "remaining": 0}
    
    files = sorted(ss_dir.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
    total = len(files)
    deleted = 0
    now = datetime.now()
    
    for i, f in enumerate(files):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        age = now - mtime
        
        # 条件：max_age_minutes以上前 OR max_count超過
        if age > timedelta(minutes=max_age_minutes) or i >= max_count:
            try:
                f.unlink()
                deleted += 1
            except Exception as e:
                print(f"Error deleting {f}: {e}", file=sys.stderr)
    
    remaining = total - deleted
    return {"total": total, "deleted": deleted, "remaining": remaining}


def main():
    parser = argparse.ArgumentParser(description="SS Cleanup")
    parser.add_argument("--max-age-minutes", type=int, default=30)
    parser.add_argument("--max-count", type=int, default=20)
    parser.add_argument("--dir", type=str, default="_screenshots")
    args = parser.parse_args()
    
    ss_dir = Path(args.dir)
    result = cleanup_screenshots(ss_dir, args.max_age_minutes, args.max_count)
    print(f"Total: {result['total']}, Deleted: {result['deleted']}, Remaining: {result['remaining']}")


if __name__ == "__main__":
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
    raise SystemExit(_run_logged_main("desktop", "ss_cleanup", main, phase_name="SS_CLEANUP_RUN"))

