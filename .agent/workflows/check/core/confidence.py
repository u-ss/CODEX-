# /check エージェント - confidence算出
"""参照確度の機械的算出モジュール"""

from enum import Enum
from typing import Optional


class ReferenceType(Enum):
    """参照の種類"""
    SCHEMA_FIELD = "schema_field"      # スキーマ定義フィールドから
    MARKDOWN_LINK = "markdown_link"    # Markdownリンク形式
    IMPORT_STATEMENT = "import"        # import/from文
    PATH_LITERAL = "path_literal"      # パス文字列リテラル
    REGEX_MATCH = "regex_match"        # regex推測
    HEURISTIC = "heuristic"            # ヒューリスティック推定


# 参照タイプごとのベース確度
CONFIDENCE_BASE = {
    ReferenceType.SCHEMA_FIELD: 1.0,
    ReferenceType.MARKDOWN_LINK: 0.8,
    ReferenceType.IMPORT_STATEMENT: 0.9,
    ReferenceType.PATH_LITERAL: 0.7,
    ReferenceType.REGEX_MATCH: 0.4,
    ReferenceType.HEURISTIC: 0.1,
}


def calculate_confidence(
    ref_type: ReferenceType,
    target_exists: bool = True,
    case_match: bool = True,
    extension_match: bool = True
) -> float:
    """参照確度を計算
    
    Args:
        ref_type: 参照の種類
        target_exists: 参照先が存在するか
        case_match: 大小文字が一致するか
        extension_match: 拡張子が一致するか
    
    Returns:
        0.0-1.0 の確度値
    """
    base = CONFIDENCE_BASE.get(ref_type, 0.5)
    
    # ペナルティ適用
    if not target_exists:
        base *= 0.3  # 存在しない場合は大幅減
    if not case_match:
        base *= 0.7  # 大小文字不一致
    if not extension_match:
        base *= 0.8  # 拡張子不一致
    
    return round(min(max(base, 0.0), 1.0), 2)


def detect_reference_type(source_context: str, raw_ref: str) -> ReferenceType:
    """参照のタイプを推定
    
    Args:
        source_context: 参照を含む行の内容
        raw_ref: 参照文字列
    
    Returns:
        推定された参照タイプ
    """
    # Markdownリンク形式
    if f"]({raw_ref})" in source_context or f"]: {raw_ref}" in source_context:
        return ReferenceType.MARKDOWN_LINK
    
    # import文
    if source_context.strip().startswith(("import ", "from ")):
        return ReferenceType.IMPORT_STATEMENT
    
    # YAML/JSONのパスフィールド（path, file, src等）
    path_keywords = ["path:", "file:", "src:", "source:", "target:", "ref:"]
    if any(kw in source_context.lower() for kw in path_keywords):
        return ReferenceType.SCHEMA_FIELD
    
    # クォートで囲まれたパス文字列
    if (f'"{raw_ref}"' in source_context or f"'{raw_ref}'" in source_context):
        return ReferenceType.PATH_LITERAL
    
    # それ以外はヒューリスティック
    return ReferenceType.HEURISTIC


def is_high_confidence(confidence: float) -> bool:
    """高確度かどうか判定（自動修正可能の目安）"""
    return confidence >= 0.7


def is_actionable(confidence: float) -> bool:
    """アクション可能な確度か判定（レポート対象）"""
    return confidence >= 0.3
