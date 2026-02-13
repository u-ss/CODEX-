#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import threading
from typing import Callable


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _add_lib_path() -> None:
    lib_root = Path(__file__).resolve().parents[1] / "lib"
    if str(lib_root) not in sys.path:
        sys.path.insert(0, str(lib_root))


_add_lib_path()

from video_pipeline.constants import STEP_IDS
from video_pipeline.io import read_json
from video_pipeline.models import DirectorQualityReport, RunState
from video_pipeline.paths import PipelinePaths
from video_pipeline.state import StateManager
from video_pipeline.steps import (
    StepOutput,
    step_d1_direct,
    step_a1_validate,
    step_a2_collect,
    step_a3_probe,
    step_a4_tts,
    step_a5_timing,
    step_a6_props,
    step_a7_render,
    step_a8_mix,
    step_a9_finalize,
)


def now_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Video pipeline D1 + A0-A9 orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser, *, include_range: bool = False) -> None:
        target.add_argument("--project", required=True, help="project slug under projects/")
        target.add_argument("--run-id", default=None, help="run id (default: timestamp)")
        target.add_argument("--resume", action="store_true", help="reuse successful steps")
        target.add_argument("--force", action="store_true", help="force regeneration")
        target.add_argument("--dry-run", action="store_true", help="skip mutating operations")
        if include_range:
            target.add_argument("--from", dest="from_step", choices=STEP_IDS, default="d1_direct")
            target.add_argument("--to", dest="to_step", choices=STEP_IDS, default="a9_finalize")

    run_parser = sub.add_parser("run", help="run DAG orchestrator")
    add_common(run_parser, include_range=True)

    for step_id in STEP_IDS:
        step_parser = sub.add_parser(step_id, help=f"run single step: {step_id}")
        add_common(step_parser)

    return parser.parse_args()


StepFunction = Callable[[PipelinePaths, bool, bool], StepOutput]


def load_director_quality_errors(report_path: Path) -> list[str]:
    if not report_path.is_file():
        return []
    payload = read_json(report_path)
    report = DirectorQualityReport.model_validate(payload)
    return [finding.code for finding in report.findings if finding.severity == "error"]


def enforce_director_quality_gate(
    *,
    manager: StateManager,
    state: RunState,
    paths: PipelinePaths,
    state_lock: threading.Lock,
) -> None:
    errors = load_director_quality_errors(paths.sora_quality_report_path)
    if not errors:
        return
    message = f"director_quality_report_has_errors: {', '.join(errors)}"
    recovery_hint = "Fix D1 quality errors, then rerun with --resume --from d1_direct."
    with state_lock:
        manager.set_step_failed(
            state,
            "d1_direct",
            error_type="DirectorQualityGate",
            message=message,
            recovery_hint=recovery_hint,
        )
    raise RuntimeError(message)


def classify_hint(step_id: str, err: Exception) -> str:
    message = str(err).lower()
    if "storyboard" in message or step_id == "d1_direct":
        return "shot_list の director 設定と Sora Storyboard向け入力文を確認してください。"
    if "voicevox" in message:
        return "VOICEVOX engine を起動して再実行してください。"
    if "missing assets" in message:
        return "sora_inbox に shot_id を含む素材名で動画を投入してください。"
    if "ffprobe" in message or "ffmpeg" in message:
        return "ffmpeg/ffprobe の実行可否と素材破損を確認してください。"
    if "remotion" in message or "npx" in message:
        return "remotion依存をインストールし、npx remotion render を単体確認してください。"
    return f"{step_id} の入力成果物を確認してください。"


def step_runner(step_id: str) -> StepFunction:
    if step_id == "d1_direct":
        return lambda paths, force, dry_run: step_d1_direct(paths, force=force, dry_run=dry_run)
    if step_id == "a1_validate":
        return lambda paths, force, dry_run: step_a1_validate(paths)
    if step_id == "a2_collect":
        return lambda paths, force, dry_run: step_a2_collect(paths, force=force, dry_run=dry_run)
    if step_id == "a3_probe":
        return lambda paths, force, dry_run: step_a3_probe(paths, force=force)
    if step_id == "a4_tts":
        return lambda paths, force, dry_run: step_a4_tts(paths, force=force)
    if step_id == "a5_timing":
        return lambda paths, force, dry_run: step_a5_timing(paths)
    if step_id == "a6_props":
        return lambda paths, force, dry_run: step_a6_props(paths)
    if step_id == "a7_render":
        return lambda paths, force, dry_run: step_a7_render(paths)
    if step_id == "a8_mix":
        return lambda paths, force, dry_run: step_a8_mix(paths)
    if step_id == "a9_finalize":
        return lambda paths, force, dry_run: step_a9_finalize(paths)
    raise KeyError(f"unknown step: {step_id}")


def should_skip(state: RunState, step_id: str, *, resume: bool, force: bool) -> bool:
    if force:
        return False
    return resume and state.steps[step_id].status == "success"


