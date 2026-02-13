# /check エージェント - dangling_reference ルール
"""参照解決失敗の検出"""

from typing import List, Optional
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.finding import Finding, Location, Severity, Edge


def detect_dangling_reference(
    edges: List[Edge],
    existing_files: List[str]
) -> List[Finding]:
    """参照解決失敗を検出
    
    Args:
        edges: 依存グラフのエッジリスト
        existing_files: 存在するファイルのリスト
    
    Returns:
        検出されたFindingリスト
    """
    findings = []
    
    # 正規化されたファイルパスのセット
    normalized_files = {_normalize_path(f) for f in existing_files}
    
    for edge in edges:
        target_normalized = _normalize_path(edge.dst_file)
        
        if target_normalized not in normalized_files:
            # 類似ファイルを検索（大小文字違い等）
            similar = _find_similar(edge.dst_file, existing_files)
            
            findings.append(Finding(
                rule_id="dangling_reference",
                severity=Severity.HIGH if edge.confidence >= 0.7 else Severity.MEDIUM,
                location=Location(
                    file=edge.src_file,
                    line_range=edge.line_range
                ),
                evidence={
                    "raw_target": edge.raw_target,
                    "resolved_target": edge.dst_file,
                    "edge_type": edge.edge_type,
                    "confidence": edge.confidence,
                    "similar_files": similar[:3]  # 上位3件
                },
                message=f"参照先が見つからない: {edge.raw_target}",
                suggestion=f"類似: {', '.join(similar[:3])}" if similar else "参照を削除または修正",
                autofix_allowed=len(similar) == 1 and edge.confidence >= 0.7,
                confidence=edge.confidence
            ))
    
    return findings


def _normalize_path(path: str) -> str:
    """パスを正規化（小文字化、区切り文字統一）"""
    return Path(path).as_posix().lower()


def _find_similar(target: str, candidates: List[str]) -> List[str]:
    """類似ファイルを検索"""
    target_name = Path(target).name.lower()
    target_stem = Path(target).stem.lower()
    
    similar = []
    for c in candidates:
        c_name = Path(c).name.lower()
        c_stem = Path(c).stem.lower()
        
        # 完全一致（大小文字違い）
        if c_name == target_name:
            similar.insert(0, c)
        # 拡張子違い
        elif c_stem == target_stem:
            similar.append(c)
        # 部分一致
        elif target_stem in c_name or c_stem in target_name:
            similar.append(c)
    
    return similar
