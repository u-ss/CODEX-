"""
スクリーンショット リングバッファ
- 1秒ごとにSSを撮影
- 最大N枚まで保持、古いものは自動削除
- バックグラウンドで動作
"""

import mss
import time
import os
import threading
from pathlib import Path
from PIL import Image
from datetime import datetime
from collections import deque

class ScreenshotRingBuffer:
    def __init__(
        self,
        output_dir: str = "_screenshots/buffer",  # Google Drive連携済み
        max_frames: int = 60,  # 最大60枚（60秒分）
        interval_sec: float = 1.0,
        monitor_index: int = 0,  # 0=全画面, 1=プライマリ, 2=セカンダリ
        resize_ratio: float = 0.5,  # 50%にリサイズ（軽量化）
        jpeg_quality: int = 50,  # JPEG品質（軽量化）
    ):
        self.resize_ratio = resize_ratio
        self.jpeg_quality = jpeg_quality
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_frames = max_frames
        self.interval_sec = interval_sec
        self.monitor_index = monitor_index
        
        self.frames: deque = deque(maxlen=max_frames)
        self._running = False
        self._thread = None
    
    def start(self):
        """バックグラウンドでキャプチャ開始"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print(f"📸 Started: {self.max_frames} frames, {self.interval_sec}s interval")
    
    def stop(self):
        """キャプチャ停止"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        print("⏹️ Stopped")
    
    def _capture_loop(self):
        with mss.mss() as sct:
            while self._running:
                try:
                    # キャプチャ
                    raw = sct.grab(sct.monitors[self.monitor_index])
                    img = Image.frombytes("RGB", raw.size, raw.rgb)
                    
                    # リサイズ（軽量化）
                    if self.resize_ratio < 1.0:
                        new_size = (int(raw.width * self.resize_ratio), 
                                    int(raw.height * self.resize_ratio))
                        img = img.resize(new_size, Image.Resampling.LANCZOS)
                    
                    # ファイル名（タイムスタンプ）
                    ts = datetime.now().strftime("%H%M%S_%f")[:-3]  # HHMMSSmmm
                    filename = f"ss_{ts}.jpg"
                    filepath = self.output_dir / filename
                    
                    # JPEGで保存（軽量化）
                    img.save(filepath, "JPEG", quality=self.jpeg_quality)
                    
                    # 古いフレームを削除
                    if len(self.frames) >= self.max_frames:
                        old_file = self.frames[0]
                        if old_file.exists():
                            old_file.unlink()
                    
                    # 新しいフレームを追加
                    self.frames.append(filepath)
                    
                except Exception as e:
                    print(f"Capture error: {e}")
                
                time.sleep(self.interval_sec)
    
    def get_latest(self, n: int = 1) -> list[Path]:
        """最新のN枚を取得"""
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
        print("🗑️ Cleared all frames")


# =============================================================================
# 使用例
# =============================================================================

def _cli_main() -> int:
    # リングバッファ作成（60秒分、1秒間隔）
    buffer = ScreenshotRingBuffer(
        output_dir="_screenshots/buffer",
        max_frames=60,
        interval_sec=1.0,
        monitor_index=0,  # 全画面
    )

    buffer.start()
    try:
        print("Running for 10 seconds...")
        time.sleep(10)
        latest = buffer.get_latest(5)
        print(f"Latest 5 frames: {[f.name for f in latest]}")
    finally:
        buffer.stop()
        print(f"Total frames in buffer: {len(buffer.frames)}")
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
    raise SystemExit(_run_logged_main("desktop", "screenshot_buffer", _cli_main, phase_name="SCREENSHOT_BUFFER_RUN"))

