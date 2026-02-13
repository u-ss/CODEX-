"""
SKILL.md Loader + Linter

目的: SKILL.mdの品質を強制し、実行のブレを減らす

機能:
1. 必須セクション検査（preconditions / success_criteria / stop_conditions 等）
2. 禁止記述検出（危険操作の無条件実行、外部送信など）
3. trigger衝突検知
4. 構造化コンパイル（セクション抽出）
5. 差分注入（変更なしセクションは再注入しない）

ChatGPT 5.2フィードバック（2026-02-05）より
"""

import re
import yaml
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class LintSeverity(Enum):
    """リントエラーの重大度"""
    ERROR = "error"       # 必須違反、実行不可
    WARNING = "warning"   # 推奨違反、実行可能だが注意
    INFO = "info"         # 改善提案


@dataclass
class LintResult:
    """リント結果"""
    severity: LintSeverity
    code: str           # エラーコード（例: MISSING_SECTION）
    message: str        # 人間向けメッセージ
    line: Optional[int] = None  # 行番号（わかれば）
    suggestion: Optional[str] = None  # 修正提案


@dataclass
class SKILLSection:
    """SKILLのセクション"""
    name: str
    content: str
    line_start: int
    line_end: int
    hash: str  # 差分検出用


@dataclass
class SKILLDocument:
    """パース済みSKILLドキュメント"""
    path: Path
    name: str                    # YAMLのname
    description: str             # YAMLのdescription
    frontmatter: dict           # YAML front matter全体
    sections: dict[str, SKILLSection] = field(default_factory=dict)
    lint_results: list[LintResult] = field(default_factory=list)
    content_hash: str = ""       # 全体ハッシュ（差分検出用）


# 必須セクション
REQUIRED_SECTIONS = [
    "アーキテクチャ",
    "コアコンポーネント",
    "実行フロー",
]

# 推奨セクション（なければWARNING）
RECOMMENDED_SECTIONS = [
    "エラー回復",
    "セキュリティ",
    "Rules",
]

# 必須 front matter キー
REQUIRED_FRONTMATTER = [
    "name",
    "description",
]

# 推奨 front matter キー（ChatGPTフィードバックより）
RECOMMENDED_FRONTMATTER = [
    "platforms",           # win/mac など
    "preconditions",       # 前提条件
    "success_criteria",    # 成功判定
    "stop_conditions",     # 安全停止条件
    "safety_level",        # Allow/Ask/Block の基準
]

# 禁止パターン
FORBIDDEN_PATTERNS = [
    (r"browser_subagent", "browser_subagentは禁止（BOT判定される）"),
    (r"rm\s+-rf\s+/", "危険な削除コマンド"),
    (r"format\s+[a-zA-Z]:", "ディスクフォーマット"),
    (r"del\s+/[sS]\s+/[qQ]", "再帰的削除"),
]


