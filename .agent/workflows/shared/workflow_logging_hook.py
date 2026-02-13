#!/usr/bin/env python3
"""
workflow_logging_hook - 全エージェント共通のログ記録フック

WorkflowLoggerを簡単に利用するためのラッパー関数。
各エージェントのSKILL.md/WORKFLOW.mdからimportして使用する。

使用例:
    import sys; sys.path.insert(0, '.agent/workflows/shared')
    from workflow_logging_hook import logged_main, phase_scope

    with logged_main("research", "deep_research") as logger:
        with phase_scope(logger, "SEARCH", inputs={"query": q}):
            # 処理
            logger.set_output("results", count)
"""
import sys
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Optional, Callable

# WorkflowLoggerへのパス解決
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # .agent/workflows/shared → root
_AUTONOMY_PATH = _WORKSPACE_ROOT / "scripts" / "autonomy"
if str(_AUTONOMY_PATH) not in sys.path:
    sys.path.insert(0, str(_AUTONOMY_PATH))


@contextmanager
def logged_main(agent: str, workflow: str = ""):
    """エージェント実行全体をログ記録するコンテキストマネージャ

    Args:
        agent: エージェント名（例: "research", "code", "desktop"）
        workflow: ワークフロー名（省略時はagent名）

    Yields:
        WorkflowLogger インスタンス

    使用例:
        with logged_main("research", "deep_research") as logger:
            with phase_scope(logger, "SEARCH"):
                logger.set_output("results", 15)
    """
    from workflow_logger import WorkflowLogger
    logger = WorkflowLogger(agent=agent, workflow=workflow or agent)
    try:
        yield logger
    except Exception as e:
        # エラー発生時もログに記録
        logger.add_phase_direct(
            phase_name="_ERROR",
            status="error",
            errors=[{"type": type(e).__name__, "message": str(e)[:500]}],
        )
        raise
    finally:
        try:
            logger.finalize()
        except Exception:
            pass  # finalize失敗は無視


@contextmanager
def phase_scope(logger, phase_name: str, inputs: dict = None):
    """フェーズを簡単に記録するコンテキストマネージャ

    Args:
        logger: WorkflowLogger インスタンス
        phase_name: フェーズ名（例: "RESEARCH", "CODE", "TEST"）
        inputs: フェーズへの入力辞書

    Yields:
        logger（set_output/add_metricで結果を記録可能）

    使用例:
        with phase_scope(logger, "SEARCH", inputs={"query": "MCP"}) as log:
            log.set_output("results", 15)
            log.add_metric("pages", 42)
    """
    with logger.phase(phase_name) as p:
        if inputs:
            for k, v in inputs.items():
                p.set_input(k, v)
        yield p


def run_logged_main(
    agent: str,
    workflow: str,
    main_func: Callable[[], Any],
    *,
    phase_name: str = "RUN",
    argv: Optional[list[str]] = None,
) -> int:
    """main関数をWorkflowLogger付きで実行する。

    Args:
        agent: エージェント名
        workflow: ワークフロー名
        main_func: 実行するmain関数（戻り値はint/None想定）
        phase_name: 1つ目の大域フェーズ名
        argv: 入力引数（省略時はsys.argv[1:]）

    Returns:
        終了コード（int）
    """
    args = list(sys.argv[1:] if argv is None else argv)
    with logged_main(agent, workflow) as logger:
        with phase_scope(logger, phase_name, inputs={"argv": args}) as p:
            try:
                result = main_func()
            except Exception as exc:
                p.add_error(str(exc), error_type=type(exc).__name__)
                verification_id = logger.record_verification(
                    checks=[{"name": "exit_code_zero", "pass": False}],
                    passed=False,
                    evidence={"exit_code": 1, "argv": args, "exception": str(exc)},
                )
                logger.claim(
                    "entrypoint_completed",
                    evidence_refs=[verification_id],
                    claimed_success=False,
                )
                raise

            if result is None:
                exit_code = 0
            elif isinstance(result, bool):
                exit_code = 0 if result else 1
            elif isinstance(result, int):
                exit_code = result
            else:
                # 非int戻り値は成功扱いで記録のみ残す
                p.set_output("return_type", type(result).__name__)
                exit_code = 0
            p.set_output("exit_code", exit_code)
            if exit_code != 0:
                p.add_error(f"non-zero exit code: {exit_code}", error_type="ExitCode")

            ok = exit_code == 0
            verification_id = logger.record_verification(
                checks=[{"name": "exit_code_zero", "pass": ok}],
                passed=ok,
                evidence={"exit_code": exit_code, "argv": args},
            )
            logger.claim(
                "entrypoint_completed",
                evidence_refs=[verification_id],
                claimed_success=ok,
            )
            return exit_code


