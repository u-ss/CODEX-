# /check エージェント - path_escape ルール
"""パス脱出（ワークスペース外参照）の検出"""

from typing import List, Optional
from pathlib import Path, PureWindowsPath
from dataclasses import dataclass
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.finding import Finding, Location, Severity, Edge


@dataclass
class ResolvedPath:
    """パス解決結果"""
    raw: str
    status: str  # ok, escape, invalid
    normalized: Optional[str] = None
    reason: Optional[str] = None


def resolve_reference(
    base_file: str,
    raw_ref: str,
    root: str
) -> ResolvedPath:
    """参照パスを解決し、脱出をチェック
    
    Args:
        base_file: 参照元ファイルパス
        raw_ref: 参照文字列
        root: ワークスペースルート
    
    Returns:
        解決結果
    """
    # 絶対パス/UNC/チルダの即時検出
    if _looks_absolute(raw_ref):
        return ResolvedPath(raw=raw_ref, status="escape", reason="absolute_path")
    
    if raw_ref.startswith("~"):
        return ResolvedPath(raw=raw_ref, status="escape", reason="home_expansion")
    
    if raw_ref.startswith("\\\\"):
        return ResolvedPath(raw=raw_ref, status="escape", reason="unc_path")
    
    try:
        base = Path(base_file).parent
        norm = (base / raw_ref).resolve()
        root_resolved = Path(root).resolve()
        
        # ルート外かチェック
        try:
            norm.relative_to(root_resolved)
            return ResolvedPath(raw=raw_ref, status="ok", normalized=str(norm))
        except ValueError:
            return ResolvedPath(
                raw=raw_ref,
                status="escape",
                normalized=str(norm),
                reason="outside_root"
            )
    except Exception as e:
        return ResolvedPath(raw=raw_ref, status="invalid", reason=str(e))


def detect_path_escape(
    edges: List[Edge],
    root: str
) -> List[Finding]:
    """パス脱出を検出
    
    Args:
        edges: 依存グラフのエッジリスト
        root: ワークスペースルート
    
    Returns:
        検出されたFindingリスト
    """
    findings = []
    
    for edge in edges:
        result = resolve_reference(edge.src_file, edge.raw_target, root)
        
        if result.status == "escape":
            findings.append(Finding(
                rule_id="path_escape",
                severity=Severity.HIGH,
                location=Location(
                    file=edge.src_file,
                    line_range=edge.line_range
                ),
                evidence={
                    "raw_ref": edge.raw_target,
                    "normalized_ref": result.normalized,
                    "base_path": str(Path(edge.src_file).parent),
                    "escape_reason": result.reason
                },
                message=f"パスがワークスペース外を参照: {edge.raw_target}",
                suggestion="相対パスに修正するか参照を削除",
                autofix_allowed=False  # 安全のため自動修正不可
            ))
    
    return findings


def _looks_absolute(path: str) -> bool:
    """絶対パスかどうか判定"""
    # Unix絶対パス
    if path.startswith("/"):
        return True
    
    # Windows絶対パス（C:\, D:\ 等）
    if len(path) >= 2 and path[1] == ":":
        return True
    
    # Windowsドライブ相対（C:folder 等）
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        return True
    
    return False
