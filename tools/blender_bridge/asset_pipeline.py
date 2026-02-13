"""
asset_pipeline.py - 外部アセット候補の選定とライセンスガード

実運用での優先事項:
- 利用不可ライセンスの混入を防ぐ
- 存在しないパスを除外する
- 選定ロジックを run_report に残せるようにする
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_ALLOW_LICENSES = ("CC0", "CC-BY", "ROYALTYFREE", "INTERNAL")
DEFAULT_DENY_LICENSES = ("NON_COMMERCIAL", "ARR", "GPL", "UNKNOWN")
SUPPORTED_FORMATS = (".blend", ".obj", ".fbx", ".glb", ".gltf")


@dataclass
class AssetCandidate:
    asset_id: str
    path: Path
    license_name: str
    domains: List[str]
    tags: List[str]
    fmt: str
    quality_score: float
    source: str


@dataclass
class AssetRejection:
    asset_id: str
    reason: str


@dataclass
class AssetSelectionResult:
    selected: List[Dict[str, Any]]
    rejected: List[Dict[str, str]]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _to_upper_set(values: Iterable[str]) -> set[str]:
    out = set()
    for value in values:
        token = str(value).strip().upper()
        if token:
            out.add(token)
    return out


def parse_license_policy(allow_csv: Optional[str], deny_csv: Optional[str]) -> Tuple[set[str], set[str]]:
    allow = _to_upper_set((allow_csv or "").split(",")) if allow_csv else set(DEFAULT_ALLOW_LICENSES)
    deny = _to_upper_set((deny_csv or "").split(",")) if deny_csv else set(DEFAULT_DENY_LICENSES)
    return allow, deny


def load_asset_manifest(path: Path) -> List[AssetCandidate]:
    payload = _read_json(path)
    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        return []

    out: List[AssetCandidate] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id", "")).strip()
        raw_path = str(item.get("path", "")).strip()
        license_name = str(item.get("license", "")).strip()
        if not asset_id or not raw_path or not license_name:
            continue

        candidate_path = Path(raw_path).expanduser()
        if not candidate_path.is_absolute():
            candidate_path = (path.parent / candidate_path).resolve()

        domains = [str(v).strip().lower() for v in (item.get("domains") or []) if str(v).strip()]
        tags = [str(v).strip().lower() for v in (item.get("tags") or []) if str(v).strip()]
        fmt = str(item.get("format", "")).strip().lower()
        if not fmt:
            fmt = candidate_path.suffix.lower()
        quality_score = float(item.get("quality_score", 0.5) or 0.5)
        source = str(item.get("source", "")).strip() or "unknown"

        out.append(
            AssetCandidate(
                asset_id=asset_id,
                path=candidate_path,
                license_name=license_name,
                domains=domains,
                tags=tags,
                fmt=fmt,
                quality_score=max(0.0, min(1.0, quality_score)),
                source=source,
            )
        )
    return out


def _score_candidate(candidate: AssetCandidate, domain: str, style_tags: Sequence[str], prompt: str) -> float:
    score = candidate.quality_score * 60.0

    if domain in candidate.domains:
        score += 25.0
    elif not candidate.domains:
        score += 10.0

    style = {s.lower() for s in style_tags}
    matched_tags = len([t for t in candidate.tags if t in style])
    score += float(matched_tags) * 5.0

    lower_prompt = prompt.lower()
    prompt_matches = len([t for t in candidate.tags if t and t in lower_prompt])
    score += float(min(prompt_matches, 4)) * 2.5

    if candidate.fmt in SUPPORTED_FORMATS:
        score += 5.0
    return score


def _default_asset_placement(index: int) -> Dict[str, List[float]]:
    row = index // 3
    col = index % 3
    spacing = 2.8
    x = (col - 1) * spacing
    y = (row + 1) * spacing
    return {
        "location": [float(x), float(y), 0.0],
        "rotation_deg": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
    }


def select_assets(
    spec: Dict[str, Any],
    catalog: Sequence[AssetCandidate],
    allow_licenses: set[str],
    deny_licenses: set[str],
    max_assets: int = 4,
) -> AssetSelectionResult:
    domain = str(spec.get("domain", "prop")).lower()
    style_tags = spec.get("composition", {}).get("style_tags", []) if isinstance(spec.get("composition"), dict) else []
    prompt = str(spec.get("source_prompt", ""))

    ranked: List[Tuple[float, AssetCandidate]] = []
    rejected: List[AssetRejection] = []

    for candidate in catalog:
        license_upper = candidate.license_name.strip().upper()
        if license_upper in deny_licenses:
            rejected.append(AssetRejection(candidate.asset_id, f"license denied: {candidate.license_name}"))
            continue
        if allow_licenses and license_upper not in allow_licenses:
            rejected.append(AssetRejection(candidate.asset_id, f"license not in allow list: {candidate.license_name}"))
            continue
        if not candidate.path.exists():
            rejected.append(AssetRejection(candidate.asset_id, "file not found"))
            continue
        if candidate.fmt not in SUPPORTED_FORMATS:
            rejected.append(AssetRejection(candidate.asset_id, f"unsupported format: {candidate.fmt}"))
            continue

        score = _score_candidate(candidate, domain=domain, style_tags=style_tags, prompt=prompt)
        ranked.append((score, candidate))

    ranked.sort(key=lambda item: item[0], reverse=True)
    selected: List[Dict[str, Any]] = []

    for index, (_, candidate) in enumerate(ranked[: max(0, int(max_assets))]):
        placement = _default_asset_placement(index)
        selected.append(
            {
                "id": candidate.asset_id,
                "path": str(candidate.path),
                "license": candidate.license_name,
                "format": candidate.fmt,
                "domains": candidate.domains,
                "tags": candidate.tags,
                "quality_score": candidate.quality_score,
                "source": candidate.source,
                "location": placement["location"],
                "rotation_deg": placement["rotation_deg"],
                "scale": placement["scale"],
            }
        )

    rejected_payload = [{"id": item.asset_id, "reason": item.reason} for item in rejected]
    return AssetSelectionResult(selected=selected, rejected=rejected_payload)


def apply_asset_selection_to_spec(spec: Dict[str, Any], selection: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    next_spec = json.loads(json.dumps(spec))
    composition = next_spec.get("composition")
    if not isinstance(composition, dict):
        composition = {}
        next_spec["composition"] = composition
    composition["selected_assets"] = list(selection)

    constraints = next_spec.get("target_constraints")
    if not isinstance(constraints, dict):
        constraints = {}
        next_spec["target_constraints"] = constraints

    current_min_objects = int(constraints.get("min_objects", 1) or 1)
    constraints["min_objects"] = max(current_min_objects, 1 + len(selection))
    return next_spec


def build_asset_report(
    manifest_path: Optional[Path],
    allow_licenses: set[str],
    deny_licenses: set[str],
    selection: AssetSelectionResult,
) -> Dict[str, Any]:
    return {
        "manifest": str(manifest_path) if manifest_path else "",
        "allow_licenses": sorted(allow_licenses),
        "deny_licenses": sorted(deny_licenses),
        "selected_count": len(selection.selected),
        "rejected_count": len(selection.rejected),
        "selected": selection.selected,
        "rejected": selection.rejected,
    }
