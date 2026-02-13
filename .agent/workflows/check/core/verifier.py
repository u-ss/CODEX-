# /check エージェント - VERIFYステップ
"""修正後の検証ロジック"""

from typing import List, Dict, Optional, Set
from dataclasses import dataclass
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.finding import Finding, VerifyResult, Severity


def verify_after_execute(
    pre_findings: List[Finding],
    post_findings: List[Finding],
    target_finding_ids: Set[str],
    applied_patches: Dict
) -> VerifyResult:
    """EXECUTE後の検証
    
    Args:
        pre_findings: 修正前のfindings
        post_findings: 修正後のfindings
        target_finding_ids: 修正対象だったfindingのID
        applied_patches: 適用されたパッチ情報
    
    Returns:
        検証結果
    """
    # 1) 解消確認（修正対象が減ったか）
    resolved = diff_resolved(pre_findings, post_findings, target_finding_ids)
    
    # 2) 退行確認（重大が増えていないか）
    regressed = diff_regressed(pre_findings, post_findings)
    
    # 3) パースエラーチェック
    parse_errors = [f for f in post_findings if f.rule_id == "invalid_yaml_json"]
    if parse_errors:
        return VerifyResult(
            ok=False,
            reason="parse_error",
            resolved=resolved,
            regressed=regressed
        )
    
    # 4) スコープ違反チェック
    if applied_patches.get("has_out_of_scope_changes"):
        return VerifyResult(
            ok=False,
            reason="scope_violation",
            resolved=resolved,
            regressed=regressed
        )
    
    # 5) 退行があれば失敗
    if len(regressed) > 0:
        return VerifyResult(
            ok=False,
            reason="regression",
            resolved=resolved,
            regressed=regressed
        )
    
    # 成功
    return VerifyResult(
        ok=True,
        resolved=resolved,
        regressed=regressed,
        post_digest=_create_digest(post_findings)
    )


def diff_resolved(
    pre: List[Finding],
    post: List[Finding],
    target_ids: Set[str]
) -> List[str]:
    """解消されたfindingを検出"""
    pre_rules = {_finding_key(f) for f in pre}
    post_rules = {_finding_key(f) for f in post}
    
    resolved = pre_rules - post_rules
    return list(resolved)


def diff_regressed(
    pre: List[Finding],
    post: List[Finding],
    severity_min: Severity = Severity.HIGH
) -> List[Finding]:
    """退行（新規または悪化）したfindingを検出"""
    pre_keys = {_finding_key(f) for f in pre}
    
    regressed = []
    for f in post:
        # 新規の重大finding
        if _finding_key(f) not in pre_keys:
            if _severity_gte(f.severity, severity_min):
                regressed.append(f)
    
    return regressed


def _finding_key(f: Finding) -> str:
    """findingの一意キー生成"""
    return f"{f.rule_id}:{f.location.file}:{f.location.line}"


def _severity_gte(s1: Severity, s2: Severity) -> bool:
    """s1 >= s2 か判定"""
    order = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}
    return order.get(s1, 0) >= order.get(s2, 0)


def _create_digest(findings: List[Finding]) -> Dict:
    """findings の digest を作成"""
    by_severity = {"high": 0, "medium": 0, "low": 0}
    by_rule = {}
    
    for f in findings:
        by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1
        by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
    
    return {
        "total": len(findings),
        "by_severity": by_severity,
        "by_rule": by_rule
    }
