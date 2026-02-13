#!/usr/bin/env python3
"""workflow_lint.py — ワークスペースドキュメント整合性チェッカー"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

__version__ = "1.3.0"

ROOT = Path(__file__).resolve().parents[1]
WF_ROOT = ROOT / ".agent" / "workflows"
INLINE_VERSION_RE = re.compile(r"v(\d+\.\d+\.\d+)\s*追加")
VERSION_RE = re.compile(r"v\d+\.\d+\.\d+")
WF_REF_RE = re.compile(r"\.agent/workflows/([A-Za-z0-9_-]+)/")
FILE_URI_RE = re.compile(r"\bfile:///", re.IGNORECASE)
MAIN_RE = re.compile(r"__name__\s*==\s*[\"']__main__[\"']")
LOGGING_MARKER_RE = re.compile(r"(logged_main|run_logged_main|WorkflowLogger)")
# v1.2.0: クロスチェック用正規表現
# 抽出仕様: バッククォート内の相対パスで .py/.ps1/.sh 拡張子を持つもの
SCRIPT_PATH_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|ps1|sh))`")
# 抽出仕様: `def 関数名(` 形式の関数定義
PYTHON_FUNC_RE = re.compile(r"^\s*def\s+(\w+)\s*\(", re.MULTILINE)
# 抽出仕様: __version__ = "X.Y.Z" 形式
PYTHON_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([\d.]+)["\']', re.MULTILINE)
# 抽出仕様: /xxx 形式のスラッシュコマンド（バッククォート内 or 行頭）
# 除外: /dev, /tmp, /v1, /usr, /etc, /bin, /home, http://, file:/// 等
SLASH_CMD_RE = re.compile(r'(?:^|[\s`])(/[a-z][a-z0-9_-]+)(?:[`\s.,\)]|$)', re.MULTILINE)

CONSOLIDATED_TO_OPS = frozenset({"deploy", "schedule", "data-clean", "doc-sync", "report"})
# Work-in-progress entities to exclude from lint until promoted.
WIP_IGNORE_AGENTS = frozenset({"画像生成エージェント"})
WIP_IGNORE_WORKFLOWS = frozenset({"imagen"})
ERROR_PREFIX = "[ERROR]"
CAUTION_PREFIX = "[CAUTION]"
ADVISORY_PREFIX = "[ADVISORY]"
WARN_PREFIX = "[WARN]"  # legacy compatibility
SEVERITY_EXPLANATION = """Severity definition:
- ERROR: Blocking issue. Must be fixed before completion (missing required files, broken references, logging coverage failure, UTF-8 decode failure).
- CAUTION: Non-blocking, but should be fixed soon (process/compliance caution).
- ADVISORY: Informational recommendation (quality/maintenance hint).
- "WARN" is kept as a legacy alias only. New findings should use CAUTION or ADVISORY.
"""


def read_utf8_checked(path: Path) -> tuple[str, list[str]]:
    findings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        findings.append(
            f"[ERROR] {path.as_posix()}: utf-8 decode failed ({e})"
        )
        return "", findings

    replacement_count = text.count("\ufffd")
    if replacement_count:
        findings.append(
            f"[ERROR] {path.as_posix()}: contains replacement character U+FFFD x{replacement_count}"
        )
    return text, findings


def first_heading_from_text(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line.strip()
    return ""


def version_from_heading(heading: str) -> str:
    m = VERSION_RE.search(heading)
    return m.group(0) if m else ""


def _check_inline_version_contradiction(text: str, h1_version: str, path_label: str, file_label: str) -> list[str]:
    """WL-VER-001: H1バージョンと本文内「vX.Y.Z追加」の矛盾を検出"""
    findings: list[str] = []
    if not h1_version:
        return findings
    h1_tuple = tuple(int(x) for x in h1_version.lstrip("v").split("."))
    for m in INLINE_VERSION_RE.finditer(text):
        inline_v = m.group(1)
        inline_tuple = tuple(int(x) for x in inline_v.split("."))
        if inline_tuple > h1_tuple:
            findings.append(
                f"[ERROR] {path_label}: {file_label} H1={h1_version} but body mentions v{inline_v}追加 (WL-VER-001)"
            )
    return findings


def _check_template_compliance(text: str, path_label: str, file_label: str) -> list[str]:
    """WL-TPL-001: SKILL.mdに「役割境界」見出しがあるかチェック"""
    findings: list[str] = []
    if file_label == "SKILL" and "役割境界" not in text and "## 役割境界" not in text:
        findings.append(
            f"[CAUTION] {path_label}: SKILL.md missing '役割境界' heading (WL-TPL-001)"
        )
    return findings


def _check_role_boundary(text: str, path_label: str, file_label: str) -> list[str]:
    """WL-ROLE: SKILL.mdにコマンド例が多すぎないか、WORKFLOWに判定基準が多すぎないかチェック"""
    findings: list[str] = []
    # WL-ROLE-001: SKILL.mdに過剰なコマンド例
    if file_label == "SKILL":
        cmd_blocks = text.count("```powershell") + text.count("```bash") + text.count("```shell")
        if cmd_blocks >= 5:
            findings.append(
                f"[ADVISORY] {path_label}: SKILL.md contains {cmd_blocks} command blocks, consider moving to WORKFLOW.md (WL-ROLE-001)"
            )
    # WL-ROLE-002: WORKFLOW.mdに判定基準の詳細定義
    if file_label == "WORKFLOW":
        rule_tables = text.count("| Rule ID") + text.count("| WL-")
        if rule_tables >= 3:
            findings.append(
                f"[ADVISORY] {path_label}: WORKFLOW.md contains {rule_tables} rule tables, consider moving to SKILL.md (WL-ROLE-002)"
            )
    return findings


def _lint_sub_agents(wf_path: Path) -> list[str]:
    """子エージェント規約チェック (WL-SUB-001~004)"""
    findings: list[str] = []
    sub_agents_dir = wf_path / "sub_agents"
    if not sub_agents_dir.exists():
        return findings

    for sub_dir in sorted(p for p in sub_agents_dir.iterdir() if p.is_dir()):
        # WL-SUB-001: sub_agents/にSKILL.md/WORKFLOW.mdがあればERROR
        for forbidden in ("SKILL.md", "WORKFLOW.md"):
            if (sub_dir / forbidden).exists():
                findings.append(
                    f"[ERROR] {wf_path.name}/sub_agents/{sub_dir.name}: "
                    f"{forbidden} must be renamed to SPEC.md/GUIDE.md in sub_agents/ (WL-SUB-001)"
                )

        spec = sub_dir / "SPEC.md"
        guide = sub_dir / "GUIDE.md"

        # WL-SUB-002: SPEC.mdの自己参照チェック
        if spec.exists():
            spec_text, spec_findings = read_utf8_checked(spec)
            findings.extend(spec_findings)
            if "この SKILL.md は" in spec_text or "このSKILL.md" in spec_text:
                findings.append(
                    f"[ERROR] {wf_path.name}/sub_agents/{sub_dir.name}: "
                    f"SPEC.md self-reference uses 'SKILL.md' instead of 'SPEC.md' (WL-SUB-002)"
                )

        # WL-SUB-003: GUIDE.mdの事前読了参照チェック
        if guide.exists():
            guide_text, guide_findings = read_utf8_checked(guide)
            findings.extend(guide_findings)
            if "SKILL.md" in guide_text and "SPEC.md" not in guide_text:
                findings.append(
                    f"[ERROR] {wf_path.name}/sub_agents/{sub_dir.name}: "
                    f"GUIDE.md references 'SKILL.md' instead of 'SPEC.md' for pre-read (WL-SUB-003)"
                )
            # GUIDEに旧スラッシュコマンドが残存
            # コードブロックを除去して判定
            guide_cleaned = re.sub(r"```[\s\S]*?```", "", guide_text)
            # 行頭/スペース後の /word_word パターンのみ検出（パス中間の /xxx は除外）
            for legacy_m in re.finditer(r"(?:^|(?<=\s))`?(/[a-z][a-z0-9_-]+)`?", guide_cleaned, re.MULTILINE):
                cmd = legacy_m.group(1)
                # 短すぎるもの、APIエンドポイント風、出力パスを除外
                if len(cmd) <= 3:
                    continue
                if cmd in ("/v1", "/api", "/docs", "/tmp", "/bin", "/usr", "/etc"):
                    continue
                # アンダースコアを含むもの or ハイフンを含むもの = レガシー名の可能性
                if "_" not in cmd and "-" not in cmd:
                    continue
                findings.append(
                    f"[CAUTION] {wf_path.name}/sub_agents/{sub_dir.name}: "
                    f"GUIDE.md contains legacy slash command '{cmd}' (WL-SUB-003)"
                )
                break  # 1件だけ報告

    # WL-SUB-004: 親SKILL.mdの子エージェント参照パスが実在するか
    parent_skill = wf_path / "SKILL.md"
    if parent_skill.exists():
        parent_text, _ = read_utf8_checked(parent_skill)
        sub_ref_re = re.compile(r"sub_agents/([A-Za-z0-9_-]+)/(SPEC|GUIDE)\.md")
        for m in sub_ref_re.finditer(parent_text):
            ref_path = sub_agents_dir / m.group(1) / f"{m.group(2)}.md"
            if not ref_path.exists():
                findings.append(
                    f"[CAUTION] {wf_path.name}: parent SKILL.md references "
                    f"sub_agents/{m.group(1)}/{m.group(2)}.md but it does not exist (WL-SUB-004)"
                )

    return findings


def lint_workflow_dir(path: Path) -> list[str]:
    findings: list[str] = []
    skill = path / "SKILL.md"
    workflow = path / "WORKFLOW.md"

    # ユーティリティ/ベンダーディレクトリは除外
    if path.name in {"shared", "claude-skills"}:
        return findings

    if not skill.exists():
        findings.append(f"[ERROR] {path.name}: missing SKILL.md")
    if not workflow.exists():
        findings.append(f"[ERROR] {path.name}: missing WORKFLOW.md")

    if skill.exists() and workflow.exists():
        skill_text, skill_findings = read_utf8_checked(skill)
        workflow_text, workflow_findings = read_utf8_checked(workflow)
        findings.extend(skill_findings)
        findings.extend(workflow_findings)

        # WL-FILE-003: SKILL.md事前読了の記載
        if workflow_text and "SKILL.md" not in workflow_text:
            findings.append(
                f"[CAUTION] {path.name}: WORKFLOW.md should mention SKILL.md pre-read requirement (WL-FILE-003)"
            )

        skill_h = first_heading_from_text(skill_text)
        wf_h = first_heading_from_text(workflow_text)
        skill_v = version_from_heading(skill_h)
        wf_v = version_from_heading(wf_h)

        # WL-VER-002: SKILL/WORKFLOWバージョン不一致
        if skill_v and wf_v and skill_v != wf_v:
            findings.append(
                f"[ERROR] {path.name}: version mismatch SKILL={skill_v} WORKFLOW={wf_v} (WL-VER-002)"
            )
        if not skill_v:
            findings.append(f"[ADVISORY] {path.name}: SKILL heading has no semantic version")
        if not wf_v:
            findings.append(
                f"[ADVISORY] {path.name}: WORKFLOW heading has no semantic version"
            )

        # WL-VER-001: 本文内vX.Y.Z矛盾チェック
        findings.extend(_check_inline_version_contradiction(skill_text, skill_v, path.name, "SKILL"))
        findings.extend(_check_inline_version_contradiction(workflow_text, wf_v, path.name, "WORKFLOW"))

        # WL-TPL-001: テンプレート準拠チェック
        findings.extend(_check_template_compliance(skill_text, path.name, "SKILL"))

        # WL-ROLE: 役割境界チェック
        findings.extend(_check_role_boundary(skill_text, path.name, "SKILL"))
        findings.extend(_check_role_boundary(workflow_text, path.name, "WORKFLOW"))

    # 子エージェント規約チェック
    findings.extend(_lint_sub_agents(path))

    return findings


# === v1.2.0: リポジトリ全体クロスチェック ===

# 除外するスラッシュ: OSパス、URLスキーム、バージョン表記
_SLASH_EXCLUDE = frozenset({
    "/dev", "/tmp", "/usr", "/etc", "/bin", "/home", "/var",
    "/v1", "/v2", "/v3", "/v4", "/v5",
    "/json", "/api", "/docs", "/null",
    # VOICEVOX/REST APIエンドポイント（スラッシュコマンドではない）
    "/audio_query", "/accent_phrases", "/mora_data", "/synthesis",
    "/speakers", "/user_dict_word", "/user_dict",
    "/health", "/status", "/version", "/setting",
    # プレースホルダー・その他非コマンド
    "/xxx", "/foo", "/bar", "/baz", "/test",
})


def _extract_script_paths(text: str) -> list[str]:
    """ドキュメントテキストからスクリプトパス参照を抽出する。

    抽出仕様: バッククォート内の相対パスで .py/.ps1/.sh 拡張子を持つもの。
    除外: 'example' や 'template' を含むパス（テンプレート記述は検証対象外）。
    除外: コードブロック（```...```）内のパス。
    除外: プレースホルダパス（foo.py, bar.py, baz.py）。
    """
    # コードブロックを除去してからパス抽出
    cleaned = re.sub(r"```[\s\S]*?```", "", text)
    _PLACEHOLDER_BASENAMES = {"foo.py", "bar.py", "baz.py", "test.py", "example.py"}
    paths = SCRIPT_PATH_RE.findall(cleaned)
    return [
        p for p in paths
        if "example" not in p.lower()
        and "template" not in p.lower()
        and "/" in p  # 短縮名（basenameのみ）はパス解決不能なので除外
        and Path(p).name not in _PLACEHOLDER_BASENAMES
    ]


def _extract_slash_commands(text: str) -> list[str]:
    """ドキュメントテキストからスラッシュコマンド参照を抽出する。

    抽出仕様: `/xxx` 形式（小文字英字で始まり英数字・アンダースコア・ハイフン）。
    除外: OSパス(/dev等)、URL(/json等)、バージョン(/v1等)、APIエンドポイント。
    コードブロック（```...```）内のパスも除外する。
    """
    # コードブロックを除去してからスラッシュコマンドを抽出
    cleaned = re.sub(r'```[\s\S]*?```', '', text)
    cmds = SLASH_CMD_RE.findall(cleaned)
    return [c for c in cmds if c not in _SLASH_EXCLUDE and not c.startswith("/v")]


def _extract_md_headings(text: str, level: int) -> list[str]:
    """Markdown見出し（指定レベル）を抽出する。"""
    if level < 1 or level > 6:
        return []
    marker = "#" * level
    heading_re = re.compile(rf"^\s*{re.escape(marker)}\s+(.+?)\s*$", re.MULTILINE)
    return [m.group(1).strip() for m in heading_re.finditer(text)]


def _has_heading_with_keywords(headings: list[str], keyword_groups: list[tuple[str, ...]]) -> bool:
    """見出し群に、指定キーワード群（AND条件）を満たすものがあるかを返す。"""
    lowered = [h.lower() for h in headings]
    for heading in lowered:
        for group in keyword_groups:
            if all(keyword.lower() in heading for keyword in group):
                return True
    return False


def lint_cross_ref_script_paths() -> list[str]:
    """WL-XREF-001: SKILL/WORKFLOWに記載のスクリプトパスがディスク上に存在するか。"""
    findings: list[str] = []
    if not WF_ROOT.exists():
        return findings

    for wf_dir in sorted(p for p in WF_ROOT.iterdir() if p.is_dir()):
        if wf_dir.name in {"shared", "claude-skills"}:
            continue
        for md_name in ["SKILL.md", "WORKFLOW.md"]:
            md_path = wf_dir / md_name
            if not md_path.exists():
                continue
            text, _ = read_utf8_checked(md_path)
            if not text:
                continue
            seen_paths: set[str] = set()  # 重複抑制
            for script_path in _extract_script_paths(text):
                if script_path in seen_paths:
                    continue
                seen_paths.add(script_path)
                # 複数の解決パターンを試行:
                # 1. ROOT / script_path (リポジトリルートから)
                # 2. wf_dir / script_path (ワークフローディレクトリから)
                # 3. wf_dir / "scripts" / basename (スクリプトフォルダー内)
                # 4. wf_dir / "lib" / basename (libフォルダー内)
                candidates = [
                    ROOT / script_path,
                    wf_dir / script_path,
                    wf_dir / "scripts" / Path(script_path).name,
                    wf_dir / "lib" / Path(script_path).name,
                ]
                if not any(c.exists() for c in candidates):
                    findings.append(
                        f"[ERROR] {wf_dir.name}/{md_name}: "
                        f"script path `{script_path}` not found on disk (WL-XREF-001)"
                    )
    return findings


def lint_cross_ref_version() -> list[str]:
    """WL-XREF-002: __version__変数とSKILL.md H1バージョンの一致確認。"""
    findings: list[str] = []
    if not WF_ROOT.exists():
        return findings

    for wf_dir in sorted(p for p in WF_ROOT.iterdir() if p.is_dir()):
        if wf_dir.name in {"shared", "claude-skills"}:
            continue
        skill = wf_dir / "SKILL.md"
        if not skill.exists():
            continue
        skill_text, _ = read_utf8_checked(skill)
        if not skill_text:
            continue
        skill_h = first_heading_from_text(skill_text)
        skill_v = version_from_heading(skill_h)
        if not skill_v:
            continue

        # SKILL.mdに記載のスクリプトで __version__ を持つものを探す（重複抑制）
        seen_scripts: set[str] = set()
        for script_path in _extract_script_paths(skill_text):
            if script_path in seen_scripts:
                continue
            seen_scripts.add(script_path)
            resolved = ROOT / script_path
            if not resolved.exists():
                continue
            if not resolved.suffix == ".py":
                continue
            try:
                code = resolved.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            m = PYTHON_VERSION_RE.search(code)
            if m:
                code_v = "v" + m.group(1)
                if code_v != skill_v:
                    findings.append(
                        f"[CAUTION] {wf_dir.name}: __version__={code_v} in "
                        f"{script_path} but SKILL H1={skill_v} (WL-XREF-002)"
                    )
    return findings


def lint_slash_commands() -> list[str]:
    """WL-CMD-001: ドキュメント中のスラッシュコマンドが.agent/workflowsに実在するか。"""
    findings: list[str] = []
    if not WF_ROOT.exists():
        return findings

    existing_wfs = {p.name for p in WF_ROOT.iterdir() if p.is_dir()}
    # ハイフン→アンダースコア / アンダースコア→ハイフンも試行
    existing_variants = set()
    for name in existing_wfs:
        existing_variants.add(name)
        existing_variants.add(name.replace("-", "_"))
        existing_variants.add(name.replace("_", "-"))

    for wf_dir in sorted(p for p in WF_ROOT.iterdir() if p.is_dir()):
        if wf_dir.name in {"shared", "claude-skills"}:
            continue
        for md_file in sorted(wf_dir.rglob("*.md")):
            text, _ = read_utf8_checked(md_file)
            if not text:
                continue
            rel = md_file.relative_to(WF_ROOT)
            seen_cmds: set[str] = set()  # 同一ファイル内の重複抑制
            for cmd in _extract_slash_commands(text):
                cmd_name = cmd.lstrip("/")
                # 自分自身のワークフロー名は除外
                if cmd_name == wf_dir.name or cmd_name == wf_dir.name.replace("-", "_"):
                    continue
                if cmd_name in seen_cmds:
                    continue
                seen_cmds.add(cmd_name)
                if cmd_name not in existing_variants:
                    findings.append(
                        f"[CAUTION] {rel}: slash command `{cmd}` has no matching "
                        f"workflow in .agent/workflows/ (WL-CMD-001)"
                    )
    return findings


def lint_disc_coverage() -> list[str]:
    """WL-DISC-001: workflowsに存在するがdocs/architecture.mdに未記載のワークフロー。"""
    findings: list[str] = []
    arch_path = ROOT / "docs" / "architecture.md"
    if not arch_path.exists() or not WF_ROOT.exists():
        return findings

    arch_text, _ = read_utf8_checked(arch_path)
    if not arch_text:
        return findings

    existing_wfs = {
        p.name for p in WF_ROOT.iterdir()
        if p.is_dir() and p.name not in {"shared", "claude-skills"}
        and p.name not in WIP_IGNORE_WORKFLOWS
    }

    for wf_name in sorted(existing_wfs):
        # ハイフン/アンダースコア両方で検索
        variants = {wf_name, wf_name.replace("-", "_"), wf_name.replace("_", "-")}
        if not any(v in arch_text for v in variants):
            findings.append(
                f"[ADVISORY] workflow '{wf_name}': exists on disk but not mentioned "
                f"in docs/architecture.md (WL-DISC-001)"
            )
    return findings


def lint_ops_migration_note() -> list[str]:
    findings: list[str] = []
    missing = sorted([wf for wf in CONSOLIDATED_TO_OPS if not (WF_ROOT / wf).exists()])
    if not missing:
        return findings

    ops_workflow = WF_ROOT / "ops" / "WORKFLOW.md"
    if not ops_workflow.exists():
        return ["[ERROR] ops: WORKFLOW.md is required when legacy workflows are consolidated"]

    text, read_findings = read_utf8_checked(ops_workflow)
    findings.extend(read_findings)
    if not text:
        return findings

    for wf in missing:
        if wf not in text:
            findings.append(
                f"[ERROR] ops: migration note must mention consolidated workflow '{wf}'"
            )
    return findings


def lint_architecture_doc() -> list[str]:
    findings: list[str] = []
    doc = ROOT / "docs" / "architecture.md"
    if not doc.exists():
        return findings

    text, read_findings = read_utf8_checked(doc)
    findings.extend(read_findings)
    if not text:
        return findings

    in_first_party = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("### 1) First-party source"):
            in_first_party = True
            continue
        if line.startswith("### 2)") or line.startswith("### 3)"):
            in_first_party = False
            continue
        if not in_first_party:
            continue

        m = WF_REF_RE.search(line)
        if not m:
            continue
        wf_name = m.group(1)
        if not (WF_ROOT / wf_name).exists():
            findings.append(
                f"[ERROR] docs/architecture.md: first-party workflow missing on disk: {wf_name}"
            )

    return findings


def lint_agent_readmes() -> tuple[list[str], set[str]]:
    findings: list[str] = []
    referenced_workflows: set[str] = set()
    agents_root = ROOT / "エージェント"
    if not agents_root.exists():
        return findings, referenced_workflows

    for agent_dir in sorted([p for p in agents_root.iterdir() if p.is_dir()]):
        if agent_dir.name in WIP_IGNORE_AGENTS:
            continue
        readme = agent_dir / "README.md"
        if not readme.exists():
            findings.append(f"[ERROR] agent '{agent_dir.name}': missing README.md")
            continue

        text, read_findings = read_utf8_checked(readme)
        findings.extend(read_findings)
        if not text:
            continue

        # WL-RMD-001~005: READMEテンプレート準拠チェック
        h1_headings = _extract_md_headings(text, 1)
        h2_headings = _extract_md_headings(text, 2)
        if not h1_headings:
            findings.append(
                f"[CAUTION] agent '{agent_dir.name}': README.md missing H1 title "
                "(WL-RMD-001)"
            )
        if not _has_heading_with_keywords(h2_headings, [("ワークフロー", "定義"), ("workflow", "definition")]):
            findings.append(
                f"[CAUTION] agent '{agent_dir.name}': README.md missing section "
                "'## ワークフロー定義（正）' (WL-RMD-002)"
            )
        if not _has_heading_with_keywords(h2_headings, [("使い方",), ("usage",)]):
            findings.append(
                f"[CAUTION] agent '{agent_dir.name}': README.md missing section "
                "'## 使い方（最短）' (WL-RMD-003)"
            )
        if not _has_heading_with_keywords(h2_headings, [("入出力",), ("input", "output"), ("inputs", "outputs")]):
            findings.append(
                f"[CAUTION] agent '{agent_dir.name}': README.md missing section "
                "'## 入出力' (WL-RMD-004)"
            )
        if not _has_heading_with_keywords(h2_headings, [("注意事項",), ("caution",), ("safety",), ("notes",)]):
            findings.append(
                f"[CAUTION] agent '{agent_dir.name}': README.md missing section "
                "'## 注意事項' (WL-RMD-005)"
            )

        if FILE_URI_RE.search(text):
            findings.append(
                f"[ERROR] agent '{agent_dir.name}': README.md must not contain file:/// links"
            )
        if "agi agents" in text.lower():
            findings.append(
                f"[ERROR] agent '{agent_dir.name}': README.md contains stale reference 'agi agents'"
            )

        refs = [m.group(1) for m in WF_REF_RE.finditer(text)]
        if not refs:
            findings.append(
                f"[ERROR] agent '{agent_dir.name}': README.md must reference a logical workflow under .agent/workflows/"
            )
            continue
        referenced_workflows.update(refs)

        for wf_name in sorted(set(refs)):
            wf_path = WF_ROOT / wf_name
            if not wf_path.exists():
                if wf_name in CONSOLIDATED_TO_OPS:
                    findings.append(
                        f"[ERROR] agent '{agent_dir.name}': deprecated workflow reference '{wf_name}' (use .agent/workflows/ops/)"
                    )
                else:
                    findings.append(
                        f"[ERROR] agent '{agent_dir.name}': missing workflow reference '{wf_name}'"
                    )

        # Avoid stale slash-commands for consolidated workflows.
        missing = [wf for wf in CONSOLIDATED_TO_OPS if not (WF_ROOT / wf).exists()]
        for wf in missing:
            legacy_cmd = f"/{wf}"
            if legacy_cmd in text:
                findings.append(
                    f"[ERROR] agent '{agent_dir.name}': deprecated command '{legacy_cmd}' (consolidated into /ops)"
                )

    return findings, referenced_workflows


def lint_unreferenced_workflows(referenced_workflows: set[str]) -> list[str]:
    findings: list[str] = []
    for child in sorted(p for p in WF_ROOT.iterdir() if p.is_dir()):
        if child.name in {"shared", "claude-skills"}:
            continue
        if child.name in WIP_IGNORE_WORKFLOWS:
            continue
        if child.name not in referenced_workflows:
            findings.append(
                f"[ADVISORY] workflow '{child.name}': not referenced from any agent README.md"
            )
    return findings


def lint_workflow_logging_coverage() -> list[str]:
    findings: list[str] = []
    for py_path in sorted(WF_ROOT.rglob("*.py")):
        if "__pycache__" in py_path.parts:
            continue
        if "tests" in py_path.parts:
            continue
        if "shared" in py_path.parts:
            continue

        text, read_findings = read_utf8_checked(py_path)
        findings.extend(read_findings)
        if not text:
            continue
        if not MAIN_RE.search(text):
            continue
        if not LOGGING_MARKER_RE.search(text):
            findings.append(
                "[ERROR] "
                f"{py_path.as_posix()}: entrypoint must integrate WorkflowLogger "
                "(logged_main/run_logged_main/WorkflowLogger)"
            )
    return findings


def lint_agent_script_logging_coverage() -> list[str]:
    findings: list[str] = []
    agents_root = ROOT / "エージェント"
    if not agents_root.exists():
        return findings

    for py_path in sorted(agents_root.rglob("*.py")):
        if "__pycache__" in py_path.parts:
            continue
        if "scripts" not in py_path.parts:
            continue
        if "tests" in py_path.parts:
            continue
        relative_parts = py_path.relative_to(agents_root).parts
        if relative_parts and relative_parts[0] in WIP_IGNORE_AGENTS:
            continue

        text, read_findings = read_utf8_checked(py_path)
        findings.extend(read_findings)
        if not text:
            continue
        if not MAIN_RE.search(text):
            continue
        if not LOGGING_MARKER_RE.search(text):
            findings.append(
                "[ERROR] "
                f"{py_path.as_posix()}: script entrypoint must integrate WorkflowLogger "
                "(logged_main/run_logged_main/WorkflowLogger)"
            )
    return findings


def count_severity(findings: list[str]) -> tuple[int, int, int, int]:
    errors = sum(1 for item in findings if item.startswith(ERROR_PREFIX))
    cautions = sum(1 for item in findings if item.startswith(CAUTION_PREFIX))
    advisories = sum(1 for item in findings if item.startswith(ADVISORY_PREFIX))
    warns_legacy = sum(1 for item in findings if item.startswith(WARN_PREFIX))
    return errors, cautions, advisories, warns_legacy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lint workflow/skill/docs consistency")
    parser.add_argument(
        "--explain-severity",
        action="store_true",
        help="Print severity definitions and exit",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit",
    )
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Legacy alias: fail if CAUTION/ADVISORY/WARN exists",
    )
    parser.add_argument(
        "--fail-on-caution",
        action="store_true",
        help="Treat CAUTION findings as CI failure (non-zero exit)",
    )
    parser.add_argument(
        "--fail-on-advisory",
        action="store_true",
        help="Treat ADVISORY findings as CI failure (non-zero exit)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.explain_severity:
        print(SEVERITY_EXPLANATION.rstrip())
        return 0
    if getattr(args, "version", False):
        print(f"workflow_lint v{__version__}")
        return 0

    if not WF_ROOT.exists():
        print(f"[ERROR] workflows root not found: {WF_ROOT}")
        return 2

    findings: list[str] = []
    for child in sorted(p for p in WF_ROOT.iterdir() if p.is_dir()):
        findings.extend(lint_workflow_dir(child))

    findings.extend(lint_ops_migration_note())
    findings.extend(lint_architecture_doc())
    agent_findings, referenced_workflows = lint_agent_readmes()
    findings.extend(agent_findings)
    findings.extend(lint_unreferenced_workflows(referenced_workflows))
    findings.extend(lint_workflow_logging_coverage())
    findings.extend(lint_agent_script_logging_coverage())

    # v1.2.0: リポジトリ全体クロスチェック
    findings.extend(lint_cross_ref_script_paths())
    findings.extend(lint_cross_ref_version())
    findings.extend(lint_slash_commands())
    findings.extend(lint_disc_coverage())

    if findings:
        print("\n".join(findings))
        errors, cautions, advisories, warns_legacy = count_severity(findings)
        print(
            "[SUMMARY] "
            f"errors={errors} cautions={cautions} advisories={advisories} legacy_warnings={warns_legacy}"
        )
        if errors > 0:
            return 1
        if args.fail_on_caution and cautions > 0:
            return 1
        if args.fail_on_advisory and advisories > 0:
            return 1
        if args.fail_on_warn and (cautions + advisories + warns_legacy) > 0:
            return 1
        return 0

    print("[OK] workflow lint passed")
    print("[SUMMARY] errors=0 cautions=0 advisories=0 legacy_warnings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
