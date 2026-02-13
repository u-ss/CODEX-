# -*- coding: utf-8 -*-
"""
Implementation Agent v4.2.4 - Test Selector Module
差分駆動テスト選択（Smoke → Diff → Full）

v4.2.1 変更点:
- added_filesをdiff_related計算の対象に追加
- rename(R*)/copy(C*)ステータスをパース
- renamed_filesフィールド追加

v4.2.4 変更点:
- detect_file_type()にCONFIG_EXCEPTIONS追加（package-lock等の誤分類防止）
"""

from dataclasses import dataclass, field
from typing import List, Set, Dict, Optional
from pathlib import Path
import re


@dataclass
class ChangeSet:
    """変更セット（v4.2.1: rename対応）"""
    changed_files: List[str] = field(default_factory=list)
    changed_modules: List[str] = field(default_factory=list)
    added_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    # v4.2.1追加
    renamed_files: List[tuple] = field(default_factory=list)  # [(old_path, new_path), ...]


@dataclass
class TestPlan:
    """テスト計画"""
    smoke: List[str] = field(default_factory=list)      # 最小スモーク
    diff_related: List[str] = field(default_factory=list)  # 差分関連
    full: List[str] = field(default_factory=list)       # フル実行
    skipped: List[str] = field(default_factory=list)    # スキップ
    
    def get_ordered_tests(self) -> List[str]:
        """実行順序でテストを取得（重複除去）"""
        seen: Set[str] = set()
        result = []
        for test in self.smoke + self.diff_related + self.full:
            if test not in seen:
                seen.add(test)
                result.append(test)
        return result


# ファイル種別判定パターン
FILE_TYPE_PATTERNS = {
    "test": [r"test_.*\.py$", r".*_test\.py$", r".*\.test\.(js|ts)$", r"__tests__/"],
    "ui": [r"\.css$", r"\.scss$", r"\.html$", r"\.vue$", r"\.tsx$", r"\.jsx$"],
    "doc": [r"\.md$", r"\.rst$", r"README", r"CHANGELOG"],
    "config": [r"\.json$", r"\.yaml$", r"\.yml$", r"\.toml$", r"\.ini$", r"\.env"],
    "code": [r"\.py$", r"\.js$", r"\.ts$", r"\.go$", r"\.rs$"],
}

# v4.2.4追加: 例外パターン（configより先に判定）
CONFIG_EXCEPTIONS = [
    (r"package-lock\.json$", "lock"),
    (r"composer\.lock$", "lock"),
    (r"yarn\.lock$", "lock"),
    (r"Pipfile\.lock$", "lock"),
    (r"poetry\.lock$", "lock"),
]


def detect_file_type(file_path: str) -> str:
    """ファイル種別を判定（v4.2.4: 例外パターン対応）"""
    # まず例外チェック（lock ファイルなど）
    for pattern, ftype in CONFIG_EXCEPTIONS:
        if re.search(pattern, file_path):
            return ftype
    
    # 通常判定
    for ftype, patterns in FILE_TYPE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, file_path):
                return ftype
    return "other"


def compute_changeset(git_diff_output: str) -> ChangeSet:
    """
    git diff出力から変更セットを計算（v4.2.1: R*/C*対応）
    
    Args:
        git_diff_output: git diff --name-status の出力
    
    Returns:
        ChangeSet
    """
    changed = []
    added = []
    deleted = []
    renamed = []  # v4.2.1追加
    
    for line in git_diff_output.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            status, file_path = parts[0], parts[1]
            if status == "A":
                added.append(file_path)
            elif status == "D":
                deleted.append(file_path)
            # v4.2.1: R(R100等) と C(C100等) をパース
            elif status.startswith("R") and len(parts) >= 3:
                old_path, new_path = parts[1], parts[2]
                renamed.append((old_path, new_path))
                changed.append(new_path)  # new_pathをchangedとしても登録
            elif status.startswith("C") and len(parts) >= 3:
                # copy: new_pathだけ追加
                new_path = parts[2]
                added.append(new_path)
            else:
                changed.append(file_path)
    
    # モジュール抽出（ディレクトリ単位）
    modules = set()
    for f in changed + added:
        parts = Path(f).parts
        if len(parts) > 1:
            modules.add(parts[0])
    
    return ChangeSet(
        changed_files=changed,
        changed_modules=list(modules),
        added_files=added,
        deleted_files=deleted,
        renamed_files=renamed
    )


def find_related_tests(
    changed_files: List[str],
    test_files: List[str]
) -> List[str]:
    """
    変更ファイルに関連するテストを検索
    
    Args:
        changed_files: 変更されたファイル
        test_files: プロジェクト内の全テストファイル
    
    Returns:
        関連テストファイルのリスト
    """
    related = []
    
    for cf in changed_files:
        cf_stem = Path(cf).stem
        cf_dir = str(Path(cf).parent)
        
        for tf in test_files:
            # ファイル名ベースのマッチング
            if cf_stem in tf:
                related.append(tf)
                continue
            
            # ディレクトリベースのマッチング
            if cf_dir in tf:
                related.append(tf)
    
    return list(set(related))


def plan_tests(
    changeset: ChangeSet,
    all_tests: List[str],
    smoke_tests: List[str] = None
) -> TestPlan:
    """
    テスト計画を作成
    
    Args:
        changeset: ChangeSet
        all_tests: 全テストファイル
        smoke_tests: スモークテスト（省略時は自動選択）
    
    Returns:
        TestPlan
    """
    plan = TestPlan()
    
    # スモークテスト（指定がなければ最初の3つ）
    if smoke_tests:
        plan.smoke = smoke_tests
    else:
        plan.smoke = all_tests[:3] if len(all_tests) > 3 else all_tests
    
    # 差分関連テスト (v4.2.1: added_filesも対象)
    code_changes = [f for f in changeset.changed_files + changeset.added_files if detect_file_type(f) == "code"]
    plan.diff_related = find_related_tests(code_changes, all_tests)
    
    # フル（残り全部、ただしsmoke/diffに含まれないもの）
    covered = set(plan.smoke + plan.diff_related)
    plan.full = [t for t in all_tests if t not in covered]
    
    return plan


@dataclass
class GateResult:
    """ゲート結果"""
    name: str
    passed: bool
    details: str = ""


@dataclass
class GatePolicy:
    """ゲートポリシー（代替ゲート）"""
    change_type: str
    required_gates: List[str] = field(default_factory=list)


# 代替ゲートポリシー
ALTERNATIVE_GATES = {
    "ui": ["snapshot", "accessibility", "e2e_smoke"],
    "doc": ["link_check", "example_code_run", "markdown_lint"],
    "config": ["schema_validation", "load_test", "compatibility"],
    "code": ["unit", "integration"],
}


def get_gates_for_changeset(changeset: ChangeSet) -> List[str]:
    """
    変更セットに基づいて必要なゲートを取得
    """
    gates = set()
    
    for f in changeset.changed_files + changeset.added_files:
        ftype = detect_file_type(f)
        if ftype in ALTERNATIVE_GATES:
            gates.update(ALTERNATIVE_GATES[ftype])
    
    return list(gates)
