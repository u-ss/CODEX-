"""
model_self_review.py - 生成済み3D成果物の自動自己レビュー

責務:
- 成果物ファイルの存在/サイズ/重複を検査
- validation JSON の pass/score/critical_failures を再判定
- 実運用向けの簡易レビュー結果を run_report へ返す
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _hash_file(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_validation(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def build_self_review(
    final_artifacts: Dict[str, Any],
    validation_payload: Optional[Dict[str, Any]] = None,
    score_threshold: float = 82.0,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    recommendations: List[str] = []
    score = 100.0

    blend = Path(str(final_artifacts.get("blend", "")))
    front = Path(str(final_artifacts.get("front", "")))
    oblique = Path(str(final_artifacts.get("oblique", "")))
    bird = Path(str(final_artifacts.get("bird", "")))
    validation_path = Path(str(final_artifacts.get("validation", "")))

    # 1) mandatory artifacts
    for name, path, min_bytes in (
        ("blend", blend, 2048),
        ("front", front, 12000),
        ("oblique", oblique, 12000),
        ("bird", bird, 12000),
        ("validation", validation_path, 128),
    ):
        ok = path.exists() and path.is_file() and path.stat().st_size >= min_bytes
        checks.append(
            {
                "name": f"artifact:{name}",
                "pass": ok,
                "detail": str(path),
                "bytes": int(path.stat().st_size) if path.exists() and path.is_file() else 0,
            }
        )
        if not ok:
            score -= 20.0 if name == "blend" else 8.0
            recommendations.append(f"{name} 成果物を再生成してください")

    # 2) render duplicate detection
    render_hashes = {
        "front": _hash_file(front),
        "oblique": _hash_file(oblique),
        "bird": _hash_file(bird),
    }
    valid_hashes = [v for v in render_hashes.values() if v]
    duplicate = len(valid_hashes) >= 2 and len(set(valid_hashes)) != len(valid_hashes)
    checks.append(
        {
            "name": "render_uniqueness",
            "pass": not duplicate,
            "detail": "ok" if not duplicate else "render outputs contain identical images",
        }
    )
    if duplicate:
        score -= 18.0
        recommendations.append("カメラ位置または被写体構成を見直してください")

    # 3) validation payload
    if not isinstance(validation_payload, dict):
        validation_payload = _load_validation(validation_path)

    val_pass = bool(validation_payload.get("pass")) if isinstance(validation_payload, dict) else False
    val_score = _to_float(validation_payload.get("score") if isinstance(validation_payload, dict) else None, 0.0)
    critical_failures = validation_payload.get("critical_failures", []) if isinstance(validation_payload, dict) else []
    if not isinstance(critical_failures, list):
        critical_failures = []

    checks.append({"name": "validation_pass", "pass": val_pass, "detail": f"pass={val_pass}"})
    if not val_pass:
        score -= 20.0
        recommendations.append("validation の失敗項目を修正して再生成してください")

    checks.append(
        {
            "name": "validation_score",
            "pass": val_score >= float(score_threshold),
            "detail": f"actual={val_score:.2f}, threshold={float(score_threshold):.2f}",
        }
    )
    if val_score < float(score_threshold):
        score -= 15.0
        recommendations.append("品質スコアが閾値未満です。修正ループを増やしてください")

    checks.append(
        {
            "name": "critical_failures",
            "pass": len(critical_failures) == 0,
            "detail": "ok" if not critical_failures else "; ".join(str(x) for x in critical_failures[:5]),
        }
    )
    if critical_failures:
        score -= 20.0
        recommendations.append("critical failures を解消してください")

    score = max(0.0, min(100.0, score))
    review_pass = all(bool(item.get("pass")) for item in checks)
    return {
        "score": round(score, 2),
        "pass": review_pass,
        "checks": checks,
        "recommendations": recommendations,
    }
