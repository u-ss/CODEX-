"""
Learning Store - SQLite Read Model for Desktop Agent Learning

ChatGPT相談（Rally 2, 5）で設計したSQLite永続化層。
JSONL（Source of Truth）から集計した候補統計・CB状態を管理。
"""

import sqlite3
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# デフォルトDBパス
DEFAULT_DB_PATH = Path.home() / ".chatgpt_agent" / "learning.db"


@dataclass
class CandidateStats:
    """候補統計"""
    bucket_key: str      # screen_key:intent:role
    selector_id: str     # CSS/XPath/UIA等の識別子
    n: int = 0           # 試行回数
    sum_reward: float = 0.0
    misclick_count: int = 0
    timeout_count: int = 0
    not_found_count: int = 0
    last_seen: int = 0   # Unix timestamp
    
    @property
    def mean_reward(self) -> float:
        """平均報酬"""
        return self.sum_reward / self.n if self.n > 0 else 0.0
    
    @property
    def misclick_rate(self) -> float:
        """misclick率"""
        return self.misclick_count / self.n if self.n > 0 else 0.0


@dataclass
class CandidateCB:
    """候補CB状態"""
    bucket_key: str
    selector_id: str
    state: str = "CLOSED"  # CLOSED / OPEN / HALF_OPEN
    open_until: int = 0    # OPEN解除時刻（Unix timestamp）
    ema_fail: float = 0.0  # EMA失敗率
    attempts: int = 0      # 試行回数（min_samples判定用）


