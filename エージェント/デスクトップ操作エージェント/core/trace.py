# trace.py - ステップTrace（運用サポート用ログ）
# ChatGPT 5.2相談（ラリー1）に基づく実装

from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Optional
import json
import time
import uuid
import hashlib


def now_iso() -> str:
    """現在時刻をISO形式で返す"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Trace:
    """
    1ステップごとのTraceログ（JSONL形式）
    
    用途:
    - サポート工数削減（何が起きたかを再現可能）
    - Replay/Regression Harness
    - 失敗分析
    
    記録内容:
    - run_id: 実行ID
    - ts: タイムスタンプ
    - step: ステップ番号
    - screen_key: 画面識別子
    - layer: 選択レイヤー（CDP/UIA/Pixel）
    - action: アクション種別
    - locator: 使用したロケータ
    - success: 成功/失敗
    - fail_type: 失敗分類
    - pre_screenshot_hash: 実行前SSハッシュ
    - post_screenshot_hash: 実行後SSハッシュ
    """
    
    def __init__(self, trace_dir: Path, run_id: Optional[str] = None):
        self.trace_dir = Path(trace_dir)
        self.run_id = run_id or f"run_{int(time.time() * 1000)}"
        self.step_count = 0
        
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.trace_dir / f"{self.run_id}.jsonl"
        self.screenshot_dir = self.trace_dir / "screenshots" / self.run_id
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
    
    def log(self, **evt) -> None:
        """イベントをログに追記"""
        self.step_count += 1
        evt = {
            "ts": now_iso(),
            "run_id": self.run_id,
            "step": self.step_count,
            **evt,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    
    def log_action(
        self,
        action: str,
        screen_key: str,
        layer: str,
        locator: Optional[Dict[str, Any]] = None,
        success: bool = True,
        fail_type: Optional[str] = None,
        fail_message: Optional[str] = None,
        elapsed_ms: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """アクション実行をログ"""
        evt = {
            "type": "action",
            "action": action,
            "screen_key": screen_key,
            "layer": layer,
            "locator": locator,
            "success": success,
            "fail_type": fail_type,
            "fail_message": fail_message,
            "elapsed_ms": elapsed_ms,
            "params": params,
        }
        if extra:
            evt.update(extra)
        self.log(**{k: v for k, v in evt.items() if v is not None})
    
    def log_screenshot(self, screenshot_bytes: bytes, label: str = "screen") -> str:
        """スクリーンショットを保存し、ハッシュを返す"""
        ss_hash = hashlib.sha256(screenshot_bytes).hexdigest()[:16]
        filename = f"{self.step_count:04d}_{label}_{ss_hash}.png"
        path = self.screenshot_dir / filename
        path.write_bytes(screenshot_bytes)
        
        self.log(type="screenshot", label=label, hash=ss_hash, path=str(path))
        return ss_hash
    
    def log_state(self, state_key: str, confidence: float, facts: Dict[str, Any]) -> None:
        """状態推定をログ"""
        self.log(
            type="state",
            state_key=state_key,
            confidence=confidence,
            facts=facts,
        )
    
    def log_decision(self, decision: str, reason: str, candidates: Optional[list] = None) -> None:
        """判断をログ"""
        self.log(
            type="decision",
            decision=decision,
            reason=reason,
            candidates=candidates,
        )
    
    def log_error(self, error_type: str, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        """エラーをログ"""
        self.log(
            type="error",
            error_type=error_type,
            message=message,
            detail=detail,
        )
    
    def read_logs(self) -> list:
        """ログを読み込んで返す"""
        if not self.log_path.exists():
            return []
        logs = []
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    logs.append(json.loads(line))
        return logs


class TraceReplay:
    """
    Traceログからの再生・分析
    """
    
    def __init__(self, trace_path: Path):
        self.trace_path = Path(trace_path)
    
    def load(self) -> list:
        """ログを読み込み"""
        if not self.trace_path.exists():
            return []
        logs = []
        with self.trace_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    logs.append(json.loads(line))
        return logs
    
    def get_failures(self) -> list:
        """失敗イベントのみ抽出"""
        logs = self.load()
        return [log for log in logs if log.get("success") is False]
    
    def get_actions(self) -> list:
        """アクションイベントのみ抽出"""
        logs = self.load()
        return [log for log in logs if log.get("type") == "action"]
    
    def summarize(self) -> Dict[str, Any]:
        """実行サマリを生成"""
        logs = self.load()
        actions = [log for log in logs if log.get("type") == "action"]
        failures = [log for log in actions if log.get("success") is False]
        
        return {
            "run_id": logs[0].get("run_id") if logs else None,
            "total_steps": len(logs),
            "total_actions": len(actions),
            "total_failures": len(failures),
            "success_rate": (len(actions) - len(failures)) / len(actions) if actions else 0,
            "failure_types": self._count_by_key(failures, "fail_type"),
            "actions_by_type": self._count_by_key(actions, "action"),
            "layers_used": self._count_by_key(actions, "layer"),
        }
    
    def _count_by_key(self, items: list, key: str) -> Dict[str, int]:
        """キーで集計"""
        counts: Dict[str, int] = {}
        for item in items:
            v = item.get(key, "unknown")
            counts[v] = counts.get(v, 0) + 1
        return counts