def execute_step(
    *,
    manager: StateManager,
    state: RunState,
    paths: PipelinePaths,
    state_lock: threading.Lock,
    step_id: str,
    resume: bool,
    force: bool,
    dry_run: bool,
) -> None:
    with state_lock:
        if should_skip(state, step_id, resume=resume, force=force):
            manager.set_step_skipped(state, step_id, "resume: already success")
            return
        manager.set_step_running(state, step_id)
    try:
        output = step_runner(step_id)(paths, force, dry_run)
    except Exception as err:
        with state_lock:
            manager.set_step_failed(
                state,
                step_id,
                error_type=type(err).__name__,
                message=str(err),
                recovery_hint=classify_hint(step_id, err),
            )
        raise

    with state_lock:
        for key, value in output.artifacts.items():
            state.artifacts[key] = value
        state.qc_warnings.extend(output.warnings)
        manager.save(state)
        manager.set_step_success(state, step_id)


def resolve_selected_steps(from_step: str, to_step: str) -> list[str]:
    start = STEP_IDS.index(from_step)
    end = STEP_IDS.index(to_step)
    if start > end:
        raise ValueError("--from must be before --to")
    return STEP_IDS[start : end + 1]


def run_single_step(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    run_id = args.run_id or now_run_id()
    paths = PipelinePaths(repo_root=repo_root, project_slug=args.project, run_id=run_id)
    paths.ensure_runtime_dirs()
    manager = StateManager(paths.run_state_path, paths.log_path)
    state = manager.load_or_create(project_slug=args.project, run_id=run_id)
    state_lock = threading.Lock()

    step_id = args.command
    try:
        execute_step(
            manager=manager,
            state=state,
            paths=paths,
            state_lock=state_lock,
            step_id=step_id,
            resume=args.resume,
            force=args.force,
            dry_run=args.dry_run,
        )
    except Exception as err:
        print(f"[FAILED] {step_id}: {err}", file=sys.stderr)
        print(f"run_state: {paths.run_state_path}", file=sys.stderr)
        return 2
    print(f"[OK] {step_id} completed")
    print(f"run_state: {paths.run_state_path}")
    return 0


def run_dag(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    run_id = args.run_id or now_run_id()
    selected = resolve_selected_steps(args.from_step, args.to_step)
    paths = PipelinePaths(repo_root=repo_root, project_slug=args.project, run_id=run_id)
    paths.ensure_runtime_dirs()
    manager = StateManager(paths.run_state_path, paths.log_path)
    state = manager.load_or_create(project_slug=args.project, run_id=run_id)
    state_lock = threading.Lock()
    manager.log_event("dag_start", selected_steps=selected)

    try:
        if "d1_direct" in selected:
            execute_step(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
                step_id="d1_direct",
                resume=args.resume,
                force=args.force,
                dry_run=args.dry_run,
            )
            enforce_director_quality_gate(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
            )
        if "a1_validate" in selected:
            execute_step(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
                step_id="a1_validate",
                resume=args.resume,
                force=args.force,
                dry_run=args.dry_run,
            )

        parallel_targets = [step_id for step_id in ("a2_collect", "a4_tts") if step_id in selected]
        if parallel_targets:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(
                        execute_step,
                        manager=manager,
                        state=state,
                        paths=paths,
                        state_lock=state_lock,
                        step_id=step_id,
                        resume=args.resume,
                        force=args.force,
                        dry_run=args.dry_run,
                    )
                    for step_id in parallel_targets
                ]
                for future in futures:
                    future.result()

        if "a3_probe" in selected:
            execute_step(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
                step_id="a3_probe",
                resume=args.resume,
                force=args.force,
                dry_run=args.dry_run,
            )
        if "a5_timing" in selected:
            execute_step(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
                step_id="a5_timing",
                resume=args.resume,
                force=args.force,
                dry_run=args.dry_run,
            )
        if "a6_props" in selected:
            execute_step(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
                step_id="a6_props",
                resume=args.resume,
                force=args.force,
                dry_run=args.dry_run,
            )
        if "a7_render" in selected:
            execute_step(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
                step_id="a7_render",
                resume=args.resume,
                force=args.force,
                dry_run=args.dry_run,
            )
        if "a8_mix" in selected:
            execute_step(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
                step_id="a8_mix",
                resume=args.resume,
                force=args.force,
                dry_run=args.dry_run,
            )
        if "a9_finalize" in selected:
            execute_step(
                manager=manager,
                state=state,
                paths=paths,
                state_lock=state_lock,
                step_id="a9_finalize",
                resume=args.resume,
                force=args.force,
                dry_run=args.dry_run,
            )
    except Exception as err:
        manager.log_event("dag_failed", error=str(err))
        print(f"[FAILED] pipeline: {err}", file=sys.stderr)
        print(f"run_state: {paths.run_state_path}", file=sys.stderr)
        return 2

    manager.log_event("dag_success", final=state.artifacts.get("exports/final.mp4"))
    print("[OK] pipeline completed")
    print(f"run_state: {paths.run_state_path}")
    if "exports/final.mp4" in state.artifacts:
        print(f"final: {state.artifacts['exports/final.mp4']}")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "run":
        return run_dag(args)
    return run_single_step(args)


if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve()
    for _parent in _here.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in _sys.path:
                _sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(
        _run_logged_main("video_orchestrator", "video_pipeline", main, phase_name="VIDEO_PIPELINE_RUN")
    )

