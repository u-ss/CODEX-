# -*- coding: utf-8 -*-
"""
Desktop Tools - Screenshot テスト
"""

import pytest
from pathlib import Path
import tempfile
import sys

# パス追加
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tools.screenshot import (
    capture_all_monitors,
    capture_monitor,
    get_monitor_count,
    HAS_MSS,
)


@pytest.mark.skipif(not HAS_MSS, reason="mss not installed")
class TestScreenshot:
    """スクリーンショットツールのテスト"""
    
    def test_get_monitor_count(self):
        """モニター数が1以上であることを確認"""
        count = get_monitor_count()
        assert count >= 1, "少なくとも1つのモニターが必要"
    
    def test_capture_all_monitors(self):
        """全モニター撮影が正常に動作することを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = capture_all_monitors(output_dir=Path(tmpdir))
            
            # リストが返ること
            assert isinstance(paths, list)
            
            # 少なくとも1つのファイルが生成されること
            assert len(paths) >= 1
            
            # 各ファイルが存在すること
            for p in paths:
                assert p.exists(), f"ファイルが存在しない: {p}"
                assert p.suffix == ".png", f"PNG形式でない: {p}"
    
    def test_capture_monitor(self):
        """特定モニター撮影が正常に動作することを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_monitor.png"
            result = capture_monitor(1, output_path)
            
            assert result.exists(), "ファイルが存在しない"
            assert result == output_path
    
    def test_capture_monitor_invalid_index(self):
        """無効なモニター番号でエラーが発生することを確認"""
        with pytest.raises(IndexError):
            capture_monitor(999)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
