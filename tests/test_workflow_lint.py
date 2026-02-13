from __future__ import annotations

import sys
from pathlib import Path

import pytest


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import workflow_lint  # noqa: E402


def test_read_utf8_checked_detects_decode_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_bytes(b"header:\x95broken")

    _, findings = workflow_lint.read_utf8_checked(path)

    assert findings
    assert "utf-8 decode failed" in findings[0]


def test_lint_workflow_dir_detects_replacement_character(tmp_path: Path) -> None:
    wf = tmp_path / "demo"
    wf.mkdir()
    (wf / "SKILL.md").write_text(
        "---\nname: Demo v1.0.0\n---\n# Demo v1.0.0\n\ufffd\n",
        encoding="utf-8",
    )
    (wf / "WORKFLOW.md").write_text(
        "---\nname: Demo v1.0.0\n---\n# Demo v1.0.0\nSKILL.md\n",
        encoding="utf-8",
    )

    findings = workflow_lint.lint_workflow_dir(wf)

    assert any("contains replacement character U+FFFD" in f for f in findings)


def test_lint_workflow_dir_warns_when_skill_reference_missing(tmp_path: Path) -> None:
    wf = tmp_path / "demo"
    wf.mkdir()
    (wf / "SKILL.md").write_text(
        "---\nname: Demo v1.0.0\n---\n# Demo v1.0.0\n",
        encoding="utf-8",
    )
    (wf / "WORKFLOW.md").write_text(
        "---\nname: Demo v1.0.0\n---\n# Demo v1.0.0\nThis workflow has no pre-read note.\n",
        encoding="utf-8",
    )

    findings = workflow_lint.lint_workflow_dir(wf)

    assert any("should mention SKILL.md pre-read requirement" in f for f in findings)


def test_main_returns_zero_when_findings_are_warn_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf_root = tmp_path / "workflows"
    (wf_root / "demo").mkdir(parents=True)

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)
    monkeypatch.setattr(workflow_lint, "lint_workflow_dir", lambda _: ["[WARN] demo: warning only"])
    monkeypatch.setattr(workflow_lint, "lint_ops_migration_note", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_architecture_doc", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_agent_readmes", lambda: ([], {"demo"}))
    monkeypatch.setattr(workflow_lint, "lint_unreferenced_workflows", lambda _: [])
    monkeypatch.setattr(workflow_lint, "lint_workflow_logging_coverage", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_agent_script_logging_coverage", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_cross_ref_script_paths", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_cross_ref_version", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_slash_commands", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_disc_coverage", lambda: [])

    assert workflow_lint.main([]) == 0


def test_main_returns_one_when_error_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf_root = tmp_path / "workflows"
    (wf_root / "demo").mkdir(parents=True)

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)
    monkeypatch.setattr(workflow_lint, "lint_workflow_dir", lambda _: ["[ERROR] demo: broken"])
    monkeypatch.setattr(workflow_lint, "lint_ops_migration_note", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_architecture_doc", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_agent_readmes", lambda: ([], {"demo"}))
    monkeypatch.setattr(workflow_lint, "lint_unreferenced_workflows", lambda _: [])
    monkeypatch.setattr(workflow_lint, "lint_workflow_logging_coverage", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_agent_script_logging_coverage", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_cross_ref_script_paths", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_cross_ref_version", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_slash_commands", lambda: [])
    monkeypatch.setattr(workflow_lint, "lint_disc_coverage", lambda: [])

    assert workflow_lint.main([]) == 1


def test_lint_unreferenced_workflows_emits_advisory_for_unlinked_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf_root = tmp_path / "workflows"
    (wf_root / "linked").mkdir(parents=True)
    (wf_root / "unlinked").mkdir(parents=True)
    (wf_root / "shared").mkdir(parents=True)

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)

    findings = workflow_lint.lint_unreferenced_workflows({"linked"})

    assert len(findings) == 1
    assert "unlinked" in findings[0]
    assert "not referenced" in findings[0]


# === v1.2.0 新ルールテスト ===


