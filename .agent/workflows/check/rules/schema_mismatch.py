# /check エージェント - schema_mismatch ルール
"""スキーマ違反検出"""

from typing import List, Dict, Any, Optional
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.finding import Finding, Location, Severity


# スキーマ定義
SCHEMAS = {
    "workflow": {
        "required": ["description"],
        "types": {"description": str},
        "name_pattern": "WORKFLOW.md"
    },
    "skill": {
        "required": ["name", "description"],
        "types": {"name": str, "description": str},
        "name_pattern": "SKILL.md"
    }
}


def validate_schema(obj: Dict, schema: Dict) -> List[Dict]:
    """スキーマ検証"""
    violations = []
    
    if not isinstance(obj, dict):
        return [{"type": "not_dict", "actual": type(obj).__name__}]
    
    # 必須キーチェック
    for key in schema.get("required", []):
        if key not in obj:
            violations.append({"type": "missing_key", "key": key})
    
    # 型チェック
    for key, expected_type in schema.get("types", {}).items():
        if key in obj and not isinstance(obj[key], expected_type):
            violations.append({
                "type": "type_error",
                "key": key,
                "expected": expected_type.__name__,
                "actual": type(obj[key]).__name__
            })
    
    return violations


def pick_schema(file_path: str, obj: Any) -> Optional[Dict]:
    """ファイルに適したスキーマを選択"""
    fname = Path(file_path).name
    
    for schema_id, schema in SCHEMAS.items():
        if schema.get("name_pattern") == fname:
            return {"id": schema_id, **schema}
    
    return None


def detect_schema_mismatch(
    parsed_objects: List[Dict]
) -> List[Finding]:
    """スキーマ違反を検出
    
    Args:
        parsed_objects: [{file_path, obj}] のリスト
    
    Returns:
        検出されたFindingリスト
    """
    findings = []
    
    for po in parsed_objects:
        file_path = po.get("file_path", "")
        obj = po.get("obj")
        
        if obj is None:
            continue
        
        schema = pick_schema(file_path, obj)
        if not schema:
            continue
        
        violations = validate_schema(obj, schema)
        
        if violations:
            findings.append(Finding(
                rule_id="schema_mismatch",
                severity=Severity.HIGH,
                location=Location(file=file_path),
                evidence={
                    "schema_id": schema["id"],
                    "violations": violations
                },
                message=f"スキーマ違反: {len(violations)}件",
                suggestion="必須フィールドを追加または型を修正",
                autofix_allowed=False
            ))
    
    return findings
