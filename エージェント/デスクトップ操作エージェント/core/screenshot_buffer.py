"""
Screenshot Ring Buffer - SSリングバッファ

ChatGPT相談（Rally 6）で設計したSSリングバッファを実装:
- 最大サイズ: 30-60枚（イベント駆動）
- 保存優先度: Failure > Unknown > StateChange > Action > Routine
- TTL: 5分（ピン留め除く）
- run_id分離: 各実行ごとにディレクトリ分離
"""

import os
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path
from enum import Enum, auto
import logging
import shutil

logger = logging.getLogger(__name__)


class SSPriority(Enum):
    """SS保存優先度（高い方が重要）"""
    FAILURE = 5      # 失敗時: 最優先
    UNKNOWN = 4      # 未知状態突入
    STATE_CHANGE = 3 # 状態遷移
    ACTION = 2       # アクション実行前後
    ROUTINE = 1      # 定期観測


@dataclass
class ScreenshotEntry:
    """SS エントリ"""
    path: Path
    timestamp: datetime
    priority: SSPriority
    run_id: str
    context: Dict[str, Any] = field(default_factory=dict)
    pinned: bool = False  # ピン留め（TTL除外）
    
    def age_seconds(self) -> float:
        return (datetime.now() - self.timestamp).total_seconds()


@dataclass
class RingBufferConfig:
    """リングバッファ設定"""
    max_size: int = 60                    # 最大枚数
    unknown_extra: int = 20               # 未知状態時の追加バッファ
    ttl_seconds: int = 300                # TTL (5分)
    base_dir: str = "_screenshots"        # ベースディレクトリ
    cleanup_interval_seconds: int = 30    # クリーンアップ間隔


