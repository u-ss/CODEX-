"""
character_agent.py - キャラモデリング専用オーケストレーター

実行例:
    python tools/blender_bridge/character_agent.py --prompt "アニメ風のキャラを作って" --work-dir ag_runs
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
from character_blueprint import build_character_blueprint
from character_spec import apply_repair_actions, normalize_character_spec, validate_character_spec
from model_self_review import build_self_review


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
    parser.add_argument("--prompt", default="キャラクターを作って")
    parser.add_argument("--form-json", default="")
    parser.add_argument("--reference-images", default="")
    parser.add_argument("--work-dir", default="ag_runs")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--score-threshold", type=float, default=84.0)
    parser.add_argument("--blender-exe", default=DEFAULT_BLENDER_EXE)
    parser.add_argument("--preset-json", default="")

    parser.add_argument("--asset-manifest", default="")
    parser.add_argument("--allow-licenses", default="")
    parser.add_argument("--deny-licenses", default="")
    parser.add_argument("--max-assets", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")

    parser.add_argument("--open-gui", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--live-host", default="127.0.0.1")
    parser.add_argument("--live-port", type=int, default=8765)
    parser.add_argument("--live-token", default="")
    parser.add_argument("--live-timeout", type=float, default=45.0)
    return parser.parse_args()


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _parse_reference_images_arg(value: str) -> List[Dict[str, Any]]:
    if not value:
        return []

    candidate = Path(value)
    if candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".json":
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except Exception:
            payload = None
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            if isinstance(payload.get("reference_images"), list):
                return payload["reference_images"]
            if isinstance(payload.get("images"), list):
                return payload["images"]

    try:
        payload = json.loads(value)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("reference_images"), list):
            return payload["reference_images"]
    except json.JSONDecodeError:
        pass

    items: List[Dict[str, Any]] = []
    for token in [x.strip() for x in value.split(",") if x.strip()]:
        items.append({"path": token})
    return items


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
    blueprint_path: Path,
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
        "--blueprint-json",
        str(blueprint_path),
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
            "--blueprint-json",
            str(blueprint_path),
            "--output-json",
            str(validation_path),
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
            "spec": str(spec_path),
            "blueprint": str(blueprint_path),
        },
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
            repl_script = Path(__file__).resolve().parent / "character_live_repl.py"
            subprocess.run([sys.executable, str(repl_script), "--session", str(session_path)], check=False)
    except Exception as exc:
        run_report["live_session_error"] = str(exc)
        run_report.setdefault("notes", []).append(f"live session start failed: {exc}")


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent

    build_script = base_dir / "scripts" / "build_character_v1.py"
    validate_script = base_dir / "scripts" / "validate_character.py"
    default_preset = base_dir / "presets" / "character_humanoid_standard.json"
    spec_contract_path = base_dir / "contracts" / "character_spec.schema.json"
    validation_contract_path = base_dir / "contracts" / "character_validation.schema.json"

    preset_path = Path(args.preset_json).resolve() if args.preset_json else default_preset
    if not preset_path.exists():
        print(f"[AG] preset not found: {preset_path}", file=sys.stderr)
        return 1
    if not build_script.exists() or not validate_script.exists():
        print("[AG] required character scripts are missing", file=sys.stderr)
        return 1
    if not spec_contract_path.exists() or not validation_contract_path.exists():
        print("[AG] character contract files are missing", file=sys.stderr)
        return 1

    try:
        form_data = _load_form_data(args.form_json)
    except Exception as exc:
        print(f"[AG] form parse error: {exc}", file=sys.stderr)
        return 1

    refs_from_arg = _parse_reference_images_arg(args.reference_images)
    if refs_from_arg:
        current_refs = form_data.get("reference_images")
        if not isinstance(current_refs, list):
            current_refs = []
        current_refs.extend(refs_from_arg)
        form_data["reference_images"] = current_refs

    preset = _read_json(preset_path)
    spec_contract = _read_json(spec_contract_path)
    validation_contract = _read_json(validation_contract_path)

    spec = normalize_character_spec(prompt=args.prompt, form_data=form_data, preset=preset)

    run_report: Dict[str, Any] = {
        "status": "RUNNING",
        "mode": "character",
        "prompt": args.prompt,
        "domain": "character",
        "agent_squad": [
            "CharacterSpecAgent",
            "CharacterBlueprintAgent",
            "CharacterBuildAgent",
            "CharacterValidationAgent",
            "CharacterLiveEditAgent",
        ],
        "assumptions": list(spec.get("assumptions", [])),
        "max_iterations": int(max(1, args.max_iterations)),
        "iterations": [],
        "notes": [],
    }

    # External asset selection (optional)
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

    spec_errors = validate_character_spec(spec)
    spec_errors.extend(validate_against_contract(spec, spec_contract))
    if spec_errors:
        print("[AG] spec validation error:")
        for err in spec_errors:
            print(f"  - {err}")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.work_dir).resolve() / f"character_agent_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_report["run_dir"] = str(run_dir)
    run_report["preset"] = str(preset_path)

    spec_path = run_dir / "character_spec_normalized.json"
    _write_json(spec_path, spec)
    blueprint = build_character_blueprint(spec)
    blueprint_path = run_dir / "character_blueprint_normalized.json"
    _write_json(blueprint_path, blueprint)

    if args.dry_run:
        run_report["status"] = "DRY_RUN"
        run_report["notes"].append("dry-run enabled: build/validate was skipped")
        run_report["final_artifacts"] = {"spec": str(spec_path), "blueprint": str(blueprint_path)}
        report_path = run_dir / "run_report.json"
        _write_json(report_path, run_report)
        print(f"[AG] status={run_report['status']}")
        print(f"[AG] spec={spec_path}")
        print(f"[AG] blueprint={blueprint_path}")
        print(f"[AG] report={report_path}")
        return 0

    cli = BlenderCLI(args.blender_exe)
    max_iterations = int(max(1, args.max_iterations))
    current_spec = spec
    final_iter: Optional[Dict[str, Any]] = None
    best_iter: Optional[Dict[str, Any]] = None

    try:
        for idx in range(max_iterations):
            iter_spec_path = run_dir / f"character_spec_iter_{idx:02d}.json"
            _write_json(iter_spec_path, current_spec)
            iter_blueprint = build_character_blueprint(current_spec)
            iter_blueprint_path = run_dir / f"character_blueprint_iter_{idx:02d}.json"
            _write_json(iter_blueprint_path, iter_blueprint)

            iter_result = _build_and_validate_iteration(
                cli=cli,
                build_script=build_script,
                validate_script=validate_script,
                run_dir=run_dir,
                iter_idx=idx,
                spec_path=iter_spec_path,
                blueprint_path=iter_blueprint_path,
                samples=args.samples,
                score_threshold=float(max(0.0, min(100.0, args.score_threshold))),
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

            validation_errors = validate_against_contract(iter_result["validation"], validation_contract)
            if validation_errors:
                iter_result["pass"] = False
                run_report["iterations"][-1]["pass"] = False
                run_report["notes"].append(f"{iter_result['prefix']} validation schema mismatch: " + "; ".join(validation_errors))

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
    final_spec = run_dir / "character_spec_final.json"
    final_blueprint = run_dir / "character_blueprint_final.json"

    src = final_iter["artifacts"]
    _copy_if_exists(Path(src["blend"]), final_blend, notes)
    _copy_if_exists(Path(src["front"]), final_front, notes)
    _copy_if_exists(Path(src["oblique"]), final_oblique, notes)
    _copy_if_exists(Path(src["bird"]), final_bird, notes)
    _copy_if_exists(Path(src["validation"]), final_validation_path, notes)
    _copy_if_exists(Path(src["spec"]), final_spec, notes)
    _copy_if_exists(Path(src["blueprint"]), final_blueprint, notes)

    run_report["final_artifacts"] = {
        "blend": str(final_blend),
        "front": str(final_front),
        "oblique": str(final_oblique),
        "bird": str(final_bird),
        "validation": str(final_validation_path),
        "spec": str(final_spec),
        "blueprint": str(final_blueprint),
    }

    validation_payload = _read_json(final_validation_path) if final_validation_path.exists() else {}
    self_review = build_self_review(
        final_artifacts=run_report["final_artifacts"],
        validation_payload=validation_payload,
        score_threshold=float(max(0.0, min(100.0, args.score_threshold))),
    )

    # character-specific review tightening
    if isinstance(validation_payload, dict):
        part_checks = validation_payload.get("part_checks", []) if isinstance(validation_payload.get("part_checks"), list) else []
        symmetry_checks = (
            validation_payload.get("symmetry_checks", [])
            if isinstance(validation_payload.get("symmetry_checks"), list)
            else []
        )
        rig_checks = validation_payload.get("rig_checks", []) if isinstance(validation_payload.get("rig_checks"), list) else []

        part_fail_count = sum(1 for item in part_checks if isinstance(item, dict) and not bool(item.get("pass")))
        sym_fail_count = sum(1 for item in symmetry_checks if isinstance(item, dict) and not bool(item.get("pass")))
        rig_fail_count = sum(1 for item in rig_checks if isinstance(item, dict) and not bool(item.get("pass")))

        if part_fail_count > 0:
            self_review["checks"].append(
                {
                    "name": "character_part_checks",
                    "pass": False,
                    "detail": f"failed parts={part_fail_count}",
                }
            )
            self_review["recommendations"].append("不足部位を補完してください")
            self_review["score"] = max(0.0, float(self_review.get("score", 0.0)) - min(20.0, part_fail_count * 6.0))

        if sym_fail_count > 0:
            self_review["checks"].append(
                {
                    "name": "character_symmetry_checks",
                    "pass": False,
                    "detail": f"failed symmetry pairs={sym_fail_count}",
                }
            )
            self_review["recommendations"].append("左右対称ペアの位置/スケールを修正してください")
            self_review["score"] = max(0.0, float(self_review.get("score", 0.0)) - min(16.0, sym_fail_count * 4.0))

        if rig_fail_count > 0:
            self_review["checks"].append(
                {
                    "name": "character_rig_checks",
                    "pass": False,
                    "detail": f"failed rig checks={rig_fail_count}",
                }
            )
            self_review["recommendations"].append("リグ要件を満たすよう再生成してください")
            self_review["score"] = max(0.0, float(self_review.get("score", 0.0)) - 14.0)

        self_review["pass"] = all(bool(item.get("pass")) for item in self_review.get("checks", []))
        self_review["score"] = round(float(self_review.get("score", 0.0)), 2)

    run_report["self_review"] = self_review
    if run_report["status"] == "PASS" and not bool(self_review.get("pass")):
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
