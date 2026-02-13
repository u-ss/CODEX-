# locator_bank.py - セレクタ候補の管理
# ChatGPT 5.2相談（ラリー2,4）に基づく実装

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class CandidateStats:
    """セレクタ候補の統計"""
    ok: int = 0
    fail: int = 0
    fp: int = 0  # 誤検知（別要素を掴んだ）
    avg_ms: int = 100
    last_ok: Optional[str] = None


@dataclass
class LocatorCandidate:
    """セレクタ候補"""
    id: str
    type: str  # role, css, xpath, text
    selector: Dict[str, Any] = field(default_factory=dict)
    preconditions: List[str] = field(default_factory=list)  # page_ready, logged_in, etc.
    brittleness: float = 0.5  # 壊れやすさ（低いほど良い）
    stats: CandidateStats = field(default_factory=CandidateStats)


@dataclass
class LocatorTarget:
    """ターゲット（複数候補を持つ）"""
    target_id: str
    description: str
    match_context: Dict[str, Any] = field(default_factory=dict)  # host, path_prefix
    candidates: List[LocatorCandidate] = field(default_factory=list)


class LocatorBank:
    """
    セレクタ候補の管理。
    ターゲットごとに複数のセレクタ候補を持ち、統計に基づいて最適なものを選択。
    """
    
    def __init__(self, bank_path: Path):
        self.bank_path = bank_path
        self.targets: Dict[str, LocatorTarget] = {}
        self._load()
    
    def _load(self) -> None:
        """バンクを読み込み"""
        if not self.bank_path.exists():
            return
        try:
            data = json.loads(self.bank_path.read_text(encoding="utf-8"))
            for tid, tdata in data.get("targets", {}).items():
                candidates = []
                for cdata in tdata.get("candidates", []):
                    stats = CandidateStats(
                        ok=cdata.get("stats", {}).get("ok", 0),
                        fail=cdata.get("stats", {}).get("fail", 0),
                        fp=cdata.get("stats", {}).get("fp", 0),
                        avg_ms=cdata.get("stats", {}).get("avg_ms", 100),
                        last_ok=cdata.get("stats", {}).get("last_ok"),
                    )
                    candidates.append(LocatorCandidate(
                        id=cdata.get("id", ""),
                        type=cdata.get("type", "css"),
                        selector=cdata.get("selector", {}),
                        preconditions=cdata.get("preconditions", []),
                        brittleness=cdata.get("brittleness", 0.5),
                        stats=stats,
                    ))
                self.targets[tid] = LocatorTarget(
                    target_id=tid,
                    description=tdata.get("description", ""),
                    match_context=tdata.get("match_context", {}),
                    candidates=candidates,
                )
        except Exception:
            pass
    
    def _save(self) -> None:
        """バンクを保存"""
        data = {"version": 1, "targets": {}}
        for tid, target in self.targets.items():
            candidates = []
            for c in target.candidates:
                candidates.append({
                    "id": c.id,
                    "type": c.type,
                    "selector": c.selector,
                    "preconditions": c.preconditions,
                    "brittleness": c.brittleness,
                    "stats": {
                        "ok": c.stats.ok,
                        "fail": c.stats.fail,
                        "fp": c.stats.fp,
                        "avg_ms": c.stats.avg_ms,
                        "last_ok": c.stats.last_ok,
                    },
                })
            data["targets"][tid] = {
                "description": target.description,
                "match_context": target.match_context,
                "candidates": candidates,
            }
        self.bank_path.parent.mkdir(parents=True, exist_ok=True)
        self.bank_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    def get_target(self, target_id: str) -> Optional[LocatorTarget]:
        """ターゲットを取得"""
        return self.targets.get(target_id)
    
    def choose_best_candidate(self, target_id: str, current_env: Dict[str, Any]) -> Optional[LocatorCandidate]:
        """
        ターゲットの候補から最適なものを選択。
        スコア = 0.55 * beta_mean + 0.25 * recency - 0.10 * brittleness - 0.10 * fp_rate
        """
        target = self.targets.get(target_id)
        if not target or not target.candidates:
            return None
        
        def beta_mean(ok: int, fail: int) -> float:
            return (ok + 1) / (ok + fail + 2)
        
        def recency_score(last_ok: Optional[str]) -> float:
            if not last_ok:
                return 0.0
            try:
                t = datetime.fromisoformat(last_ok.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
                from math import exp
                return exp(-age_days * 0.1)  # 7日で半減
            except Exception:
                return 0.0
        
        best = None
        best_score = -1e9
        for c in target.candidates:
            p = beta_mean(c.stats.ok, c.stats.fail)
            r = recency_score(c.stats.last_ok)
            fp_rate = c.stats.fp / max(c.stats.ok + c.stats.fail, 1)
            score = 0.55 * p + 0.25 * r - 0.10 * c.brittleness - 0.10 * fp_rate
            if score > best_score:
                best_score = score
                best = c
        return best
    
    def update_stats_success(self, target_id: str, candidate_id: str, elapsed_ms: int) -> None:
        """成功時の統計更新"""
        target = self.targets.get(target_id)
        if not target:
            return
        for c in target.candidates:
            if c.id == candidate_id:
                c.stats.ok += 1
                c.stats.fail = max(0, c.stats.fail)  # リセットしない、ただしfail_streak用
                prev = c.stats.avg_ms
                if prev is None or prev == 0:
                    c.stats.avg_ms = elapsed_ms
                else:
                    c.stats.avg_ms = int(prev * 0.8 + elapsed_ms * 0.2)  # EWMA
                c.stats.last_ok = now_iso()
                break
        self._save()
    
    def update_stats_fail(self, target_id: str, candidate_id: str, is_fp: bool = False) -> None:
        """失敗時の統計更新"""
        target = self.targets.get(target_id)
        if not target:
            return
        for c in target.candidates:
            if c.id == candidate_id:
                c.stats.fail += 1
                if is_fp:
                    c.stats.fp += 1
                # brittlenessを少し上げる
                c.brittleness = min(1.0, c.brittleness + 0.05)
                break
        self._save()
    
    def add_candidate(self, target_id: str, candidate: LocatorCandidate) -> None:
        """候補を追加"""
        if target_id not in self.targets:
            self.targets[target_id] = LocatorTarget(
                target_id=target_id,
                description="",
                match_context={},
                candidates=[],
            )
        self.targets[target_id].candidates.append(candidate)
        self._save()
    
    def register_target(self, target: LocatorTarget) -> None:
        """ターゲットを登録"""
        self.targets[target.target_id] = target
        self._save()
