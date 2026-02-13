# /check エージェント - dangerous_autofix ルール
"""危険な自動修正操作の検出"""

from typing import List, Dict, Any
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.finding import Finding, Location, Severity


# 危険操作の定義
DANGEROUS_OPS = {"delete", "move", "rename", "remove"}

# 閾値
THRESHOLD_LINES = 50   # 変更行数
THRESHOLD_FILES = 5    # 変更ファイル数


def detect_dangerous_autofix(
    proposals: List[Dict],
    allow_roots: List[str],
) -> List[Finding]:
    """危険な自動修正提案を検出
    
    Args:
        proposals: 修正提案リスト
        allow_roots: 許可されたルートパスリスト
    
    Returns:
        検出されたFindingリスト
    """
    findings = []
    
    for p in proposals:
        summary = _summarize_proposal(p)
        
        # スコープ外パスの検出
        out_of_scope = [
            path for path in summary["touched_paths"]
            if not _is_under_any(path, allow_roots)
        ]
        
        # 危険操作の検出
        risky_ops = any(op in DANGEROUS_OPS for op in summary["ops"])
        
        # 大規模変更の検出
        big_change = (
            summary["changed_lines"] > THRESHOLD_LINES or
            summary["changed_files"] > THRESHOLD_FILES
        )
        
        if out_of_scope or risky_ops or big_change:
            risk_score = 0
            risk_score += 3 if out_of_scope else 0
            risk_score += 2 if risky_ops else 0
            risk_score += 1 if big_change else 0
            
            severity = Severity.HIGH if risk_score >= 3 else Severity.MEDIUM
            
            findings.append(Finding(
                rule_id="dangerous_autofix",
                severity=severity,
                location=Location(file=p.get("target_file", "unknown")),
                evidence={
                    "patch_summary": summary,
                    "out_of_scope_paths": out_of_scope,
                    "risk_score": risk_score,
                    "proposal_id": p.get("id", "unknown")
                },
                message=f"危険な修正操作を検出（リスクスコア: {risk_score}）",
                suggestion="手動確認または分割提案を検討",
                autofix_allowed=False
            ))
    
    return findings


def _summarize_proposal(proposal: Dict) -> Dict:
    """提案の要約を生成"""
    return {
        "changed_files": proposal.get("changed_files", 0),
        "changed_lines": proposal.get("changed_lines", 0),
        "ops": proposal.get("ops", []),
        "touched_paths": proposal.get("touched_paths", [])
    }


def _is_under_any(path: str, roots: List[str]) -> bool:
    """パスが許可ルートの配下にあるか"""
    path_obj = Path(path).resolve()
    for root in roots:
        root_obj = Path(root).resolve()
        try:
            path_obj.relative_to(root_obj)
            return True
        except ValueError:
            continue
    return False
