# -*- coding: utf-8 -*-
"""
Implementation Agent v4.2.3 - Plan Lint Module
必須セクション検証 + Evidence強制統合

v4.2.1 変更点:
- evidence_required ルール追加
- lint_plan() が Evidence不足を error として報告
- require_evidence_for_targets() を内部で統合
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from .context import RunContext, ChangePlan


@dataclass
class LintRule:
    """Lintルール定義"""
    rule_id: str
    description: str
    severity: str = "error"  # error/warning
    check_fn: Optional[str] = None  # 検証関数名


@dataclass
class LintResult:
    """Lint結果"""
    passed: bool
    missing: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# デフォルトルール
DEFAULT_RULES = [
    LintRule("scope", "対象範囲（Scope）が定義されているか", "error"),
    LintRule("acceptance", "受け入れ条件（AC）が1つ以上あるか", "error"),
    LintRule("non_goals", "非目標（Non-goals）が明記されているか", "warning"),
    LintRule("targets", "変更対象（Targets）が1つ以上あるか", "error"),
    LintRule("test_strategy", "テスト戦略が定義されているか", "error"),
    LintRule("risks", "リスク対策が定義されているか", "warning"),
    LintRule("rollback", "ロールバック手順があるか", "warning"),
    # v4.2.1追加: Evidence必須
    LintRule("evidence_required", "変更対象にEvidenceが紐づいているか", "error"),
]


def lint_plan(ctx: RunContext, rules: List[LintRule] = None) -> LintResult:
    """
    Planを検証
    
    Args:
        ctx: RunContext
        rules: カスタムルール（省略時はDEFAULT_RULES）
    
    Returns:
        LintResult
    """
    if rules is None:
        rules = DEFAULT_RULES
    
    missing = []
    warnings = []
    errors = []
    
    tc = ctx.task_contract
    cp = ctx.change_plan
    
    for rule in rules:
        passed = True
        
        if rule.rule_id == "scope":
            passed = len(tc.scope) > 0
        elif rule.rule_id == "acceptance":
            passed = len(tc.acceptance_criteria) > 0
        elif rule.rule_id == "non_goals":
            passed = len(tc.non_goals) > 0
        elif rule.rule_id == "targets":
            passed = len(cp.targets) > 0
        elif rule.rule_id == "test_strategy":
            passed = bool(cp.test_strategy)
        elif rule.rule_id == "risks":
            passed = len(cp.risk_controls) > 0
        elif rule.rule_id == "rollback":
            passed = len(cp.rollback_steps) > 0
        elif rule.rule_id == "evidence_required":
            # v4.2.4: Evidence強制統合 + 詳細レポート
            # 変更対象が1つ以上ある場合、全対象にEvidenceが必要
            if len(cp.targets) > 0:
                evidence_check = require_evidence_for_targets(ctx)
                missing_targets = [t for t, has_ev in evidence_check.items() if not has_ev]
                if missing_targets:
                    passed = False
                    missing.append(rule.rule_id)
                    # v4.2.4: 不足ファイル名を詳細表示
                    errors.append(f"[{rule.rule_id}] Missing evidence for: {', '.join(missing_targets)}")
                    continue  # 既にerrorsに追加したのでスキップ
            else:
                passed = True  # 対象がなければスキップ
        
        if not passed:
            missing.append(rule.rule_id)
            msg = f"[{rule.rule_id}] {rule.description}"
            if rule.severity == "error":
                errors.append(msg)
            else:
                warnings.append(msg)
    
    return LintResult(
        passed=len(errors) == 0,
        missing=missing,
        warnings=warnings,
        errors=errors
    )


def require_evidence(ctx: RunContext, min_count: int = 1) -> bool:
    """
    Evidenceが十分にあるか確認
    
    Args:
        ctx: RunContext
        min_count: 最小必要数
    
    Returns:
        True if evidence >= min_count
    """
    return len(ctx.evidence) >= min_count


def require_evidence_for_targets(ctx: RunContext) -> Dict[str, bool]:
    """
    各変更対象にEvidenceがあるか確認
    
    Returns:
        {target_file: has_evidence}
    """
    evidence_paths = {e.path for e in ctx.evidence}
    result = {}
    
    for target in ctx.change_plan.targets:
        result[target.file] = target.file in evidence_paths
    
    return result


def format_lint_report(result: LintResult) -> str:
    """Lint結果をフォーマット"""
    lines = []
    lines.append("=" * 40)
    lines.append("Plan Lint Result")
    lines.append("=" * 40)
    
    if result.passed:
        lines.append("✅ PASSED")
    else:
        lines.append("❌ FAILED")
    
    if result.errors:
        lines.append("\n🚨 Errors:")
        for e in result.errors:
            lines.append(f"  - {e}")
    
    if result.warnings:
        lines.append("\n⚠️ Warnings:")
        for w in result.warnings:
            lines.append(f"  - {w}")
    
    if result.missing:
        lines.append(f"\nMissing: {', '.join(result.missing)}")
    
    return "\n".join(lines)
