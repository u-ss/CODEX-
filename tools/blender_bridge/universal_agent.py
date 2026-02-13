"""
universal_agent.py - Blender汎用生成オーケストレーター

運用優先の設計:
- 既存 house_agent を活用しつつ、汎用生成へフォールバック
- 外部アセットはライセンスポリシーを通過したもののみ採用
- 反復検証で自動修正（最大N回）
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

from asset_pipeline import (
    apply_asset_selection_to_spec,
    build_asset_report,
    load_asset_manifest,
    parse_license_policy,
    select_assets,
)
from blender_cli import BlenderCLI, BlenderCLIError, DEFAULT_BLENDER_EXE
from model_self_review import build_self_review
from universal_spec import apply_repair_actions, normalize_asset_spec, validate_asset_spec

UNIVERSAL_SCORE_THRESHOLD = 82.0


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
    parser.add_argument("--prompt", default="3Dモデルを作って")
    parser.add_argument("--form-json", default="")
    parser.add_argument("--work-dir", default="ag_runs")
    parser.add_argument("--domain", default="auto")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--score-threshold", type=float, default=UNIVERSAL_SCORE_THRESHOLD)
    parser.add_argument("--blender-exe", default=DEFAULT_BLENDER_EXE)

    parser.add_argument("--asset-manifest", default="")
    parser.add_argument("--allow-licenses", default="")
    parser.add_argument("--deny-licenses", default="")
    parser.add_argument("--max-assets", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")

    parser.add_argument("--prefer-house-agent", action="store_true", default=True)
    parser.add_argument("--no-prefer-house-agent", action="store_false", dest="prefer_house_agent")
    parser.add_argument("--prefer-character-agent", action="store_true", default=True)
    parser.add_argument("--no-prefer-character-agent", action="store_false", dest="prefer_character_agent")

    parser.add_argument("--open-gui", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--live-host", default="127.0.0.1")
    parser.add_argument("--live-port", type=int, default=8765)
    parser.add_argument("--live-token", default="")
    parser.add_argument("--live-timeout", type=float, default=45.0)
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
        value = json.loads(form_json_arg)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    raise ValueError(f"--form-json が不正です: {form_json_arg}")


def _copy_if_exists(src: Path, dst: Path, notes: List[str]) -> None:
    if src.exists() and src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    else:
        notes.append(f"missing artifact: {src}")


def _build_and_validate_iteration(
    cli: BlenderCLI,
    build_script: Path,
    validate_script: Path,
    run_dir: Path,
    iter_idx: int,
    spec_path: Path,
    samples: Optional[int],
    score_threshold: float,
) -> Dict[str, Any]:
    prefix = f"iter_{iter_idx:02d}"
    blend_path = run_dir / f"{prefix}.blend"
    validation_path = run_dir / f"validation_{prefix}.json"
    build_log = run_dir / f"{prefix}_build.log"
    validate_log = run_dir / f"{prefix}_validate.log"

    build_args = [
        "--spec-json",
        str(spec_path),
        "--output-dir",
        str(run_dir),
        "--render-prefix",
        prefix,
        "--save-blend",
        str(blend_path),
    ]
    if samples is not None:
        build_args.extend(["--samples", str(samples)])

    cli.run_script(
        str(build_script),
        args=build_args,
        log_file=str(build_log),
        timeout=1200,
        factory_startup=True,
    )

    cli.run_script(
        str(validate_script),
        blend_file=str(blend_path),
        args=[
            "--spec-json",
            str(spec_path),
            "--output-json",
            str(validation_path),
            "--render-dir",
            str(run_dir),
            "--render-prefix",
            prefix,
            "--score-threshold",
            str(float(score_threshold)),
        ],
        log_file=str(validate_log),
        timeout=600,
        factory_startup=False,
    )

    validation = _read_json(validation_path)
    return {
        "index": iter_idx,
        "prefix": prefix,
        "score": validation.get("score", 0),
        "pass": bool(validation.get("pass")),
        "validation": validation,
        "artifacts": {
            "blend": str(blend_path),
            "front": str(run_dir / f"{prefix}_front.png"),
            "oblique": str(run_dir / f"{prefix}_oblique.png"),
            "bird": str(run_dir / f"{prefix}_bird.png"),
            "validation": str(validation_path),
        },
    }


def _find_latest_subdir(root: Path) -> Optional[Path]:
    subdirs = [p for p in root.glob("*") if p.is_dir()]
    if not subdirs:
        return None
    subdirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return subdirs[0]


def _run_house_delegate(args: argparse.Namespace, run_dir: Path, form_data: Dict[str, Any], notes: List[str]) -> Optional[Dict[str, Any]]:
    base_dir = Path(__file__).resolve().parent
    house_agent = base_dir / "house_agent.py"
    if not house_agent.exists():
        notes.append("house_agent.py not found, fallback to universal build")
        return None

    delegate_root = run_dir / "house_delegate_runs"
    delegate_root.mkdir(parents=True, exist_ok=True)

    form_path = run_dir / "house_delegate_form.json"
    _write_json(form_path, form_data)

    cmd = [
        sys.executable,
        str(house_agent),
        "--prompt",
        args.prompt,
        "--form-json",
        str(form_path),
        "--work-dir",
        str(delegate_root),
        "--max-iterations",
        str(max(1, int(args.max_iterations))),
        "--blender-exe",
        args.blender_exe,
        "--score-threshold",
        str(float(max(0.0, min(100.0, args.score_threshold)))),
    ]
    if args.samples is not None:
        cmd.extend(["--samples", str(args.samples)])
    if args.open_gui:
        cmd.append("--open-gui")
    if args.interactive:
        cmd.append("--interactive")
        cmd.extend([
            "--live-host",
            str(args.live_host),
            "--live-port",
            str(args.live_port),
            "--live-token",
            str(args.live_token),
            "--live-timeout",
            str(args.live_timeout),
        ])

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    notes.append(f"house_delegate returncode={proc.returncode}")

    latest = _find_latest_subdir(delegate_root)
    if latest is None:
        notes.append("house_delegate produced no run directory")
        notes.append(proc.stdout.strip())
        notes.append(proc.stderr.strip())
        return None

    report_path = latest / "run_report.json"
    if not report_path.exists():
        notes.append(f"house_delegate missing report: {report_path}")
        return None

    report = _read_json(report_path)
    report["_delegate_stdout"] = proc.stdout[-3000:]
    report["_delegate_stderr"] = proc.stderr[-3000:]
    return {
        "delegate_run_dir": str(latest),
        "delegate_report": report,
        "returncode": proc.returncode,
    }


def _run_character_delegate(args: argparse.Namespace, run_dir: Path, form_data: Dict[str, Any], notes: List[str]) -> Optional[Dict[str, Any]]:
    base_dir = Path(__file__).resolve().parent
    character_agent = base_dir / "character_agent.py"
    if not character_agent.exists():
        notes.append("character_agent.py not found, fallback to universal build")
        return None

    delegate_root = run_dir / "character_delegate_runs"
    delegate_root.mkdir(parents=True, exist_ok=True)

    form_path = run_dir / "character_delegate_form.json"
    _write_json(form_path, form_data)

    cmd = [
        sys.executable,
        str(character_agent),
        "--prompt",
        args.prompt,
        "--form-json",
        str(form_path),
        "--work-dir",
        str(delegate_root),
        "--max-iterations",
        str(max(1, int(args.max_iterations))),
        "--blender-exe",
        args.blender_exe,
        "--score-threshold",
        str(float(max(0.0, min(100.0, args.score_threshold)))),
    ]
    if args.samples is not None:
        cmd.extend(["--samples", str(args.samples)])
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
    notes.append(f"character_delegate returncode={proc.returncode}")

    latest = _find_latest_subdir(delegate_root)
    if latest is None:
        notes.append("character_delegate produced no run directory")
        notes.append(proc.stdout.strip())
        notes.append(proc.stderr.strip())
        return None

    report_path = latest / "run_report.json"
    if not report_path.exists():
        notes.append(f"character_delegate missing report: {report_path}")
        return None

    report = _read_json(report_path)
    report["_delegate_stdout"] = proc.stdout[-3000:]
    report["_delegate_stderr"] = proc.stderr[-3000:]
    return {
        "delegate_run_dir": str(latest),
        "delegate_report": report,
        "returncode": proc.returncode,
    }


def _maybe_start_live_session(args: argparse.Namespace, final_blend: Path, run_dir: Path, run_report: Dict[str, Any]) -> None:
    if args.interactive and not args.open_gui:
        run_report.setdefault("notes", []).append("--interactive は --open-gui 指定時のみ有効です")
        return
    if not args.open_gui:
        return
    if not final_blend.exists():
        run_report.setdefault("notes", []).append("live session skipped: final blend not found")
        return

    try:
        from house_live_session import create_live_session

        session_path = create_live_session(
            blender_exe=args.blender_exe,
            blend_file=final_blend,
            run_dir=run_dir,
            host=args.live_host,
            port=args.live_port,
            token=(args.live_token or None),
            timeout_s=args.live_timeout,
        )
        run_report["live_session"] = str(session_path)
        print(f"[AG] live_session={session_path}")

        if args.interactive:
            repl_script = Path(__file__).resolve().parent / "house_live_repl.py"
            subprocess.run([sys.executable, str(repl_script), "--session", str(session_path)], check=False)
    except Exception as exc:
        run_report["live_session_error"] = str(exc)
        run_report.setdefault("notes", []).append(f"live session start failed: {exc}")


def main() -> int:
    args = parse_args()
    score_threshold = float(max(0.0, min(100.0, args.score_threshold)))
    base_dir = Path(__file__).resolve().parent

    build_script = base_dir / "scripts" / "build_universal_asset.py"
    validate_script = base_dir / "scripts" / "validate_universal_asset.py"
    spec_contract_path = base_dir / "contracts" / "asset_spec.schema.json"
    validation_contract_path = base_dir / "contracts" / "asset_validation.schema.json"

    if not build_script.exists() or not validate_script.exists():
        print("[AG] required universal scripts are missing", file=sys.stderr)
        return 1
    if not spec_contract_path.exists() or not validation_contract_path.exists():
        print("[AG] universal contract files are missing", file=sys.stderr)
        return 1

    try:
        form_data = _load_form_data(args.form_json)
    except Exception as exc:
        print(f"[AG] form parse error: {exc}", file=sys.stderr)
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.work_dir).resolve() / f"universal_agent_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    spec_contract = _read_json(spec_contract_path)
    validation_contract = _read_json(validation_contract_path)

    spec = normalize_asset_spec(
        prompt=args.prompt,
        form_data=form_data,
        preset=None,
        domain_override=args.domain,
    )

    spec_errors = validate_asset_spec(spec)
    spec_errors.extend(validate_against_contract(spec, spec_contract))
    if spec_errors:
        print("[AG] spec validation error:")
        for err in spec_errors:
            print(f"  - {err}")
        return 1

    run_report: Dict[str, Any] = {
        "status": "RUNNING",
        "mode": "universal",
        "prompt": args.prompt,
        "run_dir": str(run_dir),
        "domain": spec.get("domain"),
        "assumptions": list(spec.get("assumptions", [])),
        "max_iterations": int(max(1, args.max_iterations)),
        "iterations": [],
        "notes": [],
    }

    # External asset selection (manifest opt-in)
    if args.asset_manifest:
        manifest_path = Path(args.asset_manifest).resolve()
        if not manifest_path.exists():
            run_report["notes"].append(f"asset manifest not found: {manifest_path}")
        else:
            allow_licenses, deny_licenses = parse_license_policy(args.allow_licenses, args.deny_licenses)
            catalog = load_asset_manifest(manifest_path)
            selection = select_assets(
                spec=spec,
                catalog=catalog,
                allow_licenses=allow_licenses,
                deny_licenses=deny_licenses,
                max_assets=args.max_assets,
            )
            run_report["asset_report"] = build_asset_report(
                manifest_path=manifest_path,
                allow_licenses=allow_licenses,
                deny_licenses=deny_licenses,
                selection=selection,
            )
            if selection.selected:
                spec = apply_asset_selection_to_spec(spec, selection.selected)
                run_report["notes"].append(f"selected assets: {len(selection.selected)}")
            if selection.rejected:
                run_report["notes"].append(f"rejected assets: {len(selection.rejected)}")

    spec_path = run_dir / "asset_spec_normalized.json"
    _write_json(spec_path, spec)

    if args.dry_run:
        run_report["status"] = "DRY_RUN"
        run_report["notes"].append("dry-run enabled: build/validate was skipped")
        run_report["final_artifacts"] = {}
        report_path = run_dir / "run_report.json"
        _write_json(report_path, run_report)
        print(f"[AG] status={run_report['status']}")
        print(f"[AG] spec={spec_path}")
        print(f"[AG] report={report_path}")
        return 0

    # Prefer house pipeline when domain=house
    if str(spec.get("domain", "")) == "house" and args.prefer_house_agent:
        delegate = _run_house_delegate(args=args, run_dir=run_dir, form_data=form_data, notes=run_report["notes"])
        if delegate is not None:
            delegate_report = delegate["delegate_report"]
            status = str(delegate_report.get("status", "UNKNOWN"))
            run_report["house_delegate"] = {
                "run_dir": delegate["delegate_run_dir"],
                "status": status,
                "returncode": delegate["returncode"],
            }
            if status in ("PASS", "NEEDS_INPUT", "PARTIAL"):
                src_artifacts = delegate_report.get("final_artifacts", {}) if isinstance(delegate_report, dict) else {}
                notes = run_report["notes"]

                final_blend = run_dir / "final.blend"
                final_front = run_dir / "final_front.png"
                final_oblique = run_dir / "final_oblique.png"
                final_bird = run_dir / "final_bird.png"
                final_validation = run_dir / "validation_final.json"

                _copy_if_exists(Path(str(src_artifacts.get("blend", ""))), final_blend, notes)
                _copy_if_exists(Path(str(src_artifacts.get("front", ""))), final_front, notes)
                _copy_if_exists(Path(str(src_artifacts.get("oblique", ""))), final_oblique, notes)
                _copy_if_exists(Path(str(src_artifacts.get("bird", ""))), final_bird, notes)
                _copy_if_exists(Path(str(src_artifacts.get("validation", ""))), final_validation, notes)

                run_report["status"] = status
                run_report["final_artifacts"] = {
                    "blend": str(final_blend),
                    "front": str(final_front),
                    "oblique": str(final_oblique),
                    "bird": str(final_bird),
                    "validation": str(final_validation),
                }
                final_validation_payload = _read_json(final_validation) if final_validation.exists() else {}
                run_report["self_review"] = build_self_review(
                    final_artifacts=run_report["final_artifacts"],
                    validation_payload=final_validation_payload,
                    score_threshold=score_threshold,
                )
                if run_report["status"] == "PASS" and not bool(run_report["self_review"].get("pass")):
                    run_report["status"] = "NEEDS_INPUT"
                    run_report["notes"].append("self_review failed: status downgraded to NEEDS_INPUT")

                delegate_live_session = delegate_report.get("live_session") if isinstance(delegate_report, dict) else None
                if delegate_live_session:
                    run_report["live_session"] = delegate_live_session
                else:
                    _maybe_start_live_session(args, final_blend=final_blend, run_dir=run_dir, run_report=run_report)
                report_path = run_dir / "run_report.json"
                _write_json(report_path, run_report)

                print(f"[AG] status={run_report['status']}")
                print(f"[AG] final_blend={final_blend}")
                print(f"[AG] report={report_path}")
                if run_report["status"] == "PASS":
                    return 0
                return 2

            run_report["notes"].append(f"house delegate status={status}, fallback to universal build")

        if delegate is None:
            run_report["notes"].append("house delegate failed, fallback to universal build")

    # Prefer character pipeline when domain=character
    if str(spec.get("domain", "")) == "character" and args.prefer_character_agent:
        delegate = _run_character_delegate(args=args, run_dir=run_dir, form_data=form_data, notes=run_report["notes"])
        if delegate is not None:
            delegate_report = delegate["delegate_report"]
            status = str(delegate_report.get("status", "UNKNOWN"))
            run_report["character_delegate"] = {
                "run_dir": delegate["delegate_run_dir"],
                "status": status,
                "returncode": delegate["returncode"],
            }
            if status in ("PASS", "NEEDS_INPUT", "PARTIAL"):
                src_artifacts = delegate_report.get("final_artifacts", {}) if isinstance(delegate_report, dict) else {}
                notes = run_report["notes"]

                final_blend = run_dir / "final.blend"
                final_front = run_dir / "final_front.png"
                final_oblique = run_dir / "final_oblique.png"
                final_bird = run_dir / "final_bird.png"
                final_validation = run_dir / "validation_final.json"

                _copy_if_exists(Path(str(src_artifacts.get("blend", ""))), final_blend, notes)
                _copy_if_exists(Path(str(src_artifacts.get("front", ""))), final_front, notes)
                _copy_if_exists(Path(str(src_artifacts.get("oblique", ""))), final_oblique, notes)
                _copy_if_exists(Path(str(src_artifacts.get("bird", ""))), final_bird, notes)
                _copy_if_exists(Path(str(src_artifacts.get("validation", ""))), final_validation, notes)

                run_report["status"] = status
                run_report["final_artifacts"] = {
                    "blend": str(final_blend),
                    "front": str(final_front),
                    "oblique": str(final_oblique),
                    "bird": str(final_bird),
                    "validation": str(final_validation),
                }
                final_validation_payload = _read_json(final_validation) if final_validation.exists() else {}
                run_report["self_review"] = build_self_review(
                    final_artifacts=run_report["final_artifacts"],
                    validation_payload=final_validation_payload,
                    score_threshold=score_threshold,
                )
                if run_report["status"] == "PASS" and not bool(run_report["self_review"].get("pass")):
                    run_report["status"] = "NEEDS_INPUT"
                    run_report["notes"].append("self_review failed: status downgraded to NEEDS_INPUT")

                delegate_live_session = delegate_report.get("live_session") if isinstance(delegate_report, dict) else None
                if delegate_live_session:
                    run_report["live_session"] = delegate_live_session
                else:
                    _maybe_start_live_session(args, final_blend=final_blend, run_dir=run_dir, run_report=run_report)
                report_path = run_dir / "run_report.json"
                _write_json(report_path, run_report)

                print(f"[AG] status={run_report['status']}")
                print(f"[AG] final_blend={final_blend}")
                print(f"[AG] report={report_path}")
                if run_report["status"] == "PASS":
                    return 0
                return 2

            run_report["notes"].append(f"character delegate status={status}, fallback to universal build")

        if delegate is None:
            run_report["notes"].append("character delegate failed, fallback to universal build")

    # Universal iterative loop
    cli = BlenderCLI(args.blender_exe)
    max_iterations = int(max(1, args.max_iterations))
    current_spec = spec
    final_iter: Optional[Dict[str, Any]] = None
    best_iter: Optional[Dict[str, Any]] = None

    try:
        for idx in range(max_iterations):
            iter_spec_path = run_dir / f"asset_spec_iter_{idx:02d}.json"
            _write_json(iter_spec_path, current_spec)

            iter_result = _build_and_validate_iteration(
                cli=cli,
                build_script=build_script,
                validate_script=validate_script,
                run_dir=run_dir,
                iter_idx=idx,
                spec_path=iter_spec_path,
                samples=args.samples,
                score_threshold=score_threshold,
            )
            run_report["iterations"].append(
                {
                    "index": iter_result["index"],
                    "prefix": iter_result["prefix"],
                    "score": iter_result["score"],
                    "pass": iter_result["pass"],
                    "artifacts": iter_result["artifacts"],
                }
            )

            validation_schema_errors = validate_against_contract(iter_result["validation"], validation_contract)
            if validation_schema_errors:
                iter_result["pass"] = False
                run_report["iterations"][-1]["pass"] = False
                run_report["notes"].append(
                    f"{iter_result['prefix']} validation schema mismatch: " + "; ".join(validation_schema_errors)
                )

            current_score = float(iter_result.get("score", 0) or 0)
            if best_iter is None:
                best_iter = iter_result
            else:
                best_score = float(best_iter.get("score", 0) or 0)
                if current_score > best_score:
                    best_iter = iter_result

            if iter_result["pass"]:
                final_iter = iter_result
                break

            if idx >= max_iterations - 1:
                final_iter = best_iter or iter_result
                break

            actions = iter_result["validation"].get("repair_actions", [])
            next_spec, applied = apply_repair_actions(current_spec, actions)
            if not applied:
                run_report["notes"].append("validator が修正案を返さなかったため反復終了")
                final_iter = best_iter or iter_result
                break

            run_report["notes"].append(f"iter_{idx:02d} repair applied: {', '.join(applied)}")
            current_spec = next_spec

    except BlenderCLIError as exc:
        run_report["status"] = "FAILED"
        run_report["error"] = str(exc)
        run_report["blender_stdout"] = exc.stdout
        run_report["blender_log"] = exc.log_path
        report_path = run_dir / "run_report.json"
        _write_json(report_path, run_report)
        print(f"[AG] FAILED: {exc}", file=sys.stderr)
        print(f"[AG] report: {report_path}", file=sys.stderr)
        return 1

    if final_iter is None:
        if best_iter is not None:
            final_iter = best_iter
            run_report["notes"].append("final iteration fallback: selected best scored iteration")
        else:
            run_report["status"] = "FAILED"
            run_report["notes"].append("iteration result unavailable")
            report_path = run_dir / "run_report.json"
            _write_json(report_path, run_report)
            return 1

    final_validation = final_iter["validation"]
    passed = bool(final_validation.get("pass"))
    run_report["status"] = "PASS" if passed else "NEEDS_INPUT"
    run_report["final_iteration"] = final_iter["index"]
    run_report["final_score"] = final_validation.get("score", 0)
    if run_report["iterations"]:
        last_idx = int(run_report["iterations"][-1].get("index", 0))
        if int(run_report["final_iteration"]) != last_idx:
            run_report["notes"].append(
                f"best iteration selected: iter_{int(run_report['final_iteration']):02d} "
                f"(last iter_{last_idx:02d} より高スコア)"
            )

    notes = run_report["notes"]
    final_blend = run_dir / "final.blend"
    final_front = run_dir / "final_front.png"
    final_oblique = run_dir / "final_oblique.png"
    final_bird = run_dir / "final_bird.png"
    final_validation_path = run_dir / "validation_final.json"

    src_art = final_iter["artifacts"]
    _copy_if_exists(Path(src_art["blend"]), final_blend, notes)
    _copy_if_exists(Path(src_art["front"]), final_front, notes)
    _copy_if_exists(Path(src_art["oblique"]), final_oblique, notes)
    _copy_if_exists(Path(src_art["bird"]), final_bird, notes)
    _copy_if_exists(Path(src_art["validation"]), final_validation_path, notes)

    run_report["final_artifacts"] = {
        "blend": str(final_blend),
        "front": str(final_front),
        "oblique": str(final_oblique),
        "bird": str(final_bird),
        "validation": str(final_validation_path),
    }

    final_validation_payload = _read_json(final_validation_path) if final_validation_path.exists() else {}
    run_report["self_review"] = build_self_review(
        final_artifacts=run_report["final_artifacts"],
        validation_payload=final_validation_payload,
        score_threshold=score_threshold,
    )
    if run_report["status"] == "PASS" and not bool(run_report["self_review"].get("pass")):
        run_report["status"] = "NEEDS_INPUT"
        run_report["notes"].append("self_review failed: status downgraded to NEEDS_INPUT")

    _maybe_start_live_session(args, final_blend=final_blend, run_dir=run_dir, run_report=run_report)

    report_path = run_dir / "run_report.json"
    _write_json(report_path, run_report)

    print(f"[AG] status={run_report['status']}")
    print(f"[AG] final_blend={final_blend}")
    print(f"[AG] validation={final_validation_path}")
    print(f"[AG] report={report_path}")

    if run_report["status"] == "PASS":
        return 0
    if run_report["status"] in ("NEEDS_INPUT", "PARTIAL"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
