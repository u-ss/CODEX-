# -*- coding: utf-8 -*-
"""フォルダ解析エージェント テスト

folder_analyzer.py の主要機能をテストする。
"""

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# スクリプトのパスを追加
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "エージェント" / "フォルダ解析エージェント" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from folder_analyzer import (
    FileInfo,
    FileAnalysis,
    FolderAnalyzer,
    PythonAnalyzer,
    MarkdownAnalyzer,
    JsonYamlAnalyzer,
    GenericAnalyzer,
    AnalysisReport,
)


# ===== フィクスチャ =====

@pytest.fixture
def tmp_project(tmp_path):
    """テスト用の一時プロジェクトフォルダを作成"""
    # Pythonファイル
    py_file = tmp_path / "sample.py"
    py_file.write_text(textwrap.dedent("""\
        import os
        import json
        from pathlib import Path

        class MyClass:
            \"\"\"サンプルクラス\"\"\"
            def method_a(self):
                pass

            def method_b(self, x: int) -> str:
                return str(x)

        def standalone_func(name: str):
            \"\"\"スタンドアロン関数\"\"\"
            print(name)
    """), encoding="utf-8")

    # Markdownファイル
    md_file = tmp_path / "README.md"
    md_file.write_text(textwrap.dedent("""\
        # プロジェクト名

        ## 概要
        これはサンプルです。

        ### 機能一覧
        - 機能A
        - 機能B

        ## インストール
        `pip install sample`

        [リンク](https://example.com)
    """), encoding="utf-8")

    # JSONファイル
    json_file = tmp_path / "config.json"
    json_file.write_text(json.dumps({
        "name": "test",
        "version": "1.0.0",
        "settings": {
            "debug": True,
            "log_level": "INFO",
            "nested": {"key": "value"}
        },
        "items": [1, 2, 3]
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # YAMLファイル
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(textwrap.dedent("""\
        name: test
        version: 1.0.0
        settings:
          debug: true
          log_level: INFO
    """), encoding="utf-8")

    # サブディレクトリ
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    sub_py = sub_dir / "helper.py"
    sub_py.write_text(textwrap.dedent("""\
        def helper_func():
            return True
    """), encoding="utf-8")

    # 空ファイル
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")

    return tmp_path


# ===== FolderAnalyzer.scan テスト =====

class TestScan:
    """フォルダスキャン機能のテスト"""

    def test_scan_basic(self, tmp_project):
        """基本的なスキャンでファイル一覧取得"""
        analyzer = FolderAnalyzer(str(tmp_project))
        files = analyzer.scan()
        assert len(files) >= 5  # sample.py, README.md, config.json, config.yaml, helper.py, empty.txt
        assert all(isinstance(f, FileInfo) for f in files)

    def test_scan_returns_file_info(self, tmp_project):
        """FileInfoにサイズ・拡張子が含まれる"""
        analyzer = FolderAnalyzer(str(tmp_project))
        files = analyzer.scan()
        py_files = [f for f in files if f.name == "sample.py"]
        assert len(py_files) == 1
        info = py_files[0]
        assert info.extension == ".py"
        assert info.size > 0

    def test_scan_recursive(self, tmp_project):
        """サブディレクトリ内のファイルも走査される"""
        analyzer = FolderAnalyzer(str(tmp_project))
        files = analyzer.scan()
        names = [f.name for f in files]
        assert "helper.py" in names

    def test_scan_exclude_pattern(self, tmp_project):
        """除外パターンが機能する"""
        analyzer = FolderAnalyzer(str(tmp_project), exclude_patterns=["*.txt"])
        files = analyzer.scan()
        names = [f.name for f in files]
        assert "empty.txt" not in names

    def test_scan_max_depth(self, tmp_project):
        """max_depthによる深さ制限"""
        analyzer = FolderAnalyzer(str(tmp_project), max_depth=0)
        files = analyzer.scan()
        names = [f.name for f in files]
        # サブディレクトリは含まれない
        assert "helper.py" not in names


# ===== PythonAnalyzer テスト =====

class TestPythonAnalyzer:
    """Python解析のテスト"""

    def test_extract_classes(self, tmp_project):
        """クラスを正しく抽出"""
        py_analyzer = PythonAnalyzer()
        result = py_analyzer.analyze(tmp_project / "sample.py")
        assert "MyClass" in [c["name"] for c in result.classes]

    def test_extract_functions(self, tmp_project):
        """トップレベル関数を正しく抽出"""
        py_analyzer = PythonAnalyzer()
        result = py_analyzer.analyze(tmp_project / "sample.py")
        func_names = [f["name"] for f in result.functions]
        assert "standalone_func" in func_names

    def test_extract_imports(self, tmp_project):
        """importを正しく抽出"""
        py_analyzer = PythonAnalyzer()
        result = py_analyzer.analyze(tmp_project / "sample.py")
        assert "os" in result.imports
        assert "json" in result.imports

    def test_line_count(self, tmp_project):
        """行数が正しい"""
        py_analyzer = PythonAnalyzer()
        result = py_analyzer.analyze(tmp_project / "sample.py")
        assert result.line_count > 0


# ===== MarkdownAnalyzer テスト =====

class TestMarkdownAnalyzer:
    """Markdown解析のテスト"""

    def test_extract_headings(self, tmp_project):
        """見出し構造を抽出"""
        md_analyzer = MarkdownAnalyzer()
        result = md_analyzer.analyze(tmp_project / "README.md")
        heading_texts = [h["text"] for h in result.headings]
        assert "プロジェクト名" in heading_texts
        assert "概要" in heading_texts

    def test_extract_links(self, tmp_project):
        """リンクを抽出"""
        md_analyzer = MarkdownAnalyzer()
        result = md_analyzer.analyze(tmp_project / "README.md")
        assert len(result.links) >= 1
        assert any("example.com" in link["url"] for link in result.links)

    def test_heading_levels(self, tmp_project):
        """見出しレベルが正しい"""
        md_analyzer = MarkdownAnalyzer()
        result = md_analyzer.analyze(tmp_project / "README.md")
        h1 = [h for h in result.headings if h["level"] == 1]
        h2 = [h for h in result.headings if h["level"] == 2]
        assert len(h1) >= 1
        assert len(h2) >= 1


# ===== JsonYamlAnalyzer テスト =====

class TestJsonYamlAnalyzer:
    """JSON/YAML解析のテスト"""

    def test_json_keys(self, tmp_project):
        """JSONのトップレベルキーを抽出"""
        analyzer = JsonYamlAnalyzer()
        result = analyzer.analyze(tmp_project / "config.json")
        assert "name" in result.top_keys
        assert "settings" in result.top_keys

    def test_json_nested_structure(self, tmp_project):
        """JSONのネスト構造を検出"""
        analyzer = JsonYamlAnalyzer()
        result = analyzer.analyze(tmp_project / "config.json")
        # settingsがネストされていることを検出
        assert result.max_depth >= 2

    def test_yaml_keys(self, tmp_project):
        """YAMLのトップレベルキーを抽出"""
        analyzer = JsonYamlAnalyzer()
        result = analyzer.analyze(tmp_project / "config.yaml")
        assert "name" in result.top_keys
        assert "settings" in result.top_keys


# ===== AnalysisReport テスト =====

class TestReport:
    """レポート生成のテスト"""

    def test_generate_json_report(self, tmp_project):
        """JSONレポートが生成される"""
        analyzer = FolderAnalyzer(str(tmp_project))
        files = analyzer.scan()
        analyses = [analyzer.analyze(f) for f in files]
        report = AnalysisReport(analyses, str(tmp_project))
        json_output = report.to_json()
        data = json.loads(json_output)
        assert "summary" in data
        assert "files" in data
        assert data["summary"]["total_files"] >= 5

    def test_generate_markdown_report(self, tmp_project):
        """Markdownレポートが生成される"""
        analyzer = FolderAnalyzer(str(tmp_project))
        files = analyzer.scan()
        analyses = [analyzer.analyze(f) for f in files]
        report = AnalysisReport(analyses, str(tmp_project))
        md_output = report.to_markdown()
        assert "# フォルダ解析レポート" in md_output
        assert "sample.py" in md_output

    def test_summary_stats(self, tmp_project):
        """サマリー統計が正しい"""
        analyzer = FolderAnalyzer(str(tmp_project))
        files = analyzer.scan()
        analyses = [analyzer.analyze(f) for f in files]
        report = AnalysisReport(analyses, str(tmp_project))
        summary = report.get_summary()
        assert summary["total_files"] >= 5
        assert ".py" in summary["extensions"]