def test_xref_001_detects_missing_script_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WL-XREF-001: SKILL.mdに記載のスクリプトパスが存在しない場合ERRORを検出する。"""
    wf_root = tmp_path / ".agent" / "workflows"
    wf_dir = wf_root / "demo"
    wf_dir.mkdir(parents=True)
    (wf_dir / "SKILL.md").write_text(
        "# Demo v1.0.0\n\n参照: `scripts/nonexistent.py`\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)
    monkeypatch.setattr(workflow_lint, "ROOT", tmp_path)

    findings = workflow_lint.lint_cross_ref_script_paths()

    assert any("WL-XREF-001" in f and "nonexistent.py" in f for f in findings)


def test_xref_001_no_error_when_script_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WL-XREF-001: スクリプトが存在する場合はERRORを出さない。"""
    wf_root = tmp_path / ".agent" / "workflows"
    wf_dir = wf_root / "demo"
    wf_dir.mkdir(parents=True)
    scripts_dir = wf_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "exists.py").write_text("# ok", encoding="utf-8")
    (wf_dir / "SKILL.md").write_text(
        "# Demo v1.0.0\n\n参照: `scripts/exists.py`\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)
    monkeypatch.setattr(workflow_lint, "ROOT", tmp_path)

    findings = workflow_lint.lint_cross_ref_script_paths()

    assert not any("WL-XREF-001" in f for f in findings)


def test_xref_002_detects_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WL-XREF-002: __version__とSKILL H1が不一致の場合CAUTIONを検出する。"""
    wf_root = tmp_path / ".agent" / "workflows"
    wf_dir = wf_root / "demo"
    wf_dir.mkdir(parents=True)
    script = tmp_path / "tools" / "demo.py"
    script.parent.mkdir(parents=True)
    script.write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (wf_dir / "SKILL.md").write_text(
        "# Demo v2.0.0\n\n正本: `tools/demo.py`\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)
    monkeypatch.setattr(workflow_lint, "ROOT", tmp_path)

    findings = workflow_lint.lint_cross_ref_version()

    assert any("WL-XREF-002" in f for f in findings)


def test_cmd_001_detects_missing_slash_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WL-CMD-001: 存在しないスラッシュコマンド参照をCAUTIONで検出する。"""
    wf_root = tmp_path / "workflows"
    wf_dir = wf_root / "demo"
    wf_dir.mkdir(parents=True)
    (wf_dir / "SKILL.md").write_text(
        "# Demo v1.0.0\n\n`/nonexistent_workflow` を参照\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)

    findings = workflow_lint.lint_slash_commands()

    assert any("WL-CMD-001" in f and "/nonexistent_workflow" in f for f in findings)


def test_cmd_001_excludes_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WL-CMD-001: /xxxなどのプレースホルダは検出しない。"""
    wf_root = tmp_path / "workflows"
    wf_dir = wf_root / "demo"
    wf_dir.mkdir(parents=True)
    (wf_dir / "SKILL.md").write_text(
        "# Demo v1.0.0\n\n`/xxx` はプレースホルダ\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)

    findings = workflow_lint.lint_slash_commands()

    assert not any("/xxx" in f for f in findings)


def test_disc_001_detects_undocumented_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WL-DISC-001: workflowsに存在するがarchitecture.mdに未記載の場合ADVISORYを検出する。"""
    wf_root = tmp_path / ".agent" / "workflows"
    (wf_root / "documented").mkdir(parents=True)
    (wf_root / "undocumented").mkdir(parents=True)
    arch_dir = tmp_path / "docs"
    arch_dir.mkdir(parents=True)
    (arch_dir / "architecture.md").write_text(
        "documented workflow text\n", encoding="utf-8",
    )

    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)
    monkeypatch.setattr(workflow_lint, "ROOT", tmp_path)
    monkeypatch.setattr(workflow_lint, "WIP_IGNORE_WORKFLOWS", frozenset())

    findings = workflow_lint.lint_disc_coverage()

    assert any("WL-DISC-001" in f and "undocumented" in f for f in findings)
    assert not any("documented" in f and "WL-DISC-001" in f and "undocumented" not in f for f in findings)


def test_readme_template_detects_missing_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WL-RMD-002~005: READMEテンプレート不足をCAUTIONで検出する。"""
    wf_root = tmp_path / ".agent" / "workflows"
    (wf_root / "demo").mkdir(parents=True)
    agents_root = tmp_path / "エージェント"
    agent_dir = agents_root / "デモエージェント"
    agent_dir.mkdir(parents=True)
    (agent_dir / "README.md").write_text(
        "# デモエージェント\n\n- `.agent/workflows/demo/`\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow_lint, "ROOT", tmp_path)
    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)

    findings, referenced_workflows = workflow_lint.lint_agent_readmes()

    assert "demo" in referenced_workflows
    assert any("WL-RMD-002" in f for f in findings)
    assert any("WL-RMD-003" in f for f in findings)
    assert any("WL-RMD-004" in f for f in findings)
    assert any("WL-RMD-005" in f for f in findings)


def test_readme_template_accepts_recommended_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WL-RMD-001~005: 推奨README形式はテンプレート警告なし。"""
    wf_root = tmp_path / ".agent" / "workflows"
    (wf_root / "demo").mkdir(parents=True)
    agents_root = tmp_path / "エージェント"
    agent_dir = agents_root / "デモエージェント"
    agent_dir.mkdir(parents=True)
    (agent_dir / "README.md").write_text(
        "\n".join(
            [
                "# デモエージェント",
                "",
                "## ワークフロー定義（正）",
                "- `.agent/workflows/demo/`",
                "",
                "## 使い方（最短）",
                "```powershell",
                "python .agent/workflows/demo/scripts/demo.py",
                "```",
                "",
                "## 入出力",
                "- 入力: task",
                "- 出力: result",
                "",
                "## 注意事項",
                "- 破壊的操作は禁止",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(workflow_lint, "ROOT", tmp_path)
    monkeypatch.setattr(workflow_lint, "WF_ROOT", wf_root)

    findings, _ = workflow_lint.lint_agent_readmes()

    assert not any("WL-RMD-" in f for f in findings)
