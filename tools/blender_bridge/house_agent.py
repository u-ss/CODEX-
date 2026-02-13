"""
house_agent.py - 戸建て高精度生成エージェント

実行例:
    python tools/blender_bridge/house_agent.py --prompt "戸建てを作って" --work-dir ag_runs
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from blender_cli import BlenderCLI, BlenderCLIError, DEFAULT_BLENDER_EXE
from house_spec import apply_repair_actions, normalize_house_spec, validate_spec
from model_self_review import build_self_review


def validate_against_contract(data: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """JSON Schemaの主要部分のみを使った軽量バリデーション。"""
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
            item_rule = rule.get("items")
            if isinstance(item_rule, dict):
                for i, item in enumerate(value):
                    item_type = item_rule.get("type")
                    if item_type == "number" and not isinstance(item, (int, float)):
                        errors.append(f"{key}[{i}]: expected number")
                        continue
                    if item_type == "string" and not isinstance(item, str):
                        errors.append(f"{key}[{i}]: expected string")
                        continue
                    if isinstance(item, (int, float)):
                        if "minimum" in item_rule and item < item_rule["minimum"]:
                            errors.append(f"{key}[{i}]: {item} < minimum {item_rule['minimum']}")
                        if "maximum" in item_rule and item > item_rule["maximum"]:
                            errors.append(f"{key}[{i}]: {item} > maximum {item_rule['maximum']}")

    return errors


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", default="戸建てを作って")
    p.add_argument("--form-json", default="")
    p.add_argument("--work-dir", default="ag_runs")
    p.add_argument("--max-iterations", type=int, default=3)
    p.add_argument("--samples", type=int, default=None)
    p.add_argument("--score-threshold", type=float, default=85.0)
    p.add_argument("--blender-exe", default=DEFAULT_BLENDER_EXE)
    p.add_argument("--preset-json", default="")
    p.add_argument("--open-gui", action="store_true")
    p.add_argument("--interactive", action="store_true")
    p.add_argument("--live-host", default="127.0.0.1")
    p.add_argument("--live-port", type=int, default=8765)
    p.add_argument("--live-token", default="")
    p.add_argument("--live-timeout", type=float, default=45.0)
    return p.parse_args()


def load_json_file(path: Path) -> Dict[str, Any]:
    # PowerShellがUTF-8 BOM付きで書き込むケースを吸収する
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_form_data(form_json_arg: str) -> Dict[str, Any]:
    if not form_json_arg:
        return {}
    candidate = Path(form_json_arg)
    if candidate.exists():
        return load_json_file(candidate)
    # JSON文字列としても受ける
    try:
        value = json.loads(form_json_arg)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    raise ValueError(f"--form-json が不正です: {form_json_arg}")


def copy_if_exists(src: Path, dst: Path, notes: List[str]):
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    else:
        notes.append(f"missing artifact: {src}")


def build_and_validate_iteration(
    cli: BlenderCLI,
    build_script: Path,
    validate_script: Path,
    run_dir: Path,
    iter_idx: int,
    spec_path: Path,
    samples: int = None,
    score_threshold: float = 85.0,
) -> Dict[str, Any]:
    prefix = f"iter_{iter_idx:02d}"
    blend_path = run_dir / f"{prefix}.blend"
    validation_path = run_dir / f"validation_{prefix}.json"
    build_log = run_dir / f"{prefix}_build.log"
    validate_log = run_dir / f"{prefix}_validate.log"

    script_args = [
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
        script_args.extend(["--samples", str(samples)])

    cli.run_script(
        str(build_script),
        args=script_args,
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
            "--score-threshold",
            str(float(score_threshold)),
        ],
        log_file=str(validate_log),
        timeout=600,
        factory_startup=False,
    )

    validation = load_json_file(validation_path)
    artifacts = {
        "blend": str(blend_path),
        "front": str(run_dir / f"{prefix}_front.png"),
        "back": str(run_dir / f"{prefix}_back.png"),
        "left": str(run_dir / f"{prefix}_left.png"),
        "right": str(run_dir / f"{prefix}_right.png"),
        "oblique": str(run_dir / f"{prefix}_oblique.png"),
        "bird": str(run_dir / f"{prefix}_bird.png"),
        "validation": str(validation_path),
    }
    return {
        "index": iter_idx,
        "prefix": prefix,
        "score": validation.get("score", 0),
        "pass": bool(validation.get("pass")),
        "validation": validation,
        "artifacts": artifacts,
    }


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    build_script = base_dir / "scripts" / "build_house_v5.py"
    validate_script = base_dir / "scripts" / "validate_house.py"
    default_preset = base_dir / "presets" / "jp_wood_2f_standard.json"
    spec_contract_path = base_dir / "contracts" / "house_spec.schema.json"
    validation_contract_path = base_dir / "contracts" / "house_validation.schema.json"

    preset_path = Path(args.preset_json).resolve() if args.preset_json else default_preset
    if not preset_path.exists():
        print(f"[AG] preset not found: {preset_path}", file=sys.stderr)
        return 1
    if not build_script.exists() or not validate_script.exists():
        print("[AG] required scripts are missing", file=sys.stderr)
        return 1
    if not spec_contract_path.exists() or not validation_contract_path.exists():
        print("[AG] contract files are missing", file=sys.stderr)
        return 1

    try:
        form_data = load_form_data(args.form_json)
    except Exception as e:
        print(f"[AG] form parse error: {e}", file=sys.stderr)
        return 1

    preset = load_json_file(preset_path)
    spec_contract = load_json_file(spec_contract_path)
    validation_contract = load_json_file(validation_contract_path)
    spec = normalize_house_spec(args.prompt, form_data, preset)
    spec_errors = validate_spec(spec)
    spec_errors.extend(validate_against_contract(spec, spec_contract))
    if spec_errors:
        print("[AG] spec validation error:")
        for err in spec_errors:
            print(f"  - {err}")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.work_dir).resolve() / f"house_agent_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    spec_path = run_dir / "house_spec_normalized.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    cli = BlenderCLI(args.blender_exe)

    run_report: Dict[str, Any] = {
        "status": "RUNNING",
        "prompt": args.prompt,
        "run_dir": str(run_dir),
        "preset": str(preset_path),
        "assumptions": list(spec.get("assumptions", [])),
        "max_iterations": int(max(1, args.max_iterations)),
        "iterations": [],
        "notes": [],
    }

    final_iter = None
    best_iter = None
    current_spec = spec
    max_iterations = int(max(1, args.max_iterations))

    try:
        for idx in range(max_iterations):
            iter_spec_path = run_dir / f"house_spec_iter_{idx:02d}.json"
            iter_spec_path.write_text(json.dumps(current_spec, ensure_ascii=False, indent=2), encoding="utf-8")

            iter_result = build_and_validate_iteration(
                cli=cli,
                build_script=build_script,
                validate_script=validate_script,
                run_dir=run_dir,
                iter_idx=idx,
                spec_path=iter_spec_path,
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

            validation_contract_errors = validate_against_contract(iter_result["validation"], validation_contract)
            if validation_contract_errors:
                iter_result["pass"] = False
                run_report["iterations"][-1]["pass"] = False
                run_report["notes"].append(
                    f"{iter_result['prefix']} validation schema mismatch: " + "; ".join(validation_contract_errors)
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

            repair_actions = iter_result["validation"].get("repair_actions", [])
            next_spec, applied = apply_repair_actions(current_spec, repair_actions)
            if not applied:
                run_report["notes"].append("validator が修正案を返さなかったため反復終了")
                final_iter = best_iter or iter_result
                break
            run_report["notes"].append(f"iter_{idx:02d} repair applied: {', '.join(applied)}")
            current_spec = next_spec

    except BlenderCLIError as e:
        run_report["status"] = "FAILED"
        run_report["error"] = str(e)
        run_report["blender_stdout"] = e.stdout
        run_report["blender_log"] = e.log_path
        report_path = run_dir / "run_report.json"
        report_path.write_text(json.dumps(run_report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[AG] FAILED: {e}", file=sys.stderr)
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
            report_path.write_text(json.dumps(run_report, ensure_ascii=False, indent=2), encoding="utf-8")
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

    # final artifacts（6視点対応）
    notes: List[str] = run_report["notes"]
    final_blend = run_dir / "final.blend"
    final_validation_path = run_dir / "validation_final.json"
    view_names = ["front", "back", "left", "right", "oblique", "bird"]
    final_views = {v: run_dir / f"final_{v}.png" for v in view_names}

    source_art = final_iter["artifacts"]
    copy_if_exists(Path(source_art["blend"]), final_blend, notes)
    for v in view_names:
        if v in source_art:
            copy_if_exists(Path(source_art[v]), final_views[v], notes)
    copy_if_exists(Path(source_art["validation"]), final_validation_path, notes)

    unresolved = []
    for c in final_validation.get("dimension_checks", []):
        if not c.get("pass"):
            unresolved.append(f"dimension:{c.get('name')}")
    for c in final_validation.get("topology_checks", []):
        if not c.get("pass"):
            unresolved.append(f"topology:{c.get('name')}")
    for action in final_validation.get("repair_actions", []):
        key = action.get("key")
        reason = action.get("reason", "")
        if key:
            unresolved.append(f"repair:{key}:{reason}")
    if unresolved:
        run_report["unresolved_issues"] = unresolved

    run_report["final_artifacts"] = {
        "blend": str(final_blend),
        **{v: str(final_views[v]) for v in view_names},
        "validation": str(final_validation_path),
    }

    validation_payload = load_json_file(final_validation_path) if final_validation_path.exists() else {}
    run_report["self_review"] = build_self_review(
        final_artifacts=run_report["final_artifacts"],
        validation_payload=validation_payload,
        score_threshold=float(max(0.0, min(100.0, args.score_threshold))),
    )
    if run_report["status"] == "PASS" and not bool(run_report["self_review"].get("pass")):
        run_report["status"] = "NEEDS_INPUT"
        run_report["notes"].append("self_review failed: status downgraded to NEEDS_INPUT")

    if args.interactive and not args.open_gui:
        run_report["notes"].append("--interactive は --open-gui 指定時のみ有効です")

    if args.open_gui and final_blend.exists():
        try:
            from house_live_session import create_live_session

            session_path = create_live_session(
                blender_exe=cli.blender_exe,
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
                repl_script = base_dir / "house_live_repl.py"
                repl_cmd = [sys.executable, str(repl_script), "--session", str(session_path)]
                run_report["notes"].append("interactive live editing started")
                subprocess.run(repl_cmd, check=False)
        except Exception as e:
            run_report["live_session_error"] = str(e)
            run_report["notes"].append(f"live session start failed: {e}")

    report_path = run_dir / "run_report.json"
    report_path.write_text(json.dumps(run_report, ensure_ascii=False, indent=2), encoding="utf-8")

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
