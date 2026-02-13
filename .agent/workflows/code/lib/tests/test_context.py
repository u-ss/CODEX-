from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from lib.context import (
    RunContext,
    TaskContract,
    CodebaseMap,
    Evidence,
    ChangeTarget,
    ChangePlan,
    ExecutionTrace,
    Metrics,
)


class TestRunContextSaveLoad(unittest.TestCase):
    """RunContextのsave/load roundtripテスト"""
    
    def test_save_and_load_roundtrip(self):
        """saveしたものをloadして同じデータが復元される"""
        ctx = RunContext(run_id="roundtrip_test")
        ctx.task_contract = TaskContract(
            goal="テスト目標",
            acceptance_criteria=["AC1", "AC2"],
            non_goals=["NG1"],
            scope=["lib/"],
            constraints=["Python 3.12"]
        )
        ctx.codebase_map = CodebaseMap(
            entrypoints=["main.py"],
            key_modules=["lib/core.py"],
            run_commands={"dev": "python main.py"},
            test_commands={"test": "pytest"},
        )
        ctx.evidence = [
            Evidence(evidence_id="ev1", type="grep", path="lib/core.py", line_range="10-20")
        ]
        ctx.change_plan = ChangePlan(
            targets=[ChangeTarget(file="lib/core.py", intent="modify", steps=["step1"])],
            test_strategy="unit test",
            risk_controls=["backup"],
            rollback_steps=["revert"]
        )
        ctx.execution_trace = [
            ExecutionTrace(phase="test", command="pytest", exit_code=0, 
                          stdout_hash="abc", stderr_hash="def", duration_ms=100)
        ]
        ctx.metrics = Metrics(test_pass_rate=1.0, coverage=0.85, lint_errors=0)
        ctx.failures = [{"phase": "test", "reason": "transient"}]
        ctx.phase_results = {"test": {"success": True, "duration_ms": 100}}
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "context.json"
            ctx.save(path)
            
            # ファイルが作成されたことを確認
            self.assertTrue(path.exists())
            
            # loadして復元
            loaded = RunContext.load(path)
            
            # 主要フィールドを検証
            self.assertEqual(loaded.run_id, "roundtrip_test")
            self.assertEqual(loaded.task_contract.goal, "テスト目標")
            self.assertEqual(len(loaded.task_contract.acceptance_criteria), 2)
            self.assertEqual(len(loaded.evidence), 1)
            self.assertEqual(loaded.evidence[0].evidence_id, "ev1")
            self.assertEqual(len(loaded.change_plan.targets), 1)
            self.assertEqual(loaded.change_plan.targets[0].file, "lib/core.py")
            self.assertEqual(loaded.metrics.test_pass_rate, 1.0)
            self.assertEqual(len(loaded.failures), 1)
            self.assertEqual(loaded.phase_results["test"]["success"], True)
    
    def test_load_with_missing_optional_fields(self):
        """オプションフィールドが欠落しても読み込める"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "minimal.json"
            minimal_data = {
                "run_id": "minimal",
                "created_at": "2024-01-01T00:00:00",
                # 他のフィールドは省略
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(minimal_data, f)
            
            loaded = RunContext.load(path)
            
            self.assertEqual(loaded.run_id, "minimal")
            self.assertEqual(loaded.task_contract.goal, "")
            self.assertEqual(len(loaded.evidence), 0)
            self.assertEqual(loaded.phase_results, {})
    
    def test_phase_results_field_exists(self):
        """v4.2.4で追加されたphase_resultsフィールドが存在する"""
        ctx = RunContext(run_id="phase_results_test")
        
        # デフォルトで空のdict
        self.assertIsInstance(ctx.phase_results, dict)
        self.assertEqual(len(ctx.phase_results), 0)
        
        # 追加可能
        ctx.phase_results["plan"] = {"success": True}
        self.assertEqual(ctx.phase_results["plan"]["success"], True)


if __name__ == "__main__":
    unittest.main()