class LearningStore:
    """SQLite学習ストア"""
    
    # 報酬設定（ChatGPT Rally 4より）
    REWARD_SUCCESS = 1.0
    REWARD_MISCLICK = -1.0
    REWARD_NOT_FOUND = -0.6
    REWARD_TIMEOUT = -0.4
    REWARD_STATE_MISMATCH = -1.0
    
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """テーブル初期化"""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_stats (
                    bucket_key TEXT,
                    selector_id TEXT,
                    n INTEGER DEFAULT 0,
                    sum_reward REAL DEFAULT 0.0,
                    misclick_count INTEGER DEFAULT 0,
                    timeout_count INTEGER DEFAULT 0,
                    not_found_count INTEGER DEFAULT 0,
                    last_seen INTEGER DEFAULT 0,
                    PRIMARY KEY (bucket_key, selector_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_cb (
                    bucket_key TEXT,
                    selector_id TEXT,
                    state TEXT DEFAULT 'CLOSED',
                    open_until INTEGER DEFAULT 0,
                    ema_fail REAL DEFAULT 0.0,
                    attempts INTEGER DEFAULT 0,
                    PRIMARY KEY (bucket_key, selector_id)
                )
            """)
            conn.commit()
    
    @contextmanager
    def _conn(self):
        """DB接続コンテキスト"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            yield conn
        finally:
            conn.close()
    
    # ========================================
    # 候補統計
    # ========================================
    
    def get_stats(self, bucket_key: str, selector_id: str) -> Optional[CandidateStats]:
        """候補統計を取得"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM candidate_stats WHERE bucket_key=? AND selector_id=?",
                (bucket_key, selector_id)
            ).fetchone()
            if row:
                return CandidateStats(*row)
        return None
    
    def get_bucket_stats(self, bucket_key: str) -> List[CandidateStats]:
        """バケット内の全候補統計を取得"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM candidate_stats WHERE bucket_key=?",
                (bucket_key,)
            ).fetchall()
            return [CandidateStats(*r) for r in rows]
    
    def upsert_stats(self, stats: CandidateStats):
        """候補統計を更新（なければ挿入）"""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO candidate_stats VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(bucket_key, selector_id) DO UPDATE SET
                    n=excluded.n,
                    sum_reward=excluded.sum_reward,
                    misclick_count=excluded.misclick_count,
                    timeout_count=excluded.timeout_count,
                    not_found_count=excluded.not_found_count,
                    last_seen=excluded.last_seen
            """, (
                stats.bucket_key, stats.selector_id, stats.n,
                stats.sum_reward, stats.misclick_count,
                stats.timeout_count, stats.not_found_count, stats.last_seen
            ))
            conn.commit()
    
    def record_outcome(
        self,
        bucket_key: str,
        selector_id: str,
        outcome: str  # success/misclick/not_found/timeout/state_mismatch
    ) -> float:
        """
        試行結果を記録し、報酬を返す
        """
        # 報酬計算
        reward = {
            "success": self.REWARD_SUCCESS,
            "misclick": self.REWARD_MISCLICK,
            "not_found": self.REWARD_NOT_FOUND,
            "timeout": self.REWARD_TIMEOUT,
            "state_mismatch": self.REWARD_STATE_MISMATCH,
        }.get(outcome, 0.0)
        
        # 統計更新
        stats = self.get_stats(bucket_key, selector_id)
        if stats is None:
            stats = CandidateStats(bucket_key, selector_id)
        
        stats.n += 1
        stats.sum_reward += reward
        stats.last_seen = int(time.time())
        
        if outcome == "misclick":
            stats.misclick_count += 1
        elif outcome == "timeout":
            stats.timeout_count += 1
        elif outcome == "not_found":
            stats.not_found_count += 1
        
        self.upsert_stats(stats)
        
        # CB状態も更新
        self._update_cb_ema(bucket_key, selector_id, outcome)
        
        return reward
    
    # ========================================
    # CB状態
    # ========================================
    
    def get_cb(self, bucket_key: str, selector_id: str) -> Optional[CandidateCB]:
        """CB状態を取得"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM candidate_cb WHERE bucket_key=? AND selector_id=?",
                (bucket_key, selector_id)
            ).fetchone()
            if row:
                return CandidateCB(*row)
        return None
    
    def upsert_cb(self, cb: CandidateCB):
        """CB状態を更新"""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO candidate_cb VALUES (?,?,?,?,?,?)
                ON CONFLICT(bucket_key, selector_id) DO UPDATE SET
                    state=excluded.state,
                    open_until=excluded.open_until,
                    ema_fail=excluded.ema_fail,
                    attempts=excluded.attempts
            """, (
                cb.bucket_key, cb.selector_id, cb.state,
                cb.open_until, cb.ema_fail, cb.attempts
            ))
            conn.commit()
    
    def _update_cb_ema(
        self,
        bucket_key: str,
        selector_id: str,
        outcome: str,
        alpha: float = 0.25,
        min_samples: int = 5,
        fail_threshold: float = 0.5,
        cooldown_sec: int = 30
    ):
        """EMAベースでCB状態を更新"""
        cb = self.get_cb(bucket_key, selector_id)
        if cb is None:
            cb = CandidateCB(bucket_key, selector_id)
        
        # 失敗判定（ChatGPT Rally 3: 重み付き）
        fail_weight = {
            "success": 0.0,
            "timeout": 0.5,
            "not_found": 0.7,
            "misclick": 1.0,
            "state_mismatch": 1.0,
        }.get(outcome, 0.5)
        
        # EMA更新: new_ema = α * x + (1-α) * old_ema
        cb.ema_fail = alpha * fail_weight + (1 - alpha) * cb.ema_fail
        cb.attempts += 1
        
        now = int(time.time())
        
        # 状態遷移
        if cb.state == "CLOSED":
            # OPEN判定: EMA >= threshold かつ attempts >= min_samples
            if cb.ema_fail >= fail_threshold and cb.attempts >= min_samples:
                cb.state = "OPEN"
                cb.open_until = now + cooldown_sec
                logger.warning(f"CB OPEN: {bucket_key}:{selector_id} (ema={cb.ema_fail:.2f})")
        
        elif cb.state == "OPEN":
            # cooldown経過でHALF_OPEN
            if now >= cb.open_until:
                cb.state = "HALF_OPEN"
                logger.info(f"CB HALF_OPEN: {bucket_key}:{selector_id}")
        
        elif cb.state == "HALF_OPEN":
            if outcome == "success":
                cb.state = "CLOSED"
                cb.ema_fail = 0.0
                cb.attempts = 0
                logger.info(f"CB CLOSED (success): {bucket_key}:{selector_id}")
            elif fail_weight >= 0.5:
                cb.state = "OPEN"
                cb.open_until = now + cooldown_sec
                logger.warning(f"CB OPEN (probe failed): {bucket_key}:{selector_id}")
        
        self.upsert_cb(cb)
    
    def is_open(self, bucket_key: str, selector_id: str) -> bool:
        """候補がOPEN状態か"""
        cb = self.get_cb(bucket_key, selector_id)
        if cb is None:
            return False
        
        now = int(time.time())
        
        # OPEN状態かつcooldown中
        if cb.state == "OPEN" and now < cb.open_until:
            return True
        
        # cooldown経過していればHALF_OPENへ遷移
        if cb.state == "OPEN" and now >= cb.open_until:
            cb.state = "HALF_OPEN"
            self.upsert_cb(cb)
        
        return False
    
    def reset(self, bucket_key: Optional[str] = None):
        """リセット（テスト用）"""
        with self._conn() as conn:
            if bucket_key:
                conn.execute("DELETE FROM candidate_stats WHERE bucket_key=?", (bucket_key,))
                conn.execute("DELETE FROM candidate_cb WHERE bucket_key=?", (bucket_key,))
            else:
                conn.execute("DELETE FROM candidate_stats")
                conn.execute("DELETE FROM candidate_cb")
            conn.commit()


# テスト
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== Learning Store テスト ===")
    
    store = LearningStore(Path("_temp/test_learning.db"))
    store.reset()
    
    bucket = "vscode:click:button"
    
    # テスト1: outcome記録
    print("\n--- Test 1: Record Outcomes ---")
    store.record_outcome(bucket, "css:#submit", "success")
    store.record_outcome(bucket, "css:#submit", "success")
    store.record_outcome(bucket, "xpath://button", "misclick")
    
    stats = store.get_stats(bucket, "css:#submit")
    print(f"css:#submit: n={stats.n}, mean={stats.mean_reward:.2f}")
    
    # テスト2: CB状態
    print("\n--- Test 2: CB State ---")
    for _ in range(5):
        store.record_outcome(bucket, "bad_selector", "misclick")
    
    cb = store.get_cb(bucket, "bad_selector")
    print(f"bad_selector: state={cb.state}, ema={cb.ema_fail:.2f}")
    
    is_open = store.is_open(bucket, "bad_selector")
    print(f"is_open: {is_open}")
    
    print("\n✅ テスト完了")
