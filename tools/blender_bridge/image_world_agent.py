"""
image_world_agent.py - 参照画像からワールド生成する専用オーケストレーター

実行例:
    python tools/blender_bridge/image_world_agent.py \
      --prompt "dark and darker風のボス部屋を作って" \
      --reference-images ".\\refs\\boss_room_front.png,.\\refs\\boss_room_side.png"
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from blender_cli import DEFAULT_BLENDER_EXE
from image_world_spec import normalize_image_world_spec, parse_reference_images_arg, to_universal_form_data
from model_self_review import build_self_review
from universal_spec import validate_asset_spec


def validate_against_contract(data: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    required = schema.get("required", [])
    props = schema.get("properties", {})

    for key in required:
        if key not in data:
            errors.append(f"missing required key: {key}")

    for key, rule in props.items():
        if key not in data:
            continue
        value = data[key]
        typ = rule.get("type")
        if typ == "string" and not isinstance(value, str):
            errors.append(f"{key}: expected string")
            continue
        if typ == "integer" and not isinstance(value, int):
            errors.append(f"{key}: expected integer")
            continue
        if typ == "number" and not isinstance(value, (int, float)):
            errors.append(f"{key}: expected number")
            continue
        if typ == "boolean" and not isinstance(value, bool):
            errors.append(f"{key}: expected boolean")
            continue
        if typ == "array" and not isinstance(value, list):
            errors.append(f"{key}: expected array")
            continue
        if typ == "object" and not isinstance(value, dict):
            errors.append(f"{key}: expected object")
            continue

        if "enum" in rule and value not in rule["enum"]:
            errors.append(f"{key}: value '{value}' not in enum")

        if isinstance(value, (int, float)):
            if "minimum" in rule and value < rule["minimum"]:
                errors.append(f"{key}: value {value} < minimum {rule['minimum']}")
            if "maximum" in rule and value > rule["maximum"]:
                errors.append(f"{key}: value {value} > maximum {rule['maximum']}")

        if isinstance(value, list):
            if "minItems" in rule and len(value) < int(rule["minItems"]):
                errors.append(f"{key}: item count {len(value)} < minItems {rule['minItems']}")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="画像からワールドを作って")
    parser.add_argument("--reference-images", default="")
    parser.add_argument("--form-json", default="")
    parser.add_argument("--work-dir", default="ag_runs")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--blender-exe", default=DEFAULT_BLENDER_EXE)
    parser.add_argument("--score-threshold", type=float, default=82.0)

    parser.add_argument("--asset-manifest", default="")
    parser.add_argument("--allow-licenses", default="")
    parser.add_argument("--deny-licenses", default="")
    parser.add_argument("--max-assets", type=int, default=4)

    parser.add_argument("--open-gui", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--live-host", default="127.0.0.1")
    parser.add_argument("--live-port", type=int, default=8765)
    parser.add_argument("--live-token", default="")
    parser.add_argument("--live-timeout", type=float, default=45.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_form_data(form_json_arg: str) -> Dict[str, Any]:
    if not form_json_arg:
        return {}
    candidate = Path(form_json_arg)
    if candidate.exists():
        return _read_json(candidate)
    try:
        payload = json.loads(form_json_arg)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    raise ValueError(f"--form-json が不正です: {form_json_arg}")


def _find_latest_subdir(root: Path) -> Optional[Path]:
    subdirs = [p for p in root.glob("*") if p.is_dir()]
    if not subdirs:
        return None
    subdirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return subdirs[0]


def _resolve_path(raw_path: str, base_dir: Path) -> Path:
    p = Path(str(raw_path))
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


def _copy_if_exists(src: Path, dst: Path, notes: List[str]) -> None:
    if src.exists() and src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    else:
        notes.append(f"missing artifact: {src}")


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    universal_agent = base_dir / "universal_agent.py"
    spec_contract_path = base_dir / "contracts" / "asset_spec.schema.json"

    if not universal_agent.exists():
        print("[AG] universal_agent.py not found", file=sys.stderr)
        return 1
    if not spec_contract_path.exists():
        print("[AG] asset_spec.schema.json not found", file=sys.stderr)
        return 1

    try:
        form_data = _load_form_data(args.form_json)
    except Exception as exc:
        print(f"[AG] form parse error: {exc}", file=sys.stderr)
        return 1

    ref_from_form = form_data.get("reference_images", [])
    ref_from_arg = parse_reference_images_arg(args.reference_images)
    reference_images = ref_from_arg if ref_from_arg else parse_reference_images_arg(ref_from_form)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.work_dir).resolve() / f"image_world_agent_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    spec = normalize_image_world_spec(
        prompt=args.prompt,
        form_data=form_data,
        reference_images=reference_images,
        preset=None,
    )
    spec_contract = _read_json(spec_contract_path)
    spec_errors = validate_asset_spec(spec)
    spec_errors.extend(validate_against_contract(spec, spec_contract))
    if spec_errors:
        print("[AG] image world spec validation error:", file=sys.stderr)
        for err in spec_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    normalized_spec_path = run_dir / "image_world_spec_normalized.json"
    _write_json(normalized_spec_path, spec)
    delegate_form = to_universal_form_data(spec)
    delegate_form_path = run_dir / "delegate_form.json"
    _write_json(delegate_form_path, delegate_form)

    run_report: Dict[str, Any] = {
        "status": "RUNNING",
        "mode": "image_world",
        "prompt": args.prompt,
        "run_dir": str(run_dir),
        "max_iterations": int(max(1, args.max_iterations)),
        "score_threshold": float(max(0.0, min(100.0, args.score_threshold))),
        "reference_images": spec.get("reference_images", []),
        "reference_analysis": spec.get("reference_analysis", {}),
        "assumptions": list(spec.get("assumptions", [])),
        "notes": [],
    }

    if args.dry_run:
        run_report["status"] = "DRY_RUN"
        run_report["notes"].append("dry-run enabled: delegate universal run was skipped")
        run_report["spec"] = str(normalized_spec_path)
        run_report["delegate_form"] = str(delegate_form_path)
        report_path = run_dir / "run_report.json"
        _write_json(report_path, run_report)
        print(f"[AG] status={run_report['status']}")
        print(f"[AG] spec={normalized_spec_path}")
        print(f"[AG] report={report_path}")
        return 0

    delegate_root = run_dir / "universal_delegate_runs"
    delegate_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(universal_agent),
        "--prompt",
        args.prompt,
        "--form-json",
        str(delegate_form_path),
        "--domain",
        "scene",
        "--work-dir",
        str(delegate_root),
        "--max-iterations",
        str(int(max(1, args.max_iterations))),
        "--blender-exe",
        str(args.blender_exe),
        "--score-threshold",
        str(float(max(0.0, min(100.0, args.score_threshold)))),
        "--no-prefer-house-agent",
        "--no-prefer-character-agent",
    ]
    if args.samples is not None:
        cmd.extend(["--samples", str(int(args.samples))])
    if args.asset_manifest:
        cmd.extend(["--asset-manifest", str(args.asset_manifest)])
    if args.allow_licenses:
        cmd.extend(["--allow-licenses", str(args.allow_licenses)])
    if args.deny_licenses:
        cmd.extend(["--deny-licenses", str(args.deny_licenses)])
    if args.max_assets is not None:
        cmd.extend(["--max-assets", str(int(args.max_assets))])
    if args.open_gui:
        cmd.append("--open-gui")
    if args.interactive:
        cmd.append("--interactive")
        cmd.extend(
            [
                "--live-host",
                str(args.live_host),
                "--live-port",
                str(args.live_port),
                "--live-token",
                str(args.live_token),
                "--live-timeout",
                str(args.live_timeout),
            ]
        )

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    run_report["delegate_returncode"] = int(proc.returncode)
    run_report["delegate_cmd"] = " ".join(cmd)
    run_report["delegate_stdout_tail"] = proc.stdout[-5000:]
    run_report["delegate_stderr_tail"] = proc.stderr[-5000:]

    latest = _find_latest_subdir(delegate_root)
    if latest is None:
        run_report["status"] = "FAILED"
        run_report["notes"].append("delegate universal run directory not found")
        report_path = run_dir / "run_report.json"
        _write_json(report_path, run_report)
        print(f"[AG] status={run_report['status']}")
        print(f"[AG] report={report_path}")
        return 1

    delegate_report_path = latest / "run_report.json"
    if not delegate_report_path.exists():
        run_report["status"] = "FAILED"
        run_report["notes"].append(f"delegate report missing: {delegate_report_path}")
        report_path = run_dir / "run_report.json"
        _write_json(report_path, run_report)
        print(f"[AG] status={run_report['status']}")
        print(f"[AG] report={report_path}")
        return 1

    delegate_report = _read_json(delegate_report_path)
    run_report["delegate_run_dir"] = str(latest)
    run_report["delegate_status"] = str(delegate_report.get("status", "UNKNOWN"))
    run_report["iterations"] = delegate_report.get("iterations", [])
    run_report["final_iteration"] = delegate_report.get("final_iteration")
    run_report["final_score"] = delegate_report.get("final_score")
    run_report["notes"].extend(delegate_report.get("notes", []) if isinstance(delegate_report.get("notes"), list) else [])

    src_artifacts = delegate_report.get("final_artifacts", {}) if isinstance(delegate_report, dict) else {}
    final_blend = run_dir / "final.blend"
    final_front = run_dir / "final_front.png"
    final_oblique = run_dir / "final_oblique.png"
    final_bird = run_dir / "final_bird.png"
    final_validation = run_dir / "validation_final.json"

    _copy_if_exists(_resolve_path(str(src_artifacts.get("blend", "")), latest), final_blend, run_report["notes"])
    _copy_if_exists(_resolve_path(str(src_artifacts.get("front", "")), latest), final_front, run_report["notes"])
    _copy_if_exists(_resolve_path(str(src_artifacts.get("oblique", "")), latest), final_oblique, run_report["notes"])
    _copy_if_exists(_resolve_path(str(src_artifacts.get("bird", "")), latest), final_bird, run_report["notes"])
    _copy_if_exists(_resolve_path(str(src_artifacts.get("validation", "")), latest), final_validation, run_report["notes"])

    run_report["final_artifacts"] = {
        "blend": str(final_blend),
        "front": str(final_front),
        "oblique": str(final_oblique),
        "bird": str(final_bird),
        "validation": str(final_validation),
    }
    run_report["live_session"] = delegate_report.get("live_session")
    run_report["status"] = str(delegate_report.get("status", "FAILED"))

    validation_payload = _read_json(final_validation) if final_validation.exists() else {}
    run_report["self_review"] = build_self_review(
        final_artifacts=run_report["final_artifacts"],
        validation_payload=validation_payload,
        score_threshold=float(max(0.0, min(100.0, args.score_threshold))),
    )
    if run_report["status"] == "PASS" and not bool(run_report["self_review"].get("pass")):
        run_report["status"] = "NEEDS_INPUT"
        run_report["notes"].append("self_review failed: status downgraded to NEEDS_INPUT")

    report_path = run_dir / "run_report.json"
    _write_json(report_path, run_report)

    print(f"[AG] status={run_report['status']}")
    print(f"[AG] final_blend={final_blend}")
    print(f"[AG] report={report_path}")

    if run_report["status"] == "PASS":
        return 0
    if run_report["status"] in ("NEEDS_INPUT", "PARTIAL"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
