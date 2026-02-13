"""
タスク連動スクリーンショットバッファ
- タスク実行中のみSSを撮影
- with文で自動開始/停止
- エラー時はSS保持、成功時は削除
"""

import mss
import time
import threading
from pathlib import Path
from PIL import Image
from datetime import datetime
from collections import deque
from typing import Optional, Callable
import ctypes


# DPI Aware設定
def set_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except:
            ctypes.windll.user32.SetProcessDPIAware()


class TaskScreenshotBuffer:
    """
    タスク実行中のみSSを撮影するバッファ
    
    使い方:
        with TaskScreenshotBuffer(task_name="browse_chatgpt") as buffer:
            # この間だけSSを撮る
            do_automation()
        # 成功時は自動削除、エラー時は保持
    """
    
    def __init__(
        self,
        task_name: str = "task",
        output_dir: str = "_screenshots/tasks",
        max_frames: int = 120,  # 最大120枚（2分分）
        interval_sec: float = 1.0,
        monitor_index: int = 0,
        resize_ratio: float = 0.5,
        jpeg_quality: int = 50,
        keep_on_success: bool = False,  # 成功時もSS保持
        keep_on_error: bool = True,  # エラー時はSS保持
    ):
        self.task_name = task_name
        self.output_dir = Path(output_dir) / f"{task_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.max_frames = max_frames
        self.interval_sec = interval_sec
        self.monitor_index = monitor_index
        self.resize_ratio = resize_ratio
        self.jpeg_quality = jpeg_quality
        self.keep_on_success = keep_on_success
        self.keep_on_error = keep_on_error
        
        self.frames: deque = deque(maxlen=max_frames)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._error_occurred = False
        
        set_dpi_aware()
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        
        if exc_type is not None:
            # エラー発生
            self._error_occurred = True
            if self.keep_on_error:
                print(f"❌ Task failed. Screenshots kept at: {self.output_dir}")
            else:
                self.clear()
        else:
            # 成功
            if self.keep_on_success:
                print(f"✅ Task succeeded. Screenshots at: {self.output_dir}")
            else:
                self.clear()
                print(f"✅ Task succeeded. Screenshots cleared.")
        
        return False  # 例外は再送出
    
    def start(self):
        """キャプチャ開始"""
        if self._running:
            return
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print(f"📸 Screenshot capture started: {self.task_name}")
    
    def stop(self):
        """キャプチャ停止"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        print(f"⏹️ Screenshot capture stopped. {len(self.frames)} frames captured.")
    
    def _capture_loop(self):
        with mss.mss() as sct:
            frame_count = 0
            while self._running:
                try:
                    # キャプチャ
                    raw = sct.grab(sct.monitors[self.monitor_index])
                    img = Image.frombytes("RGB", raw.size, raw.rgb)
                    
                    # リサイズ
                    if self.resize_ratio < 1.0:
                        new_size = (
                            int(raw.width * self.resize_ratio),
                            int(raw.height * self.resize_ratio)
                        )
                        img = img.resize(new_size, Image.Resampling.LANCZOS)
                    
                    # ファイル保存
                    ts = datetime.now().strftime("%H%M%S_%f")[:-3]
                    filename = f"frame_{frame_count:04d}_{ts}.jpg"
                    filepath = self.output_dir / filename
                    img.save(filepath, "JPEG", quality=self.jpeg_quality)
                    
                    # 古いフレーム削除
                    if len(self.frames) >= self.max_frames:
                        old_file = self.frames[0]
                        if old_file.exists():
                            old_file.unlink()
                    
                    self.frames.append(filepath)
                    frame_count += 1
                    
                except Exception as e:
                    print(f"Capture error: {e}")
                
                time.sleep(self.interval_sec)
    
    def get_latest(self, n: int = 1) -> list[Path]:
        """最新N枚を取得"""
        return list(self.frames)[-n:]
    
    def get_all(self) -> list[Path]:
        """全フレームを取得"""
        return list(self.frames)
    
    def clear(self):
        """全フレーム削除"""
        for f in self.frames:
            if f.exists():
                f.unlink()
        self.frames.clear()
        
        # 空のディレクトリも削除
        if self.output_dir.exists() and not any(self.output_dir.iterdir()):
            self.output_dir.rmdir()
    
    def mark_error(self):
        """手動でエラーをマーク（SSを保持させる）"""
        self._error_occurred = True


# =============================================================================
# 使用例
# =============================================================================

def _cli_main() -> int:
    print("=== Demo: Successful Task ===")
    with TaskScreenshotBuffer(task_name="demo_success") as buffer:
        print("Simulating 5 second task...")
        time.sleep(5)
        print(f"Captured {len(buffer.frames)} frames")

    print("\n=== Demo: Failed Task ===")
    try:
        with TaskScreenshotBuffer(task_name="demo_error") as buffer:
            print("Simulating 3 second task then error...")
            time.sleep(3)
            raise ValueError("Simulated error!")
    except ValueError:
        print("Error caught! Screenshots should be kept for debugging.")
    return 0


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
    raise SystemExit(
        _run_logged_main("desktop", "task_screenshot_buffer", _cli_main, phase_name="TASK_SCREENSHOT_BUFFER_RUN")
    )

