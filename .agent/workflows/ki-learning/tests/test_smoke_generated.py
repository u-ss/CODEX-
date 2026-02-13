from __future__ import annotations

from pathlib import Path

def test_workflow_has_docs():
    base = Path(__file__).resolve().parents[1]
    assert (base / 'SKILL.md').exists()
    assert (base / 'WORKFLOW.md').exists()