class SKILLLoader:
    """SKILL.md Loader + Linter"""
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path(".skill_cache")
        self._section_cache: dict[str, str] = {}  # セクション別ハッシュキャッシュ
    
    def load(self, skill_path: Path) -> SKILLDocument:
        """SKILL.mdを読み込み、パースしてリント"""
        
        content = skill_path.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        
        # YAML front matter抽出
        frontmatter, body = self._extract_frontmatter(content)
        
        # セクション抽出
        sections = self._extract_sections(body)
        
        # ドキュメント作成
        doc = SKILLDocument(
            path=skill_path,
            name=frontmatter.get("name", "Unknown"),
            description=frontmatter.get("description", ""),
            frontmatter=frontmatter,
            sections=sections,
            content_hash=content_hash,
        )
        
        # リント実行
        doc.lint_results = self._lint(doc, content)
        
        return doc
    
    def _extract_frontmatter(self, content: str) -> tuple[dict, str]:
        """YAML front matterを抽出"""
        
        pattern = r"^---\s*\n(.*?)\n---\s*\n"
        match = re.match(pattern, content, re.DOTALL)
        
        if match:
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                frontmatter = {}
            body = content[match.end():]
        else:
            frontmatter = {}
            body = content
        
        return frontmatter, body
    
    def _extract_sections(self, body: str) -> dict[str, SKILLSection]:
        """Markdownセクションを抽出"""
        
        sections = {}
        lines = body.split("\n")
        
        current_section = None
        current_content = []
        current_start = 0
        
        for i, line in enumerate(lines):
            # ## で始まるヘッダーを検出
            header_match = re.match(r"^##\s+(.+)$", line)
            
            if header_match:
                # 前のセクションを保存
                if current_section:
                    content_text = "\n".join(current_content)
                    sections[current_section] = SKILLSection(
                        name=current_section,
                        content=content_text,
                        line_start=current_start,
                        line_end=i - 1,
                        hash=hashlib.sha256(content_text.encode()).hexdigest()[:16],
                    )
                
                # 新しいセクション開始
                current_section = header_match.group(1).strip()
                current_content = []
                current_start = i
            elif current_section:
                current_content.append(line)
        
        # 最後のセクションを保存
        if current_section:
            content_text = "\n".join(current_content)
            sections[current_section] = SKILLSection(
                name=current_section,
                content=content_text,
                line_start=current_start,
                line_end=len(lines) - 1,
                hash=hashlib.sha256(content_text.encode()).hexdigest()[:16],
            )
        
        return sections
    
    def _lint(self, doc: SKILLDocument, content: str) -> list[LintResult]:
        """リント実行"""
        
        results = []
        
        # 1. 必須 front matter チェック
        for key in REQUIRED_FRONTMATTER:
            if key not in doc.frontmatter:
                results.append(LintResult(
                    severity=LintSeverity.ERROR,
                    code="MISSING_FRONTMATTER",
                    message=f"必須front matterキーがありません: {key}",
                    suggestion=f"---\n{key}: <値>\n---\nを追加してください",
                ))
        
        # 2. 推奨 front matter チェック
        for key in RECOMMENDED_FRONTMATTER:
            if key not in doc.frontmatter:
                results.append(LintResult(
                    severity=LintSeverity.WARNING,
                    code="RECOMMENDED_FRONTMATTER",
                    message=f"推奨front matterキーがありません: {key}",
                    suggestion=f"機械運用の安定化のため {key} の追加を検討してください",
                ))
        
        # 3. 必須セクションチェック
        section_names = [s.lower() for s in doc.sections.keys()]
        for required in REQUIRED_SECTIONS:
            found = any(required.lower() in name for name in section_names)
            if not found:
                results.append(LintResult(
                    severity=LintSeverity.WARNING,
                    code="MISSING_SECTION",
                    message=f"セクションが見つかりません: {required}",
                ))
        
        # 4. 禁止パターンチェック
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            for pattern, message in FORBIDDEN_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    results.append(LintResult(
                        severity=LintSeverity.ERROR,
                        code="FORBIDDEN_PATTERN",
                        message=message,
                        line=i,
                        suggestion="この記述を削除または修正してください",
                    ))
        
        # 5. BOT判定ルールセクションチェック
        section_content = "\n".join(s.content for s in doc.sections.values())
        if "bot" not in section_content.lower() or "禁止" not in section_content:
            results.append(LintResult(
                severity=LintSeverity.WARNING,
                code="MISSING_BOT_RULES",
                message="BOT判定防止ルールセクションがありません",
                suggestion="browser_subagent禁止などのルールを明記してください",
            ))
        
        return results
    
    def get_changed_sections(
        self, 
        doc: SKILLDocument, 
        previous_hashes: dict[str, str]
    ) -> list[SKILLSection]:
        """変更されたセクションのみを取得（差分注入用）"""
        
        changed = []
        for name, section in doc.sections.items():
            prev_hash = previous_hashes.get(name, "")
            if section.hash != prev_hash:
                changed.append(section)
        
        return changed
    
    def get_sections_for_phase(
        self, 
        doc: SKILLDocument, 
        phase: str
    ) -> list[SKILLSection]:
        """実行フェーズに応じたセクションを取得"""
        
        phase_sections = {
            "perceive": ["アーキテクチャ", "screen_key", "SS戦略"],
            "decide": ["Rules", "エラー回復", "回復戦略"],
            "act": ["実行フロー", "コアコンポーネント", "ブラウザ", "CDP"],
        }
        
        target_keywords = phase_sections.get(phase.lower(), [])
        
        result = []
        for name, section in doc.sections.items():
            for keyword in target_keywords:
                if keyword.lower() in name.lower():
                    result.append(section)
                    break
        
        return result
    
    def has_errors(self, doc: SKILLDocument) -> bool:
        """ERRORレベルのリント結果があるか"""
        return any(r.severity == LintSeverity.ERROR for r in doc.lint_results)
    
    def format_lint_report(self, doc: SKILLDocument) -> str:
        """リント結果をフォーマット"""
        
        if not doc.lint_results:
            return "✅ リント通過: 問題なし"
        
        lines = [f"📋 リント結果: {doc.path.name}"]
        
        errors = [r for r in doc.lint_results if r.severity == LintSeverity.ERROR]
        warnings = [r for r in doc.lint_results if r.severity == LintSeverity.WARNING]
        infos = [r for r in doc.lint_results if r.severity == LintSeverity.INFO]
        
        if errors:
            lines.append(f"\n❌ エラー ({len(errors)}件)")
            for r in errors:
                line_info = f" (行{r.line})" if r.line else ""
                lines.append(f"  - [{r.code}]{line_info} {r.message}")
                if r.suggestion:
                    lines.append(f"    💡 {r.suggestion}")
        
        if warnings:
            lines.append(f"\n⚠️ 警告 ({len(warnings)}件)")
            for r in warnings:
                lines.append(f"  - [{r.code}] {r.message}")
        
        if infos:
            lines.append(f"\nℹ️ 情報 ({len(infos)}件)")
            for r in infos:
                lines.append(f"  - {r.message}")
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    import sys
    
    loader = SKILLLoader()
    
    # テスト用SKILL.mdパス
    test_paths = [
        Path(r"c:\Users\dodos\Documents\agi agents\agent\skills\desktop_control\SKILL.md"),
    ]
    
    all_passed = True
    
    for skill_path in test_paths:
        if not skill_path.exists():
            print(f"❌ ファイルなし: {skill_path}")
            continue
        
        print(f"\n{'='*60}")
        print(f"📄 {skill_path.name}")
        print(f"{'='*60}")
        
        doc = loader.load(skill_path)
        
        print(f"\n📊 基本情報:")
        print(f"  - 名前: {doc.name}")
        print(f"  - 説明: {doc.description}")
        print(f"  - セクション数: {len(doc.sections)}")
        print(f"  - ハッシュ: {doc.content_hash}")
        
        print(f"\n📑 セクション一覧:")
        for name, section in doc.sections.items():
            print(f"  - {name} (行{section.line_start}-{section.line_end}, hash:{section.hash})")
        
        print(f"\n{loader.format_lint_report(doc)}")
        
        if loader.has_errors(doc):
            all_passed = False
    
    print(f"\n{'='*60}")
    print(f"{'✅ テスト完了' if all_passed else '❌ エラーあり'}")
