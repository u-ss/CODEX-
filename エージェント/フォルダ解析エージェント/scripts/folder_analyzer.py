# -*- coding: utf-8 -*-
"""フォルダ解析エージェント

指定フォルダ内のファイルを再帰的に解析し、
各ファイルの種類・サイズ・構造・内容概要を把握する。

使い方:
    python folder_analyzer.py <target_dir> [--output-dir <dir>] [--exclude <pattern>] [--max-depth <n>]
"""

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional


# ===== データクラス =====

@dataclass
class FileInfo:
    """ファイルのメタ情報"""
    path: str          # ファイルの絶対パス
    name: str          # ファイル名
    extension: str     # 拡張子（.py等）
    size: int          # バイト数
    modified_at: str   # 最終更新日時（ISO形式）
    relative_path: str # ルートからの相対パス


@dataclass
class PythonAnalysisResult:
    """Python解析結果"""
    classes: list = field(default_factory=list)      # [{"name": str, "methods": [str], "docstring": str}]
    functions: list = field(default_factory=list)     # [{"name": str, "args": [str], "docstring": str}]
    imports: list = field(default_factory=list)       # [str]
    line_count: int = 0


@dataclass
class MarkdownAnalysisResult:
    """Markdown解析結果"""
    headings: list = field(default_factory=list)  # [{"level": int, "text": str}]
    links: list = field(default_factory=list)      # [{"text": str, "url": str}]
    line_count: int = 0


@dataclass
class JsonYamlAnalysisResult:
    """JSON/YAML解析結果"""
    top_keys: list = field(default_factory=list)   # トップレベルキー一覧
    max_depth: int = 0                              # ネスト深度
    item_count: int = 0                             # 要素数


@dataclass
class GenericAnalysisResult:
    """汎用解析結果"""
    line_count: int = 0
    is_binary: bool = False
    mime_guess: str = "text/plain"


@dataclass
class FileAnalysis:
    """ファイル解析結果の統合型"""
    file_info: FileInfo
    file_type: str                              # python, markdown, json, yaml, generic
    details: Any = None                         # 各解析結果
    error: Optional[str] = None                 # 解析エラー時のメッセージ


# ===== 解析ハンドラクラス =====

