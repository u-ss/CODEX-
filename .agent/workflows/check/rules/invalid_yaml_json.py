# /check エージェント - invalid_yaml_json ルール
"""YAML/JSON/frontmatter パースエラー検出"""

from typing import List, Tuple, Optional, Any
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.finding import Finding, Location, Severity, ParseError

# YAMLは任意依存
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def parse_file(path: str) -> Tuple[str, Optional[Any], Optional[ParseError]]:
    """ファイルをパースし、エラーを検出
    
    Returns:
        (parser_type, parsed_obj, error)
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return ("read_error", None, ParseError(path=path, msg=str(e)))
    
    try:
        # JSON
        if path.endswith(".json"):
            return ("json", json.loads(text), None)
        
        # YAML
        if path.endswith((".yml", ".yaml")):
            if HAS_YAML:
                return ("yaml", yaml.safe_load(text), None)
            return ("yaml", None, None)  # YAMLライブラリなし
        
        # Markdown frontmatter
        if path.endswith(".md") and text.startswith("---"):
            fm, _ = _split_frontmatter(text)
            if fm and HAS_YAML:
                return ("frontmatter", yaml.safe_load(fm), None)
            return ("frontmatter", None, None)
        
        return ("other", None, None)
        
    except json.JSONDecodeError as e:
        return ("json", None, ParseError(
            path=path,
            msg=str(e),
            line=e.lineno,
            snippet=_extract_snippet(text, e.lineno)
        ))
    except Exception as e:
        line = _extract_line_from_error(e)
        return (_guess_type(path), None, ParseError(
            path=path,
            msg=str(e)[:200],
            line=line,
            snippet=_extract_snippet(text, line) if line else ""
        ))


def detect_invalid_yaml_json(files: List[str]) -> List[Finding]:
    """パースエラーを検出
    
    Args:
        files: チェック対象ファイルリスト
    
    Returns:
        検出されたFindingリスト
    """
    findings = []
    
    for f in files:
        # 対象拡張子のみ
        if not f.endswith((".json", ".yml", ".yaml", ".md")):
            continue
        
        ptype, obj, err = parse_file(f)
        
        if err:
            findings.append(Finding(
                rule_id="invalid_yaml_json",
                severity=Severity.HIGH,
                location=Location(file=f, line=err.line),
                evidence={
                    "parser_type": ptype,
                    "error_message": err.msg[:200],
                    "error_line": err.line,
                    "snippet": err.snippet[:100] if err.snippet else ""
                },
                message=f"パースエラー: {err.msg[:50]}",
                suggestion="構文を修正してください",
                autofix_allowed=False
            ))
    
    return findings


def _split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """Markdown frontmatterを分離"""
    if not text.startswith("---"):
        return None, text
    
    lines = text.split("\n")
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break
    
    if end_idx is None:
        return None, text
    
    fm = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])
    return fm, body


def _extract_snippet(text: str, line: Optional[int], context: int = 2) -> str:
    """該当行周辺を抽出"""
    if line is None:
        return ""
    lines = text.split("\n")
    start = max(0, line - context - 1)
    end = min(len(lines), line + context)
    return "\n".join(lines[start:end])


def _extract_line_from_error(e: Exception) -> Optional[int]:
    """例外からエラー行を抽出"""
    # YAML等のエラーから行番号を抽出
    msg = str(e)
    if "line" in msg.lower():
        import re
        match = re.search(r"line\s*(\d+)", msg, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _guess_type(path: str) -> str:
    """パスから種類を推測"""
    if path.endswith(".json"):
        return "json"
    if path.endswith((".yml", ".yaml")):
        return "yaml"
    if path.endswith(".md"):
        return "frontmatter"
    return "unknown"
