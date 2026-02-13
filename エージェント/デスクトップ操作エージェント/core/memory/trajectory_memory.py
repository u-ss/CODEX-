# trajectory_memory.py - 成功/失敗の軌跡保存・読み込み
# ChatGPT 5.2相談（ラリー3,5）に基づく実装

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import time
from datetime import datetime, timezone
from math import exp


def now_iso() -> str:
    """現在時刻をISO形式で返す"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_load_json(path: Path) -> Dict[str, Any]:
    """安全にJSONを読み込み、失敗時は空dictを返す"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        bad = path.with_suffix(path.suffix + ".bad")
        try:
            path.rename(bad)
        except Exception:
            pass
        return {}


def safe_write_json(path: Path, obj: Dict[str, Any]) -> None:
    """安全にJSONを書き込む"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """辞書を深くマージ"""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def recency_score(last_ok_iso: Optional[str], half_life_days: float = 7.0) -> float:
    """最終成功からの経過日数に基づくスコア（新しいほど高い）"""
    if not last_ok_iso:
        return 0.0
    t = datetime.fromisoformat(last_ok_iso.replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    return exp(-age_days * (0.693 / half_life_days))


def calc_cost(step_count: int, total_ms: int, w_time: float = 0.2) -> float:
    """コスト計算: ステップ数 + 時間重み"""
    return step_count + (total_ms / 1000.0) * w_time


@dataclass
class TrajMeta:
    """軌跡メタ情報"""
    traj_id: str
    intent: str
    screen_key: str
    cost: float
    stats: Dict[str, Any] = field(default_factory=dict)
    env: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FailureRecord:
    """失敗記録"""
    ts: str
    run_id: str
    screen_key: str
    intent: str
    symptom: str
    failure_type: str
    detail: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)


class RunRecorder:
    """
    1実行(run)の軌跡をJSONLに記録し、最後に成功/失敗のmetaを返す。
    """
    
    def __init__(self, root: Path, run_id: str, screen_key: str, intent: str, env: Dict[str, Any]):
        self.root = root
        self.run_id = run_id
        self.screen_key = screen_key
        self.intent = intent
        self.env = env
        self.steps: List[Dict[str, Any]] = []
        self.started = time.time()
    
    def add_step(self, step: Dict[str, Any]) -> None:
        """ステップを追加"""
        step = dict(step)
        step.setdefault("ts", now_iso())
        step.setdefault("run_id", self.run_id)
        self.steps.append(step)
    
    def flush_jsonl(self, traj_path: Path) -> None:
        """軌跡をJSONLファイルに書き込む"""
        traj_path.parent.mkdir(parents=True, exist_ok=True)
        with traj_path.open("w", encoding="utf-8") as f:
            for s in self.steps:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
    
    def finalize(self) -> Dict[str, Any]:
        """実行終了時のメタ情報を返す"""
        total_ms = int((time.time() - self.started) * 1000)
        return {
            "run_id": self.run_id,
            "screen_key": self.screen_key,
            "intent": self.intent,
            "env": self.env,
            "total_ms": total_ms,
            "step_count": len(self.steps),
        }


class TrajectoryMemory:
    """
    site/task単位のメモリ管理。
    memory/perplexity/search/ のような単位で使う想定。
    """
    
    def __init__(self, memory_root: Path, site: str, task: str):
        self.root = memory_root / site / task
        self.site = site
        self.task = task
        
        self.paths = {
            "manifest_global": memory_root / "global" / "manifest.json",
            "manifest_site": memory_root / site / "manifest.json",
            "manifest_task": self.root / "manifest.json",
            "failures": self.root / "failures.jsonl",
            "trajectories_dir": self.root / "trajectories",
        }
        self.manifest: Dict[str, Any] = {}
    
    def init_dirs(self) -> None:
        """必要なディレクトリを作成"""
        (self.root / "trajectories").mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts" / "traces").mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts" / "har").mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts" / "screenshots").mkdir(parents=True, exist_ok=True)
    
    def load_manifest_chain(self) -> Dict[str, Any]:
        """グローバル→サイト→タスクの順でマニフェストをマージ"""
        m = {}
        m = deep_merge(m, safe_load_json(self.paths["manifest_global"]))
        m = deep_merge(m, safe_load_json(self.paths["manifest_site"]))
        m = deep_merge(m, safe_load_json(self.paths["manifest_task"]))
        self.manifest = m
        return m
    
    def start_run(self, screen_key: str, intent: str, env: Dict[str, Any]) -> RunRecorder:
        """新しい実行を開始"""
        run_id = f"run_{int(time.time() * 1000)}"
        return RunRecorder(self.root, run_id, screen_key, intent, env)
    
    def find_best_trajectory(self, screen_key: str, intent: str, current_env: Dict[str, Any]) -> Optional[TrajMeta]:
        """
        screen_key + intentにマッチする軌跡から最適なものを選択。
        """
        items = self.manifest.get("trajectories", [])
        out: List[TrajMeta] = []
        
        def parts(k: str) -> tuple:
            ps = k.split("|")
            host = ps[0] if len(ps) > 0 else ""
            bucket = ps[1] if len(ps) > 1 else ""
            return host, bucket
        
        host0, bucket0 = parts(screen_key)
        seen = set()
        
        for mode in ("exact", "host_bucket", "host"):
            for it in items:
                if it.get("intent") != intent:
                    continue
                sk = it.get("screen_key", "")
                host, bucket = parts(sk)
                ok = (sk == screen_key) if mode == "exact" else ((host == host0 and bucket == bucket0) if mode == "host_bucket" else (host == host0))
                if not ok:
                    continue
                tid = it.get("traj_id")
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                out.append(TrajMeta(
                    traj_id=tid,
                    intent=intent,
                    screen_key=sk,
                    cost=float(it.get("cost", 9999.0)),
                    stats=it.get("stats", {}),
                    env=it.get("env", {}),
                ))
        
        if not out:
            return None
        
        return self._choose_best(out, current_env)
    
    def _choose_best(self, candidates: List[TrajMeta], current_env: Dict[str, Any]) -> Optional[TrajMeta]:
        """最適な軌跡を選択"""
        def env_sim(a: Dict[str, Any], b: Dict[str, Any]) -> float:
            keys = ["locale", "browser", "viewport"]
            same = 0
            tot = 0
            for k in keys:
                if k in a and k in b:
                    tot += 1
                    if a[k] == b[k]:
                        same += 1
            return same / tot if tot else 0.5
        
        def beta_mean(ok: int, fail: int) -> float:
            return (ok + 1) / (ok + fail + 2)
        
        best = None
        best_score = -1e9
        for c in candidates:
            ok = int(c.stats.get("ok", 0))
            fail = int(c.stats.get("fail", 0))
            p = beta_mean(ok, fail)
            r = recency_score(c.stats.get("last_ok"))
            sim = env_sim(c.env, current_env)
            cost_penalty = min(c.cost / 20.0, 1.0)
            score = 0.55 * p + 0.25 * r + 0.10 * sim - 0.10 * cost_penalty
            if score > best_score:
                best_score = score
                best = c
        return best
    
    def persist_success(self, run: RunRecorder, traj_id: str, artifacts: Dict[str, str]) -> None:
        """成功した実行を永続化"""
        traj_path = self.paths["trajectories_dir"] / f"{traj_id}.jsonl"
        run.flush_jsonl(traj_path)
        
        meta = run.finalize()
        total_ms = meta["total_ms"]
        step_count = meta["step_count"]
        cost = calc_cost(step_count, total_ms)
        
        # manifest_task更新（成功統計）
        manifest = safe_load_json(self.paths["manifest_task"])
        manifest.setdefault("trajectories", [])
        
        # 該当trajのメタを更新 or 追加
        found = None
        for it in manifest["trajectories"]:
            if it.get("traj_id") == traj_id:
                found = it
                break
        if found is None:
            found = {
                "traj_id": traj_id,
                "intent": meta["intent"],
                "screen_key": meta["screen_key"],
                "stats": {"ok": 0, "fail": 0, "last_ok": None},
                "env": meta["env"],
                "cost": cost,
            }
            manifest["trajectories"].append(found)
        
        st = found.setdefault("stats", {})
        st["ok"] = int(st.get("ok", 0)) + 1
        st["last_ok"] = now_iso()
        
        safe_write_json(self.paths["manifest_task"], manifest)
        self.manifest = manifest
    
    def persist_failure(self, run: RunRecorder, failure: FailureRecord) -> None:
        """失敗した実行を記録"""
        self.paths["failures"].parent.mkdir(parents=True, exist_ok=True)
        with self.paths["failures"].open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": failure.ts,
                "run_id": failure.run_id,
                "screen_key": failure.screen_key,
                "intent": failure.intent,
                "symptom": failure.symptom,
                "failure_type": failure.failure_type,
                "detail": failure.detail,
                "evidence": failure.evidence,
            }, ensure_ascii=False) + "\n")
        
        # stats更新
        manifest = safe_load_json(self.paths["manifest_task"])
        for it in manifest.get("trajectories", []):
            if it.get("screen_key") == failure.screen_key and it.get("intent") == failure.intent:
                st = it.setdefault("stats", {})
                st["fail"] = int(st.get("fail", 0)) + 1
                st["fail_streak"] = int(st.get("fail_streak", 0)) + 1
                break
        safe_write_json(self.paths["manifest_task"], manifest)
