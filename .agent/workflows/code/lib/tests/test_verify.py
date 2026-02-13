from __future__ import annotations

import sys
import unittest
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from lib.verify import ACVerifier, GateEvaluator, GateStatus, GatePhase


class TestACVerifier(unittest.TestCase):
    """ACVerifierのテスト（index境界）"""
    
    def test_verify_valid_index(self):
        """有効なindex（1-based）で正常に取得"""
        criteria = ["AC1: 条件1", "AC2: 条件2", "AC3: 条件3"]
        verifier = ACVerifier(criteria)
        
        verifier.verify("1", True, "evidence1")
        verifier.verify("2", False, "evidence2")
        
        self.assertEqual(len(verifier.results), 2)
        self.assertEqual(verifier.results[0].description, "AC1: 条件1")
        self.assertEqual(verifier.results[1].description, "AC2: 条件2")
    
    def test_verify_index_out_of_range_graceful(self):
        """範囲外indexでもエラーにならない（v4.2.4）"""
        criteria = ["AC1: 条件1"]
        verifier = ACVerifier(criteria)
        
        # index 10 は範囲外だが、クラッシュしない
        verifier.verify("10", True, "evidence")
        
        self.assertEqual(len(verifier.results), 1)
        self.assertIn("out of range", verifier.results[0].description)
        self.assertIn("10", verifier.results[0].description)
    
    def test_verify_zero_index_out_of_range(self):
        """index 0 は範囲外として扱われる（1-based）"""
        criteria = ["AC1"]
        verifier = ACVerifier(criteria)
        
        verifier.verify("0", True, "")
        
        # 0 - 1 = -1 なので範囲外
        self.assertIn("out of range", verifier.results[0].description)
    
    def test_verify_non_digit_uses_as_description(self):
        """非数字のac_idはdescriptionとして使用"""
        criteria = ["AC1"]
        verifier = ACVerifier(criteria)
        
        verifier.verify("custom_condition", True, "")
        
        self.assertEqual(verifier.results[0].description, "custom_condition")


class TestGateEvaluator(unittest.TestCase):
    """GateEvaluatorのテスト"""
    
    def test_evaluate_with_string_phase(self):
        """文字列phaseでの後方互換（plan/implement/verify）"""
        evaluator = GateEvaluator()
        
        result = evaluator.evaluate("plan", {
            "scope_defined": True,
            "ac_defined": True,
            "evidence_linked": True,
        })
        
        self.assertEqual(result.status, GateStatus.PASSED)
    
    def test_evaluate_unknown_phase_returns_skipped(self):
        """未知のphaseはSKIPPED"""
        evaluator = GateEvaluator()
        
        result = evaluator.evaluate("unknown_phase", {})
        
        self.assertEqual(result.status, GateStatus.SKIPPED)
        self.assertIn("Unknown phase", result.details)


if __name__ == "__main__":
    unittest.main()