class ScreenshotRingBuffer:
    """SSリングバッファ"""
    
    def __init__(self, config: Optional[RingBufferConfig] = None):
        self.config = config or RingBufferConfig()
        self._entries: List[ScreenshotEntry] = []
        self._lock = threading.Lock()
        self._current_run_id: str = ""
        self._unknown_mode = False
        self._last_cleanup = time.time()
    
    def set_run_id(self, run_id: str):
        """実行ID設定（ディレクトリ分離用）"""
        self._current_run_id = run_id
        run_dir = Path(self.config.base_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
    
    def enter_unknown_mode(self):
        """未知状態突入（バッファ拡張）"""
        self._unknown_mode = True
        logger.info("Screenshot buffer: Unknown mode ON (+20 extra)")
    
    def exit_unknown_mode(self):
        """未知状態終了"""
        self._unknown_mode = False
        logger.info("Screenshot buffer: Unknown mode OFF")
    
    def get_max_size(self) -> int:
        """現在の最大サイズ"""
        base = self.config.max_size
        if self._unknown_mode:
            return base + self.config.unknown_extra
        return base
    
    def add(
        self,
        path: Path,
        priority: SSPriority,
        context: Optional[Dict[str, Any]] = None,
        pinned: bool = False
    ) -> ScreenshotEntry:
        """SS追加"""
        entry = ScreenshotEntry(
            path=path,
            timestamp=datetime.now(),
            priority=priority,
            run_id=self._current_run_id,
            context=context or {},
            pinned=pinned
        )
        
        with self._lock:
            self._entries.append(entry)
            self._maybe_cleanup()
        
        logger.debug(f"SS added: {path.name} ({priority.name})")
        return entry
    
    def pin(self, path: Path):
        """ピン留め（TTL除外）"""
        with self._lock:
            for entry in self._entries:
                if entry.path == path:
                    entry.pinned = True
                    logger.info(f"SS pinned: {path.name}")
                    return
    
    def unpin(self, path: Path):
        """ピン解除"""
        with self._lock:
            for entry in self._entries:
                if entry.path == path:
                    entry.pinned = False
                    return
    
    def _maybe_cleanup(self):
        """クリーンアップ（必要に応じて）"""
        now = time.time()
        if now - self._last_cleanup < self.config.cleanup_interval_seconds:
            return
        
        self._last_cleanup = now
        self._cleanup()
    
    def _cleanup(self):
        """クリーンアップ実行"""
        max_size = self.get_max_size()
        ttl_threshold = datetime.now() - timedelta(seconds=self.config.ttl_seconds)
        
        # TTL切れを削除（ピン留め除く）
        before_count = len(self._entries)
        expired = [
            e for e in self._entries
            if not e.pinned and e.timestamp < ttl_threshold
        ]
        
        for entry in expired:
            self._delete_entry(entry)
        
        self._entries = [e for e in self._entries if e not in expired]
        
        # サイズ超過を削除（優先度低い順）
        if len(self._entries) > max_size:
            # ピン留めでないものを優先度・時間順でソート
            deletable = [e for e in self._entries if not e.pinned]
            deletable.sort(key=lambda e: (e.priority.value, e.timestamp))
            
            excess = len(self._entries) - max_size
            to_delete = deletable[:excess]
            
            for entry in to_delete:
                self._delete_entry(entry)
            
            self._entries = [e for e in self._entries if e not in to_delete]
        
        after_count = len(self._entries)
        if before_count != after_count:
            logger.info(f"SS cleanup: {before_count} → {after_count}")
    
    def _delete_entry(self, entry: ScreenshotEntry):
        """ファイル削除"""
        try:
            if entry.path.exists():
                entry.path.unlink()
                logger.debug(f"SS deleted: {entry.path.name}")
        except Exception as e:
            logger.warning(f"Failed to delete SS: {e}")
    
    def get_recent(self, count: int = 10) -> List[ScreenshotEntry]:
        """直近のSSを取得"""
        with self._lock:
            sorted_entries = sorted(self._entries, key=lambda e: e.timestamp, reverse=True)
            return sorted_entries[:count]
    
    def get_by_priority(self, priority: SSPriority) -> List[ScreenshotEntry]:
        """優先度でフィルタ"""
        with self._lock:
            return [e for e in self._entries if e.priority == priority]
    
    def get_stats(self) -> Dict[str, Any]:
        """統計情報"""
        with self._lock:
            by_priority = {}
            for p in SSPriority:
                by_priority[p.name] = sum(1 for e in self._entries if e.priority == p)
            
            return {
                "total": len(self._entries),
                "max_size": self.get_max_size(),
                "unknown_mode": self._unknown_mode,
                "pinned": sum(1 for e in self._entries if e.pinned),
                "by_priority": by_priority
            }
    
    def clear_run(self, run_id: str):
        """特定run_idのSSを削除"""
        with self._lock:
            to_delete = [e for e in self._entries if e.run_id == run_id and not e.pinned]
            for entry in to_delete:
                self._delete_entry(entry)
            self._entries = [e for e in self._entries if e not in to_delete]
            logger.info(f"Cleared run: {run_id} ({len(to_delete)} entries)")


# 使用例とテスト
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== Screenshot Ring Buffer テスト ===")
    
    # テスト用一時ディレクトリ
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        config = RingBufferConfig(
            max_size=5,
            unknown_extra=2,
            ttl_seconds=10,
            base_dir=tmpdir
        )
        
        buffer = ScreenshotRingBuffer(config)
        buffer.set_run_id("test_run_001")
        
        # テスト1: 追加
        for i in range(3):
            path = Path(tmpdir) / f"ss_{i}.png"
            path.touch()  # ダミーファイル作成
            buffer.add(path, SSPriority.ROUTINE)
        
        stats = buffer.get_stats()
        print(f"Test 1 - Add 3: total={stats['total']} (expected: 3)")
        
        # テスト2: 優先度違い追加
        fail_path = Path(tmpdir) / "ss_fail.png"
        fail_path.touch()
        buffer.add(fail_path, SSPriority.FAILURE, pinned=True)
        
        stats = buffer.get_stats()
        print(f"Test 2 - Add Failure: pinned={stats['pinned']} (expected: 1)")
        
        # テスト3: Unknown mode
        buffer.enter_unknown_mode()
        max_size = buffer.get_max_size()
        print(f"Test 3 - Unknown mode: max_size={max_size} (expected: 7)")
        
        buffer.exit_unknown_mode()
        max_size = buffer.get_max_size()
        print(f"Test 3b - Normal mode: max_size={max_size} (expected: 5)")
        
        # テスト4: 超過時のクリーンアップ
        for i in range(10):
            path = Path(tmpdir) / f"ss_extra_{i}.png"
            path.touch()
            buffer.add(path, SSPriority.ROUTINE)
        
        buffer._cleanup()
        stats = buffer.get_stats()
        print(f"Test 4 - Cleanup: total={stats['total']} (expected: <=5)")
        
        # 結果
        passed = stats['total'] <= 5 and stats['pinned'] == 1
        print(f"\n{'✅ テスト完了' if passed else '❌ 一部失敗'}")