class PythonAnalyzer:
    """Pythonファイル解析器（AST使用）"""

    def analyze(self, file_path: Path) -> PythonAnalysisResult:
        """Pythonファイルを解析してクラス/関数/import情報を抽出"""
        result = PythonAnalysisResult()
        try:
            source = file_path.read_text(encoding="utf-8")
            result.line_count = len(source.splitlines())
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError) as e:
            # 解析失敗してもメタ情報は返す
            try:
                result.line_count = len(file_path.read_text(encoding="utf-8", errors="replace").splitlines())
            except Exception:
                pass
            return result

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(item.name)
                result.classes.append({
                    "name": node.name,
                    "methods": methods,
                    "docstring": ast.get_docstring(node) or "",
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [arg.arg for arg in node.args.args]
                result.functions.append({
                    "name": node.name,
                    "args": args,
                    "docstring": ast.get_docstring(node) or "",
                })
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    result.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    result.imports.append(node.module)

        return result


class MarkdownAnalyzer:
    """Markdownファイル解析器"""

    # 見出しパターン
    _heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    # リンクパターン [text](url)
    _link_re = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

    def analyze(self, file_path: Path) -> MarkdownAnalysisResult:
        """Markdownファイルの見出し・リンクを抽出"""
        result = MarkdownAnalysisResult()
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = file_path.read_text(encoding="utf-8", errors="replace")

        result.line_count = len(content.splitlines())

        # 見出し抽出
        for match in self._heading_re.finditer(content):
            level = len(match.group(1))
            text = match.group(2).strip()
            result.headings.append({"level": level, "text": text})

        # リンク抽出
        for match in self._link_re.finditer(content):
            text = match.group(1)
            url = match.group(2)
            result.links.append({"text": text, "url": url})

        return result


class JsonYamlAnalyzer:
    """JSON/YAMLファイル解析器"""

    def analyze(self, file_path: Path) -> JsonYamlAnalysisResult:
        """JSON/YAMLファイルのキー構造を抽出"""
        result = JsonYamlAnalysisResult()
        suffix = file_path.suffix.lower()

        try:
            content = file_path.read_text(encoding="utf-8")
            if suffix == ".json":
                data = json.loads(content)
            elif suffix in (".yaml", ".yml"):
                data = self._parse_yaml_simple(content)
            else:
                return result

            if isinstance(data, dict):
                result.top_keys = list(data.keys())
                result.max_depth = self._calc_depth(data)
                result.item_count = self._count_items(data)
            elif isinstance(data, list):
                result.top_keys = []
                result.max_depth = self._calc_depth(data)
                result.item_count = len(data)

        except Exception:
            pass

        return result

    def _parse_yaml_simple(self, content: str) -> dict:
        """シンプルなYAMLパーサー（PyYAML不要）"""
        # 基本的なkey: value形式のみ対応
        result = {}
        current_key = None
        indent_stack = [(0, result)]

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())

            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()

                # インデントレベルに応じて親を特定
                while len(indent_stack) > 1 and indent <= indent_stack[-1][0]:
                    indent_stack.pop()

                parent = indent_stack[-1][1]

                if value:
                    # 値の型変換
                    parent[key] = self._convert_yaml_value(value)
                else:
                    # ネストされた辞書
                    child = {}
                    parent[key] = child
                    indent_stack.append((indent + 1, child))

        return result

    def _convert_yaml_value(self, value: str) -> Any:
        """YAML値の型変換"""
        if value.lower() in ("true", "yes"):
            return True
        if value.lower() in ("false", "no"):
            return False
        if value.lower() in ("null", "~"):
            return None
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    def _calc_depth(self, obj: Any, current: int = 1) -> int:
        """ネスト深度を計算"""
        if isinstance(obj, dict):
            if not obj:
                return current
            return max(self._calc_depth(v, current + 1) for v in obj.values())
        elif isinstance(obj, list):
            if not obj:
                return current
            return max(self._calc_depth(v, current + 1) for v in obj)
        return current

    def _count_items(self, obj: Any) -> int:
        """要素数を再帰的にカウント"""
        count = 0
        if isinstance(obj, dict):
            count += len(obj)
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    count += self._count_items(v)
        elif isinstance(obj, list):
            count += len(obj)
            for v in obj:
                if isinstance(v, (dict, list)):
                    count += self._count_items(v)
        return count


class GenericAnalyzer:
    """汎用ファイル解析器"""

    # バイナリ判定の拡張子
    BINARY_EXTENSIONS = {
        ".exe", ".dll", ".so", ".dylib", ".bin",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
        ".mp3", ".mp4", ".avi", ".mov", ".wav",
        ".zip", ".tar", ".gz", ".7z", ".rar",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        ".pyc", ".pyo", ".whl",
    }

    # MIME推定マッピング
    MIME_MAP = {
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".ts": "text/typescript",
        ".html": "text/html",
        ".css": "text/css",
        ".md": "text/markdown",
        ".json": "application/json",
        ".yaml": "application/x-yaml",
        ".yml": "application/x-yaml",
        ".xml": "application/xml",
        ".txt": "text/plain",
        ".sh": "text/x-shellscript",
        ".ps1": "text/x-powershell",
        ".bat": "text/x-batch",
        ".toml": "application/toml",
        ".ini": "text/plain",
        ".cfg": "text/plain",
        ".csv": "text/csv",
    }

    def analyze(self, file_path: Path) -> GenericAnalysisResult:
        """汎用的なファイル解析"""
        result = GenericAnalysisResult()
        suffix = file_path.suffix.lower()

        result.is_binary = suffix in self.BINARY_EXTENSIONS
        result.mime_guess = self.MIME_MAP.get(suffix, "application/octet-stream" if result.is_binary else "text/plain")

        if not result.is_binary:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                result.line_count = len(content.splitlines())
            except Exception:
                result.is_binary = True

        return result


# ===== メイン解析エンジン =====

class FolderAnalyzer:
    """フォルダ解析エンジン"""

    # ファイル種別ごとの解析器マッピング
    ANALYZERS = {
        ".py": ("python", PythonAnalyzer),
        ".md": ("markdown", MarkdownAnalyzer),
        ".json": ("json", JsonYamlAnalyzer),
        ".yaml": ("yaml", JsonYamlAnalyzer),
        ".yml": ("yaml", JsonYamlAnalyzer),
    }

    # デフォルト除外パターン
    DEFAULT_EXCLUDES = [
        "__pycache__", ".git", "node_modules", ".venv", "venv",
        ".mypy_cache", ".pytest_cache", "*.pyc", ".DS_Store",
    ]

    def __init__(
        self,
        target_dir: str,
        exclude_patterns: Optional[list[str]] = None,
        max_depth: Optional[int] = None,
    ):
        self.target_dir = Path(target_dir).resolve()
        self.exclude_patterns = exclude_patterns or []
        self.max_depth = max_depth
        self._py_analyzer = PythonAnalyzer()
        self._md_analyzer = MarkdownAnalyzer()
        self._jy_analyzer = JsonYamlAnalyzer()
        self._generic_analyzer = GenericAnalyzer()

    def scan(self) -> list[FileInfo]:
        """フォルダを再帰的にスキャンしてファイル一覧を取得"""
        files = []
        self._scan_recursive(self.target_dir, files, depth=0)
        return sorted(files, key=lambda f: f.relative_path)

    def _scan_recursive(self, directory: Path, files: list, depth: int):
        """再帰スキャン実装"""
        if self.max_depth is not None and depth > self.max_depth:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda e: e.name)
        except PermissionError:
            return

        for entry in entries:
            # 除外チェック
            if self._should_exclude(entry):
                continue

            if entry.is_file():
                try:
                    stat = entry.stat()
                    files.append(FileInfo(
                        path=str(entry),
                        name=entry.name,
                        extension=entry.suffix.lower(),
                        size=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        relative_path=str(entry.relative_to(self.target_dir)),
                    ))
                except (OSError, ValueError):
                    pass
            elif entry.is_dir():
                self._scan_recursive(entry, files, depth + 1)

    def _should_exclude(self, entry: Path) -> bool:
        """除外判定"""
        name = entry.name
        # デフォルト除外
        for pattern in self.DEFAULT_EXCLUDES:
            if fnmatch(name, pattern):
                return True
        # ユーザー指定除外
        for pattern in self.exclude_patterns:
            if fnmatch(name, pattern):
                return True
        return False

    def analyze(self, file_info: FileInfo) -> FileAnalysis:
        """個別ファイルを解析"""
        file_path = Path(file_info.path)
        suffix = file_info.extension

        try:
            if suffix in self.ANALYZERS:
                file_type, analyzer_cls = self.ANALYZERS[suffix]
                # キャッシュ済みインスタンスを使用
                if file_type == "python":
                    details = self._py_analyzer.analyze(file_path)
                elif file_type == "markdown":
                    details = self._md_analyzer.analyze(file_path)
                elif file_type in ("json", "yaml"):
                    details = self._jy_analyzer.analyze(file_path)
                else:
                    details = self._generic_analyzer.analyze(file_path)
            else:
                file_type = "generic"
                details = self._generic_analyzer.analyze(file_path)

            return FileAnalysis(
                file_info=file_info,
                file_type=file_type,
                details=details,
            )
        except Exception as e:
            return FileAnalysis(
                file_info=file_info,
                file_type="error",
                error=str(e),
            )


