"""
Observation Transaction（観測トランザクション）

目的: 観測の"整合性"を保証し、「現実と違う認識」を防ぐ

ChatGPT 5.2フィードバック（2026-02-05 Round6）より:
「DOM/UIA/SSの更新タイミングがズレると『現実と違う認識』が残る」

設計:
- 観測をFrame/Revisionとして束ねる（同一フレーム内のDOM/UIA/SSは同じ時点の証拠）
- staleness（鮮度）を必ず判定し、古い証拠でbeliefを更新しない
- 矛盾時の再観測ルールが固定（どのプローブを優先するか）
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any
import time


class ObservationType(Enum):
    """観測タイプ"""
    DOM = "dom"
    UIA = "uia"
    SS = "ss"


class FrameStatus(Enum):
    """フレーム状態"""
    OPEN = "open"           # 観測中
    CLOSED = "closed"       # 確定済み
    STALE = "stale"         # 古い（再観測必要）
    CONFLICTING = "conflicting"  # 矛盾あり


@dataclass
class Observation:
    """単一観測"""
    obs_type: ObservationType
    key: str                  # 観測キー（url, title, element:xxx等）
    value: Any
    timestamp: float          # Unix timestamp
    confidence: float = 1.0
    
    def age_ms(self) -> float:
        """経過時間（ミリ秒）"""
        return (time.time() - self.timestamp) * 1000
    
    def is_stale(self, max_age_ms: float = 2000) -> bool:
        """古くなったか"""
        return self.age_ms() > max_age_ms


@dataclass
class ObservationFrame:
    """観測フレーム（同一時点の観測群）"""
    
    frame_id: int
    started_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    status: FrameStatus = FrameStatus.OPEN
    
    observations: list[Observation] = field(default_factory=list)
    conflicts: list[tuple[Observation, Observation]] = field(default_factory=list)
    
    # 鮮度制約
    max_frame_duration_ms: float = 500   # フレーム内の最大時間差
    max_age_ms: float = 3000             # フレーム全体の最大経過時間
    
    def add(self, obs: Observation) -> bool:
        """観測を追加"""
        if self.status != FrameStatus.OPEN:
            return False
        
        # 時間差チェック
        if self.observations:
            first_ts = self.observations[0].timestamp
            if (obs.timestamp - first_ts) * 1000 > self.max_frame_duration_ms:
                # 時間差が大きすぎる
                return False
        
        self.observations.append(obs)
        return True
    
    def close(self) -> None:
        """フレームを確定"""
        self.closed_at = time.time()
        self._check_conflicts()
        
        if self.conflicts:
            self.status = FrameStatus.CONFLICTING
        else:
            self.status = FrameStatus.CLOSED
    
    def _check_conflicts(self) -> None:
        """矛盾をチェック"""
        # 同じキーで異なる値があれば矛盾
        by_key: dict[str, list[Observation]] = {}
        for obs in self.observations:
            key = f"{obs.obs_type.value}:{obs.key}"
            if key not in by_key:
                by_key[key] = []
            by_key[key].append(obs)
        
        for key, obs_list in by_key.items():
            if len(obs_list) > 1:
                # 値が異なるか確認
                values = set(str(o.value) for o in obs_list)
                if len(values) > 1:
                    self.conflicts.append((obs_list[0], obs_list[1]))
    
    def is_stale(self) -> bool:
        """フレームが古くなったか"""
        if self.status == FrameStatus.STALE:
            return True
        
        age = (time.time() - self.started_at) * 1000
        if age > self.max_age_ms:
            self.status = FrameStatus.STALE
            return True
        
        return False
    
    def get_by_type(self, obs_type: ObservationType) -> list[Observation]:
        """タイプ別に観測を取得"""
        return [o for o in self.observations if o.obs_type == obs_type]
    
    def get_by_key(self, key: str) -> Optional[Observation]:
        """キーで観測を取得（最新）"""
        for obs in reversed(self.observations):
            if obs.key == key:
                return obs
        return None
    
    def get_summary(self) -> dict:
        """サマリ取得"""
        return {
            "frame_id": self.frame_id,
            "status": self.status.value,
            "observation_count": len(self.observations),
            "conflict_count": len(self.conflicts),
            "age_ms": int((time.time() - self.started_at) * 1000),
        }


class ObservationTransaction:
    """観測トランザクション管理"""
    
    def __init__(self):
        self.current_frame: Optional[ObservationFrame] = None
        self.frame_history: list[ObservationFrame] = []
        self.next_frame_id: int = 1
        self.max_history = 20
        
        # 再観測優先度（矛盾時）
        self.reprobe_priority = [
            ObservationType.DOM,  # DOM最優先
            ObservationType.UIA,
            ObservationType.SS,
        ]
    
    def begin(self) -> ObservationFrame:
        """新しいフレームを開始"""
        # 現在のフレームがあれば閉じる
        if self.current_frame and self.current_frame.status == FrameStatus.OPEN:
            self.current_frame.close()
            self._archive_frame(self.current_frame)
        
        self.current_frame = ObservationFrame(frame_id=self.next_frame_id)
        self.next_frame_id += 1
        
        return self.current_frame
    
    def record(self, obs_type: ObservationType, key: str, value: Any, confidence: float = 1.0) -> bool:
        """観測を記録"""
        if not self.current_frame or self.current_frame.status != FrameStatus.OPEN:
            self.begin()
        
        obs = Observation(
            obs_type=obs_type,
            key=key,
            value=value,
            timestamp=time.time(),
            confidence=confidence
        )
        
        return self.current_frame.add(obs)
    
    def commit(self) -> ObservationFrame:
        """フレームを確定"""
        if not self.current_frame:
            raise RuntimeError("No open frame")
        
        self.current_frame.close()
        frame = self.current_frame
        self._archive_frame(frame)
        self.current_frame = None
        
        return frame
    
    def rollback(self) -> None:
        """フレームを破棄"""
        self.current_frame = None
    
    def _archive_frame(self, frame: ObservationFrame) -> None:
        """フレームを履歴に追加"""
        self.frame_history.append(frame)
        if len(self.frame_history) > self.max_history:
            self.frame_history = self.frame_history[-self.max_history:]
    
    def needs_reprobe(self) -> tuple[bool, list[ObservationType]]:
        """再観測が必要か判定"""
        if not self.current_frame:
            return False, []
        
        # 古くなっていれば全再観測
        if self.current_frame.is_stale():
            return True, self.reprobe_priority
        
        # 矛盾があれば該当タイプを再観測
        if self.current_frame.conflicts:
            conflicting_types = set()
            for obs1, obs2 in self.current_frame.conflicts:
                conflicting_types.add(obs1.obs_type)
                conflicting_types.add(obs2.obs_type)
            
            # 優先度順にソート
            reprobe = [t for t in self.reprobe_priority if t in conflicting_types]
            return True, reprobe
        
        return False, []
    
    def get_latest_value(self, key: str) -> Optional[Any]:
        """最新の観測値を取得"""
        if self.current_frame:
            obs = self.current_frame.get_by_key(key)
            if obs:
                return obs.value
        
        # 履歴から検索
        for frame in reversed(self.frame_history):
            if frame.status == FrameStatus.CLOSED:
                obs = frame.get_by_key(key)
                if obs:
                    return obs.value
        
        return None
    
    def format_status(self) -> str:
        """ステータスをフォーマット"""
        lines = ["Observation Transaction Status:"]
        
        if self.current_frame:
            summary = self.current_frame.get_summary()
            lines.append(f"  Current Frame: #{summary['frame_id']}")
            lines.append(f"    Status: {summary['status']}")
            lines.append(f"    Observations: {summary['observation_count']}")
            lines.append(f"    Conflicts: {summary['conflict_count']}")
            lines.append(f"    Age: {summary['age_ms']}ms")
        else:
            lines.append("  No active frame")
        
        lines.append(f"  History: {len(self.frame_history)} frames")
        
        needs, types = self.needs_reprobe()
        if needs:
            lines.append(f"  ⚠️ Reprobe needed: {[t.value for t in types]}")
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("Observation Transaction テスト")
    print("=" * 60)
    
    tx = ObservationTransaction()
    
    # フレーム1: 正常
    print("\n--- フレーム1: 正常な観測 ---")
    tx.begin()
    tx.record(ObservationType.DOM, "url", "https://example.com/page1")
    tx.record(ObservationType.DOM, "title", "Page 1")
    tx.record(ObservationType.UIA, "foreground", "Brave")
    frame1 = tx.commit()
    print(f"Status: {frame1.status.value}")
    print(f"Observations: {len(frame1.observations)}")
    
    # フレーム2: 矛盾
    print("\n--- フレーム2: 矛盾あり ---")
    tx.begin()
    tx.record(ObservationType.DOM, "url", "https://example.com/page1")
    tx.record(ObservationType.DOM, "url", "https://example.com/page2")  # 矛盾
    frame2 = tx.commit()
    print(f"Status: {frame2.status.value}")
    print(f"Conflicts: {len(frame2.conflicts)}")
    
    needs, types = tx.needs_reprobe()
    if needs:
        print(f"Reprobe needed: {[t.value for t in types]}")
    
    # ステータス
    print("\n--- 現在のステータス ---")
    print(tx.format_status())
    
    print("\n" + "=" * 60)
    print("テスト完了")