async def run_logged_main_async(
    agent: str,
    workflow: str,
    main_func: Callable[[], Any],
    *,
    phase_name: str = "RUN",
    argv: Optional[list[str]] = None,
) -> int:
    """async main関数をWorkflowLogger付きで実行する。"""
    args = list(sys.argv[1:] if argv is None else argv)
    with logged_main(agent, workflow) as logger:
        with phase_scope(logger, phase_name, inputs={"argv": args}) as p:
            try:
                result = await main_func()
            except Exception as exc:
                p.add_error(str(exc), error_type=type(exc).__name__)
                verification_id = logger.record_verification(
                    checks=[{"name": "exit_code_zero", "pass": False}],
                    passed=False,
                    evidence={"exit_code": 1, "argv": args, "exception": str(exc)},
                )
                logger.claim(
                    "entrypoint_completed",
                    evidence_refs=[verification_id],
                    claimed_success=False,
                )
                raise

            if result is None:
                exit_code = 0
            elif isinstance(result, bool):
                exit_code = 0 if result else 1
            elif isinstance(result, int):
                exit_code = result
            else:
                p.set_output("return_type", type(result).__name__)
                exit_code = 0
            p.set_output("exit_code", exit_code)
            if exit_code != 0:
                p.add_error(f"non-zero exit code: {exit_code}", error_type="ExitCode")

            ok = exit_code == 0
            verification_id = logger.record_verification(
                checks=[{"name": "exit_code_zero", "pass": ok}],
                passed=ok,
                evidence={"exit_code": exit_code, "argv": args},
            )
            logger.claim(
                "entrypoint_completed",
                evidence_refs=[verification_id],
                claimed_success=ok,
            )
            return exit_code


def _get_log_root() -> Path:
    """WorkflowLoggerと同じログルートを取得"""
    try:
        from workflow_logger import WORKSPACE_ROOT
        return WORKSPACE_ROOT
    except ImportError:
        return _WORKSPACE_ROOT


def resolve_latest_log(agent: str) -> dict:
    """指定エージェントの最新ログ情報を返す

    Args:
        agent: エージェント名

    Returns:
        {"agent", "log_path", "summary_path", "timestamp"} or 空dict
    """
    import json
    latest = _get_log_root() / "_logs" / "autonomy" / agent / "latest.json"
    if latest.exists():
        try:
            with open(latest, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def bundle_logs_for_codex(agent: str, last_n: int = 3) -> str:
    """直近N回分のログサマリーをCODEXAPP送信用テキストにまとめる

    Args:
        agent: エージェント名
        last_n: 取得するサイクル数

    Returns:
        テキスト形式のログサマリー（CODEXAPP送信用）
    """
    import json
    from datetime import datetime

    log_base = _get_log_root() / "_logs" / "autonomy" / agent
    if not log_base.exists():
        return f"エージェント'{agent}'のログが見つかりません。"

    # 日付ディレクトリを降順でスキャン
    summaries = []
    for date_dir in sorted(log_base.iterdir(), reverse=True):
        if not date_dir.is_dir() or date_dir.name == "latest.json":
            continue
        for summary_file in sorted(date_dir.glob("*_summary.json"), reverse=True):
            try:
                with open(summary_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                summaries.append(data)
                if len(summaries) >= last_n:
                    break
            except Exception:
                continue
        if len(summaries) >= last_n:
            break

    if not summaries:
        return f"エージェント'{agent}'のログサマリーが見つかりません。"

    # テキスト生成
    lines = [f"=== {agent} エージェント ログ (直近{len(summaries)}件) ===\n"]
    for s in summaries:
        lines.append(f"--- Run: {s.get('run_id', '?')} ---")
        lines.append(f"  開始: {s.get('started_at', '?')}")
        lines.append(f"  完了: {s.get('completed_at', '?')}")
        lines.append(f"  フェーズ: {s.get('total_phases', 0)}件 "
                     f"(成功={s.get('passed_phases', 0)}, "
                     f"失敗={s.get('failed_phases', 0)})")
        lines.append(f"  状態: {s.get('final_status', '?')}")
        lines.append(f"  所要時間: {s.get('total_duration_ms', 0)}ms")
        # 検証結果
        v = s.get("verification", {})
        if v:
            lines.append(f"  検証: integrity={v.get('avg_integrity_score', '?')}, "
                        f"verdict={v.get('overall_verdict', '?')}")
        lines.append(f"  ログ: {s.get('log_path', '?')}")
        lines.append("")

    return "\n".join(lines)