# ===== レポート生成 =====

class AnalysisReport:
    """解析レポート生成"""

    def __init__(self, analyses: list[FileAnalysis], root_dir: str):
        self.analyses = analyses
        self.root_dir = root_dir

    def get_summary(self) -> dict:
        """サマリー統計を生成"""
        extensions = {}
        total_size = 0
        total_lines = 0

        for a in self.analyses:
            ext = a.file_info.extension or "(なし)"
            extensions[ext] = extensions.get(ext, 0) + 1
            total_size += a.file_info.size

            # 行数の取得
            if hasattr(a.details, "line_count"):
                total_lines += a.details.line_count

        return {
            "total_files": len(self.analyses),
            "total_size_bytes": total_size,
            "total_lines": total_lines,
            "extensions": extensions,
        }

    def to_json(self) -> str:
        """JSON形式でレポート出力"""
        summary = self.get_summary()
        files_data = []

        for a in self.analyses:
            file_entry = {
                "path": a.file_info.relative_path,
                "name": a.file_info.name,
                "type": a.file_type,
                "size": a.file_info.size,
                "extension": a.file_info.extension,
                "modified_at": a.file_info.modified_at,
            }

            # 詳細情報を追加
            if a.file_type == "python" and isinstance(a.details, PythonAnalysisResult):
                file_entry["details"] = {
                    "classes": a.details.classes,
                    "functions": a.details.functions,
                    "imports": a.details.imports,
                    "line_count": a.details.line_count,
                }
            elif a.file_type == "markdown" and isinstance(a.details, MarkdownAnalysisResult):
                file_entry["details"] = {
                    "headings": a.details.headings,
                    "links": a.details.links,
                    "line_count": a.details.line_count,
                }
            elif a.file_type in ("json", "yaml") and isinstance(a.details, JsonYamlAnalysisResult):
                file_entry["details"] = {
                    "top_keys": a.details.top_keys,
                    "max_depth": a.details.max_depth,
                    "item_count": a.details.item_count,
                }
            elif isinstance(a.details, GenericAnalysisResult):
                file_entry["details"] = {
                    "line_count": a.details.line_count,
                    "is_binary": a.details.is_binary,
                    "mime_guess": a.details.mime_guess,
                }

            if a.error:
                file_entry["error"] = a.error

            files_data.append(file_entry)

        return json.dumps(
            {"summary": summary, "files": files_data},
            indent=2,
            ensure_ascii=False,
        )

    def to_markdown(self) -> str:
        """Markdown形式でレポート出力"""
        summary = self.get_summary()
        lines = []

        lines.append("# フォルダ解析レポート")
        lines.append("")
        lines.append(f"- **対象**: `{self.root_dir}`")
        lines.append(f"- **解析日時**: {datetime.now().isoformat()}")
        lines.append(f"- **ファイル数**: {summary['total_files']}")
        lines.append(f"- **合計サイズ**: {self._format_size(summary['total_size_bytes'])}")
        lines.append(f"- **合計行数**: {summary['total_lines']:,}")
        lines.append("")

        # 拡張子分布
        lines.append("## 拡張子分布")
        lines.append("")
        lines.append("| 拡張子 | ファイル数 |")
        lines.append("|:-------|----------:|")
        for ext, count in sorted(summary["extensions"].items(), key=lambda x: -x[1]):
            lines.append(f"| `{ext}` | {count} |")
        lines.append("")

        # ファイル一覧
        lines.append("## ファイル詳細")
        lines.append("")

        for a in self.analyses:
            lines.append(f"### `{a.file_info.relative_path}`")
            lines.append("")
            lines.append(f"- **種別**: {a.file_type}")
            lines.append(f"- **サイズ**: {self._format_size(a.file_info.size)}")

            if a.file_type == "python" and isinstance(a.details, PythonAnalysisResult):
                lines.append(f"- **行数**: {a.details.line_count}")
                if a.details.imports:
                    lines.append(f"- **import**: {', '.join(a.details.imports)}")
                if a.details.classes:
                    for cls in a.details.classes:
                        methods_str = ", ".join(cls["methods"]) if cls["methods"] else "(なし)"
                        lines.append(f"- **クラス `{cls['name']}`**: メソッド: {methods_str}")
                if a.details.functions:
                    func_names = [f["name"] for f in a.details.functions]
                    lines.append(f"- **関数**: {', '.join(func_names)}")

            elif a.file_type == "markdown" and isinstance(a.details, MarkdownAnalysisResult):
                lines.append(f"- **行数**: {a.details.line_count}")
                if a.details.headings:
                    lines.append("- **構造**:")
                    for h in a.details.headings:
                        indent = "  " * h["level"]
                        lines.append(f"  {indent}- {h['text']}")

            elif a.file_type in ("json", "yaml") and isinstance(a.details, JsonYamlAnalysisResult):
                if a.details.top_keys:
                    lines.append(f"- **キー**: {', '.join(a.details.top_keys)}")
                lines.append(f"- **ネスト深度**: {a.details.max_depth}")

            elif isinstance(a.details, GenericAnalysisResult):
                lines.append(f"- **行数**: {a.details.line_count}")
                if a.details.is_binary:
                    lines.append("- **バイナリファイル**")

            if a.error:
                lines.append(f"- ⚠️ **エラー**: {a.error}")

            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """読みやすいサイズ表記に変換"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


# ===== エージェント構造検出 =====

@dataclass
class AgentEntry:
    """エージェント情報"""
    name: str                          # ワークフロー名（英小文字）
    workflow_dir: str                  # 論理層パス
    has_skill: bool = False            # SKILL.md の有無
    has_workflow: bool = False         # WORKFLOW.md の有無
    skill_name: str = ""               # SKILL.md の name フィールド
    skill_version: str = ""            # バージョン
    physical_dir: Optional[str] = None # 物理層パス（エージェント/xxx/）
    scripts: list = field(default_factory=list)  # スクリプト一覧
    tests: list = field(default_factory=list)     # テストファイル一覧
    lib_modules: list = field(default_factory=list)  # libモジュール一覧


class AgentMapBuilder:
    """ワークスペース内のエージェント構造を検出・一覧化"""

    def __init__(self, workspace_root: str):
        self.root = Path(workspace_root).resolve()
        self.workflows_dir = self.root / ".agent" / "workflows"
        self.agents_dir = self.root / "エージェント"
        self.tests_dir = self.root / "tests"
        self._readme_workflow_map = self._build_readme_workflow_map()

    def build(self) -> list[AgentEntry]:
        """全エージェントを検出して一覧化"""
        agents = []

        if not self.workflows_dir.exists():
            return agents

        # 論理層（.agent/workflows/）をスキャン
        for wf_dir in sorted(self.workflows_dir.iterdir()):
            if not wf_dir.is_dir() or wf_dir.name == "shared":
                continue

            entry = AgentEntry(
                name=wf_dir.name,
                workflow_dir=str(wf_dir),
            )

            # SKILL.md チェック
            skill_path = wf_dir / "SKILL.md"
            if skill_path.exists():
                entry.has_skill = True
                entry.skill_name, entry.skill_version = self._parse_skill_frontmatter(skill_path)

            # WORKFLOW.md チェック
            workflow_path = wf_dir / "WORKFLOW.md"
            if workflow_path.exists():
                entry.has_workflow = True

            # lib/ モジュール
            lib_dir = wf_dir / "lib"
            if lib_dir.exists():
                entry.lib_modules = [
                    f.name for f in sorted(lib_dir.iterdir())
                    if f.is_file() and f.suffix == ".py"
                ]

            # 物理層の検出（エージェント/xxx/ を名前で紐づけ）
            entry.physical_dir = self._find_physical_dir(wf_dir.name)

            # スクリプト検出
            if entry.physical_dir:
                scripts_dir = Path(entry.physical_dir) / "scripts"
                if scripts_dir.exists():
                    entry.scripts = [
                        f.name for f in sorted(scripts_dir.iterdir())
                        if f.is_file() and f.suffix == ".py"
                    ]

            # テストファイル検出
            entry.tests = self._find_tests(wf_dir.name)

            agents.append(entry)

        return agents

    def _parse_skill_frontmatter(self, skill_path: Path) -> tuple[str, str]:
        """SKILL.md の frontmatter から name を抽出"""
        try:
            content = skill_path.read_text(encoding="utf-8")
            # YAML frontmatter からnameを抽出
            match = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
            if match:
                fm = match.group(1)
                name_match = re.search(r"name:\s*(.+)", fm)
                if name_match:
                    full_name = name_match.group(1).strip()
                    # バージョン抽出
                    ver_match = re.search(r"v(\d+\.\d+\.\d+)", full_name)
                    version = ver_match.group(0) if ver_match else ""
                    return full_name, version
        except Exception:
            pass
        return "", ""

    def _build_readme_workflow_map(self) -> dict[str, str]:
        """物理層 README.md から workflow -> agent_dir の対応を抽出"""
        mapping: dict[str, str] = {}
        if not self.agents_dir.exists():
            return mapping

        pattern = re.compile(r"\.agent/workflows/([A-Za-z0-9_-]+)/")
        for agent_dir in sorted(self.agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            readme_path = agent_dir / "README.md"
            if not readme_path.exists():
                continue
            try:
                content = readme_path.read_text(encoding="utf-8")
            except Exception:
                continue
            for workflow_name in pattern.findall(content):
                mapping.setdefault(workflow_name, str(agent_dir))
        return mapping

    # 物理層ディレクトリ名のマッピング（ワークフロー名 → エージェントフォルダ名）
    _PHYSICAL_MAP = {
        "code": "実装エージェント",
        "pdca": "PDCAエージェント",
        "research": "リサーチエージェント",
        "desktop": "デスクトップ操作エージェント",
        "desktop-chatgpt": "デスクトップ操作エージェント",
        "monitor": "モニターエージェント",
        "orchestrator_pm": "オーケストレーター",
        "folder-check": "フォルダ解析エージェント",
        "ops": "デプロイエージェント",
        "codex": None,  # 物理層なし
        "check": None,
        "ki-learning": None,
        "shared": None,
    }

    def _find_physical_dir(self, workflow_name: str) -> Optional[str]:
        """ワークフロー名から物理層ディレクトリを探す"""
        if not self.agents_dir.exists():
            return None

        # 1) README.md の workflow 参照を優先
        if workflow_name in self._readme_workflow_map:
            return self._readme_workflow_map[workflow_name]

        # マッピングテーブルで検索
        if workflow_name in self._PHYSICAL_MAP:
            folder_name = self._PHYSICAL_MAP[workflow_name]
            if folder_name:
                candidate = self.agents_dir / folder_name
                if candidate.exists():
                    return str(candidate)
            return None

        # フォールバック: エージェント/ 配下で部分一致検索
        for d in self.agents_dir.iterdir():
            if d.is_dir() and workflow_name.lower() in d.name.lower():
                return str(d)

        return None

    def _find_tests(self, workflow_name: str) -> list[str]:
        """ワークフローに対応するテストファイルを検出"""
        tests = []
        if not self.tests_dir.exists():
            return tests

        # テストファイル名のパターンで検索
        search_terms = [
            workflow_name.replace("-", "_"),
            workflow_name.replace("-", ""),
        ]

        for f in sorted(self.tests_dir.iterdir()):
            if f.is_file() and f.suffix == ".py":
                for term in search_terms:
                    if term in f.name.lower():
                        tests.append(f.name)
                        break

        return tests

    def to_json(self) -> str:
        """エージェントマップをJSON出力"""
        agents = self.build()
        data = {
            "workspace_root": str(self.root),
            "generated_at": datetime.now().isoformat(),
            "total_agents": len(agents),
            "agents": [],
        }

        for a in agents:
            entry = {
                "name": a.name,
                "slash_command": f"/{a.name}",
                "skill_name": a.skill_name,
                "version": a.skill_version,
                "has_skill_md": a.has_skill,
                "has_workflow_md": a.has_workflow,
                "workflow_dir": a.workflow_dir,
                "physical_dir": a.physical_dir,
                "scripts": a.scripts,
                "tests": a.tests,
                "lib_modules": a.lib_modules,
            }
            data["agents"].append(entry)

        return json.dumps(data, indent=2, ensure_ascii=False)

    def to_markdown(self) -> str:
        """エージェントマップをMarkdown出力"""
        agents = self.build()
        lines = []

        lines.append("# エージェント構造マップ")
        lines.append("")
        lines.append(f"- **ワークスペース**: `{self.root}`")
        lines.append(f"- **検出日時**: {datetime.now().isoformat()}")
        lines.append(f"- **エージェント数**: {len(agents)}")
        lines.append("")

        lines.append("## エージェント一覧")
        lines.append("")
        lines.append("| コマンド | 名前 | SKILL | WORKFLOW | スクリプト | テスト |")
        lines.append("|:---------|:-----|:-----:|:--------:|:---------:|:-----:|")
        for a in agents:
            skill = "✅" if a.has_skill else "❌"
            workflow = "✅" if a.has_workflow else "❌"
            scripts = str(len(a.scripts)) if a.scripts else "-"
            tests = str(len(a.tests)) if a.tests else "-"
            lines.append(f"| `/{a.name}` | {a.skill_name or a.name} | {skill} | {workflow} | {scripts} | {tests} |")
        lines.append("")

        # 詳細
        lines.append("## 詳細")
        lines.append("")
        for a in agents:
            lines.append(f"### `/{a.name}`")
            lines.append("")
            if a.skill_name:
                lines.append(f"- **名前**: {a.skill_name}")
            lines.append(f"- **論理層**: `{a.workflow_dir}`")
            if a.physical_dir:
                lines.append(f"- **物理層**: `{a.physical_dir}`")
            if a.scripts:
                lines.append(f"- **スクリプト**: {', '.join(a.scripts)}")
            if a.tests:
                lines.append(f"- **テスト**: {', '.join(a.tests)}")
            if a.lib_modules:
                lines.append(f"- **lib/**: {', '.join(a.lib_modules)}")
            lines.append("")

        return "\n".join(lines)


# ===== CLI =====

def main():
    """CLIエントリポイント"""
    parser = argparse.ArgumentParser(
        description="フォルダ解析エージェント - フォルダ内の各ファイルを解析して把握する",
    )
    parser.add_argument("target_dir", nargs="?", default=None, help="解析対象フォルダのパス（--workspace時は不要）")
    parser.add_argument("--output-dir", default=None, help="出力先ディレクトリ（デフォルト: _outputs/folder-check/latest/）")
    parser.add_argument("--exclude", nargs="*", default=[], help="除外パターン（glob形式）")
    parser.add_argument("--max-depth", type=int, default=None, help="最大走査深度")
    parser.add_argument("--json-only", action="store_true", help="JSONのみ出力")
    parser.add_argument("--workspace", action="store_true", help="ワークスペース全体を自動解析")
    parser.add_argument("--agent-map", action="store_true", help="エージェント構造マップのみ生成")

    args = parser.parse_args()

    # ワークスペースモード: スクリプトの3階層上をルートとする
    if args.workspace or args.agent_map:
        target = Path(__file__).resolve().parent.parent.parent.parent
    elif args.target_dir:
        target = Path(args.target_dir).resolve()
    else:
        print("エラー: target_dir または --workspace を指定してください", file=sys.stderr)
        sys.exit(1)

    if not target.exists() or not target.is_dir():
        print(f"エラー: ディレクトリが存在しません: {target}", file=sys.stderr)
        sys.exit(1)

    # 出力先ディレクトリの設定
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = target / "_outputs" / "folder-check" / "latest"

    output_dir.mkdir(parents=True, exist_ok=True)

    # エージェントマップのみモード
    if args.agent_map:
        print(f"🗺️ エージェント構造マップ生成: {target}")
        builder = AgentMapBuilder(str(target))

        agent_json_path = output_dir / "agent_map.json"
        agent_json_path.write_text(builder.to_json(), encoding="utf-8")
        print(f"   → JSON: {agent_json_path}")

        agent_md_path = output_dir / "agent_map.md"
        agent_md_path.write_text(builder.to_markdown(), encoding="utf-8")
        print(f"   → Markdown: {agent_md_path}")

        agents = builder.build()
        print(f"\n✅ エージェントマップ完了! {len(agents)} エージェント検出")
        return

    print(f"🔍 フォルダ解析開始: {target}")
    print(f"📁 出力先: {output_dir}")

    # Phase 1: スキャン
    print("\n📂 Phase 1: スキャン中...")
    analyzer = FolderAnalyzer(
        str(target),
        exclude_patterns=args.exclude,
        max_depth=args.max_depth,
    )
    files = analyzer.scan()
    print(f"   → {len(files)} ファイル検出")

    # Phase 2: 解析
    print("\n🔬 Phase 2: 解析中...")
    analyses = []
    for i, f in enumerate(files, 1):
        analysis = analyzer.analyze(f)
        analyses.append(analysis)
        if i % 10 == 0 or i == len(files):
            print(f"   → {i}/{len(files)} 完了")

    # Phase 3: レポート生成
    print("\n📝 Phase 3: レポート生成中...")
    report = AnalysisReport(analyses, str(target))

    # JSON出力
    json_path = output_dir / "report.json"
    json_path.write_text(report.to_json(), encoding="utf-8")
    print(f"   → JSON: {json_path}")

    # Markdownレポート出力
    if not args.json_only:
        md_path = output_dir / "report.md"
        md_path.write_text(report.to_markdown(), encoding="utf-8")
        print(f"   → Markdown: {md_path}")

    # ワークスペースモードではエージェントマップも生成
    if args.workspace:
        print("\n🗺️ Phase 2.5: エージェント構造マップ生成中...")
        builder = AgentMapBuilder(str(target))
        agent_json_path = output_dir / "agent_map.json"
        agent_json_path.write_text(builder.to_json(), encoding="utf-8")
        print(f"   → JSON: {agent_json_path}")

        if not args.json_only:
            agent_md_path = output_dir / "agent_map.md"
            agent_md_path.write_text(builder.to_markdown(), encoding="utf-8")
            print(f"   → Markdown: {agent_md_path}")

    # サマリー表示
    summary = report.get_summary()
    print(f"\n✅ 完了!")
    print(f"   ファイル数: {summary['total_files']}")
    print(f"   合計サイズ: {AnalysisReport._format_size(summary['total_size_bytes'])}")
    print(f"   合計行数: {summary['total_lines']:,}")
    print(f"   拡張子: {', '.join(summary['extensions'].keys())}")


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve()
    for _parent in _here.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in _sys.path:
                _sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(_run_logged_main("folder_check", "folder_analyzer", main, phase_name="FOLDER_ANALYZER_RUN"))


