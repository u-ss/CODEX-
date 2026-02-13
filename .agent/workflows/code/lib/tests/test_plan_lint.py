from __future__ import annotations

import sys
import unittest
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from lib.context import RunContext, TaskContract, ChangePlan, ChangeTarget, Evidence
from lib.plan_lint import lint_plan, require_evidence_for_targets, DEFAULT_RULES


class TestPlanLint(unittest.TestCase):
    """Plan Lintモジュールのテスト"""
    
    def test_evidence_required_missing_shows_file_names(self):
        """Evidence不足時にファイル名が詳細表示される"""
        ctx = RunContext(run_id="test_evidence")
        ctx.task_contract = TaskContract(
            scope=["lib/"],
            acceptance_criteria=["AC1"],
        )
        ctx.change_plan = ChangePlan(
            targets=[
                ChangeTarget(file="lib/foo.py", intent="modify"),
                ChangeTarget(file="lib/bar.py", intent="add"),
            ],
            test_strategy="unit test"
        )
        # Evidenceが1つだけ（bar.pyがmissing）
        ctx.evidence = [Evidence(evidence_id="ev1", type="grep", path="lib/foo.py")]
        
        result = lint_plan(ctx)
        
        # 失敗すべき
        self.assertFalse(result.passed)
        self.assertIn("evidence_required", result.missing)
        
        # エラーにファイル名が含まれる（v4.2.4の詳細レポート）
        error_text = " ".join(result.errors)
        self.assertIn("lib/bar.py", error_text)
    
    def test_evidence_required_all_present_passes(self):
        """全ターゲットにEvidenceがある場合はパス"""
        ctx = RunContext(run_id="test_evidence_ok")
        ctx.task_contract = TaskContract(
            scope=["lib/"],
            acceptance_criteria=["AC1"],
        )
        ctx.change_plan = ChangePlan(
            targets=[
                ChangeTarget(file="lib/foo.py", intent="modify"),
            ],
            test_strategy="unit test"
        )
        ctx.evidence = [Evidence(evidence_id="ev1", type="grep", path="lib/foo.py")]
        
        result = lint_plan(ctx)
        
        self.assertNotIn("evidence_required", result.missing)
    
    def test_no_targets_skips_evidence_check(self):
        """ターゲットがない場合はevidence_requiredをスキップ"""
        ctx = RunContext(run_id="test_no_targets")
        ctx.task_contract = TaskContract(
            scope=["lib/"],
            acceptance_criteria=["AC1"],
        )
        ctx.change_plan = ChangePlan(
            targets=[],  # 空
            test_strategy="none"
        )
        
        result = lint_plan(ctx)
        
        # evidence_requiredは失敗しない
        self.assertNotIn("evidence_required", result.missing)


if __name__ == "__main__":
    unittest.main()
