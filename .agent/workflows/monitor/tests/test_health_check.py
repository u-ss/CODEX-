from __future__ import annotations

import json
from pathlib import Path
import importlib.util
import sys


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "health_check.py"
    spec = importlib.util.spec_from_file_location("monitor_health_check", str(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_health_checks_schema():
    module = _load_module()
    report = module.run_health_checks(repo_root=module._repo_root(), run_pytest=False)
    assert "checks" in report
    assert "summary" in report
    assert isinstance(report["checks"], list)
    assert set(report["summary"].keys()) == {"pass", "fail", "skip"}
    for check in report["checks"]:
        assert set(check.keys()) == {"name", "status", "latency_ms", "details"}
        assert check["status"] in {"PASS", "FAIL", "SKIP"}


def test_save_report_creates_json():
    module = _load_module()
    report = module.run_health_checks(repo_root=module._repo_root(), run_pytest=False)
    report_path = module._save_report(repo_root=module._repo_root(), report=report)
    assert report_path.exists()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert "summary" in loaded
