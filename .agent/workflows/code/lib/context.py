# -*- coding: utf-8 -*-
"""
Implementation Agent v4.2.4 - Context Module
フェーズ間で共有する状態管理（RunContext）

v4.2.4 変更点:
- phase_results を正式フィールド追加
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from hashlib import sha256


@dataclass
class TaskContract:
    """タスク契約：目標と受け入れ条件"""
    goal: str = ""
    acceptance_criteria: List[str] = field(default_factory=list)
    non_goals: List[str] = field(default_factory=list)
    scope: List[str] = field(default_factory=list)  # 対象ファイル/モジュール
    constraints: List[str] = field(default_factory=list)  # OS/言語/禁止事項


@dataclass
class CodebaseMap:
    """コードベース地図"""
    entrypoints: List[str] = field(default_factory=list)  # CLI/server/main
    key_modules: List[str] = field(default_factory=list)
    run_commands: Dict[str, str] = field(default_factory=dict)  # dev/build/test
    test_commands: Dict[str, str] = field(default_factory=dict)
    no_touch_zones: List[str] = field(default_factory=list)  # generated/vendor


@dataclass
class Evidence:
    """調査証拠"""
    evidence_id: str
    type: str  # grep/file/outline
    path: str
    line_range: Optional[str] = None
    snippet_hash: Optional[str] = None
    note: str = ""
    
    @classmethod
    def from_grep(cls, path: str, line: int, content: str) -> "Evidence":
        snippet_hash = sha256(content.encode()).hexdigest()[:12]
        return cls(
            evidence_id=f"ev_{snippet_hash}",
            type="grep",
            path=path,
            line_range=str(line),
            snippet_hash=snippet_hash
        )


@dataclass
class ChangeTarget:
    """変更対象"""
    file: str
    intent: str
    steps: List[str] = field(default_factory=list)


@dataclass
class ChangePlan:
    """変更計画"""
    targets: List[ChangeTarget] = field(default_factory=list)
    test_strategy: str = ""  # 何をどう検証するか
    risk_controls: List[str] = field(default_factory=list)
    rollback_steps: List[str] = field(default_factory=list)


@dataclass
class ExecutionTrace:
    """実行トレース"""
    phase: str
    command: str
    exit_code: int
    stdout_hash: str
    stderr_hash: str
    duration_ms: int
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class Metrics:
    """品質メトリクス"""
    test_pass_rate: float = 0.0
    coverage: float = 0.0
    lint_errors: int = 0
    flaky_signals: int = 0


@dataclass
class RunContext:
    """実行コンテキスト：全フェーズが参照/更新する共有状態"""
    run_id: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 契約
    task_contract: TaskContract = field(default_factory=TaskContract)
    
    # 調査結果
    codebase_map: CodebaseMap = field(default_factory=CodebaseMap)
    evidence: List[Evidence] = field(default_factory=list)
    
    # 計画
    change_plan: ChangePlan = field(default_factory=ChangePlan)
    
    # 実行
    execution_trace: List[ExecutionTrace] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)
    
    # 失敗記録（self_healing.pyで詳細定義）
    failures: List[Dict[str, Any]] = field(default_factory=list)
    
    # v4.2.4追加: フェーズ結果（orchestrator.pyが記録）
    phase_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def save(self, path: Path) -> None:
        """JSONファイルに保存"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
    
    @classmethod
    def load(cls, path: Path) -> "RunContext":
        """JSONファイルから読込"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            run_id=data["run_id"],
            created_at=data.get("created_at", ""),
            task_contract=TaskContract(**data.get("task_contract", {})),
            codebase_map=CodebaseMap(**data.get("codebase_map", {})),
            evidence=[Evidence(**e) for e in data.get("evidence", [])],
            change_plan=ChangePlan(
                targets=[ChangeTarget(**t) for t in data.get("change_plan", {}).get("targets", [])],
                test_strategy=data.get("change_plan", {}).get("test_strategy", ""),
                risk_controls=data.get("change_plan", {}).get("risk_controls", []),
                rollback_steps=data.get("change_plan", {}).get("rollback_steps", [])
            ),
            execution_trace=[ExecutionTrace(**t) for t in data.get("execution_trace", [])],
            metrics=Metrics(**data.get("metrics", {})),
            failures=data.get("failures", []),
            phase_results=data.get("phase_results", {})
        )
    
    def append_evidence(self, ev: Evidence) -> None:
        """証拠を追加"""
        self.evidence.append(ev)
    
    def append_trace(self, trace: ExecutionTrace) -> None:
        """実行トレースを追加"""
        self.execution_trace.append(trace)
    
    def append_failure(self, failure: Dict[str, Any]) -> None:
        """失敗記録を追加"""
        self.failures.append(failure)
