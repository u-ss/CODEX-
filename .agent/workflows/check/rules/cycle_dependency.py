# /check エージェント - cycle_dependency ルール
"""循環依存検出"""

from typing import List, Dict, Set, Tuple, Any
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.finding import Finding, Location, Severity, Edge


# 実行順序に影響するエッジタイプ
CRITICAL_EDGE_TYPES = {"import", "extends", "include"}


def find_cycles(graph: Dict[str, List[Tuple[str, Edge]]]) -> List[List[Tuple[str, str, Edge]]]:
    """DFSでサイクルを検出
    
    Args:
        graph: {node: [(neighbor, edge), ...]} 形式の隣接リスト
    
    Returns:
        検出されたサイクルのリスト
    """
    visited: Set[str] = set()
    stack: Set[str] = set()
    cycles: List[List[Tuple[str, str, Edge]]] = []
    
    def dfs(node: str, path: List[Tuple[str, str, Edge]]):
        visited.add(node)
        stack.add(node)
        
        for neighbor, edge in graph.get(node, []):
            if neighbor not in visited:
                dfs(neighbor, path + [(node, neighbor, edge)])
            elif neighbor in stack:
                # サイクル検出
                cycle = _extract_cycle(path + [(node, neighbor, edge)], neighbor)
                if cycle:
                    cycles.append(cycle)
        
        stack.remove(node)
    
    for node in graph:
        if node not in visited:
            dfs(node, [])
    
    return cycles


def _extract_cycle(
    path: List[Tuple[str, str, Edge]],
    start: str
) -> List[Tuple[str, str, Edge]]:
    """パスからサイクル部分を抽出"""
    cycle = []
    in_cycle = False
    
    for src, dst, edge in path:
        if src == start:
            in_cycle = True
        if in_cycle:
            cycle.append((src, dst, edge))
    
    return cycle


def detect_cycle_dependency(
    graph: Dict[str, List[Tuple[str, Edge]]]
) -> List[Finding]:
    """循環依存を検出
    
    Args:
        graph: 依存グラフ（隣接リスト形式）
    
    Returns:
        検出されたFindingリスト
    """
    findings = []
    cycles = find_cycles(graph)
    
    for cyc in cycles:
        edge_types = [e.edge_type for (_, _, e) in cyc]
        
        # 実行順序に影響する循環は HIGH
        is_critical = any(t in CRITICAL_EDGE_TYPES for t in edge_types)
        severity = Severity.HIGH if is_critical else Severity.MEDIUM
        
        # 最小confidence
        min_conf = min((e.confidence for (_, _, e) in cyc), default=1.0)
        
        # 循環パスを文字列化
        cycle_path = " → ".join([src for (src, _, _) in cyc])
        if cyc:
            cycle_path += f" → {cyc[0][0]}"  # 始点に戻る
        
        findings.append(Finding(
            rule_id="cycle_dependency",
            severity=severity,
            location=Location(file=cyc[0][0] if cyc else "unknown"),
            evidence={
                "cycle_path": cycle_path,
                "edge_types": edge_types,
                "confidence_min": min_conf,
                "cycle_length": len(cyc)
            },
            message=f"循環依存を検出: {len(cyc)}ノード",
            suggestion="循環を解消するためにどのエッジを切るか検討",
            autofix_allowed=False
        ))
    
    return findings
