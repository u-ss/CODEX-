from __future__ import annotations

import sys
import unittest
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from lib.context import RunContext  # noqa: E402
from lib.orchestrator import Orchestrator, Phase, PhaseResult  # noqa: E402


def _ok_handler(phase: Phase):
    def _inner(_ctx: RunContext) -> PhaseResult:
        return PhaseResult(phase=phase, success=True, output=f"{phase.value}:ok")

    return _inner


class TestOrchestrator(unittest.TestCase):
    def test_run_all_uses_7_phase_order(self):
        ctx = RunContext(run_id="t1")
        orch = Orchestrator(ctx)

        ordered = [
            Phase.RESEARCH,
            Phase.PLAN,
            Phase.TEST,
            Phase.CODE,
            Phase.DEBUG,
            Phase.VERIFY,
            Phase.DOCUMENT,
        ]
        for p in ordered:
            orch.register_handler(p, _ok_handler(p))

        results = orch.run_all()
        self.assertEqual(list(results.keys()), ordered)
        self.assertTrue(all(r.success for r in results.values()))
        self.assertEqual(orch.current_phase, Phase.DOCUMENT)

    def test_missing_handler_stops_at_first_failure(self):
        ctx = RunContext(run_id="t2")
        orch = Orchestrator(ctx)
        orch.register_handler(Phase.RESEARCH, _ok_handler(Phase.RESEARCH))
        orch.register_handler(Phase.PLAN, _ok_handler(Phase.PLAN))
        # TEST handler intentionally missing

        results = orch.run_all()
        self.assertIn(Phase.TEST, results)
        self.assertFalse(results[Phase.TEST].success)
        self.assertNotIn(Phase.CODE, results)

    def test_evaluate_gate_maps_code_to_implement_gate(self):
        ctx = RunContext(run_id="t3")
        orch = Orchestrator(ctx)
        passed = orch.evaluate_gate(
            Phase.CODE,
            {
                "code_valid": True,
                "lint_passed": True,
                "diff_tests_passed": True,
            },
        )
        self.assertTrue(passed)


if __name__ == "__main__":
    unittest.main()

