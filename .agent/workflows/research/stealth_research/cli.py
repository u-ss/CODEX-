# -*- coding: utf-8 -*-
"""
CLIエントリポイント — ステルスリサーチツール v2.0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import StealthResearchConfig
from .orchestrator import Orchestrator


def main():
    parser = argparse.ArgumentParser(
        description="ステルスリサーチツール v2.1 — BOT検知→停止→迂回の最強リサーチ"
    )
    parser.add_argument(
        "--version",
        action="version",
        version="stealth_research v2.1.0",
    )
    parser.add_argument(
        "queries",
        nargs="+",
        help="検索クエリ（複数可）",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="出力ディレクトリ（デフォルト: _outputs/stealth_research/YYYYMMDD_HHMMSS）",
    )
    parser.add_argument(
        "--max-urls",
        type=int,
        default=50,
        help="最大URL数（デフォルト: 50）",
    )
    parser.add_argument(
        "--max-time",
        type=float,
        default=600.0,
        help="最大実行時間（秒、デフォルト: 600）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON形式で結果を出力",
    )

    args = parser.parse_args()

    # 出力ディレクトリ
    if args.output:
        out_dir = args.output
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_dir = f"_outputs/stealth_research/{ts}"

    # 設定
    config = StealthResearchConfig()
    config.budget.max_urls = args.max_urls
    config.budget.max_time_sec = args.max_time
    config.log.artifact_dir = out_dir

    # 実行
    orchestrator = Orchestrator(config)
    try:
        result = orchestrator.run(args.queries, out_dir)
    finally:
        orchestrator.close()

    # 結果出力
    if args.json:
        # JSON出力（コンテンツは除外）
        output = {
            "run_id": result.run_id,
            "queries": result.queries,
            "urls_found": len(result.urls_found),
            "fetched_count": len(result.fetched_contents),
            "summary": result.summary,
            "verification": result.verification,
            "output_dir": result.output_dir,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        # テキスト出力
        v = result.verification
        verified = v.get("verified_success", False)
        checks = v.get("checks", [])

        print(f"\n{'='*60}")
        print(f"  ステルスリサーチ v2.0 — 実行結果")
        print(f"{'='*60}")
        print(f"  Run ID:     {result.run_id}")
        print(f"  クエリ数:   {len(result.queries)}")
        print(f"  URL発見:    {len(result.urls_found)}")
        print(f"  取得成功:   {len(result.fetched_contents)}")
        print(f"  出力先:     {result.output_dir}")
        print(f"{'='*60}")
        print(f"  VERIFICATION: {'✅ PASS' if verified else '❌ FAIL'}")
        for c in checks:
            status = "✅" if c["passed"] else "❌"
            print(f"    {status} {c['name']}: {c['detail']}")
        print(f"{'='*60}")

    return 0 if result.verification.get("verified_success") else 1


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        sys.exit(main())
    else:
        raise SystemExit(
            run_logged_main(
                "research",
                "stealth_research_cli",
                main,
                phase_name="STEALTH_RESEARCH_CLI_RUN",
            )
        )
