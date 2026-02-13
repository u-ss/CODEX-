# -*- coding: utf-8 -*-
"""
Stealth Research Runner — 本番テストスクリプト

ハイブリッドアプローチ:
  検索 = Bing RSS API（requestsベース、BOT検出なし）
  フェッチ = Playwright stealth（BOT回避付き）

WorkflowLogger統合でJSONLログ出力。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# パスを通す
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))

from stealth_fetcher import StealthFetcher
from search_adapter import SearchAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("stealth_research_test")


async def run_research(query: str, output_dir: Path) -> dict:
    """ハイブリッドモードでリサーチ実行"""
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "logs"

    results_summary = {
        "query": query,
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "phases": {},
        "overall": {},
    }

    # Phase 1: Bing RSS検索（requestsベース、BOT検出なし）
    logger.info("=== Phase 1: Bing RSS 検索 ===")
    adapter = SearchAdapter(fetcher=None, max_results=10)
    search_results_raw = adapter._search_bing_rss(query)

    search_results = [
        {"title": r.title, "url": r.url, "snippet": r.snippet, "position": r.position}
        for r in search_results_raw
    ]
    results_summary["phases"]["search"] = {
        "search_results_count": len(search_results),
        "results": search_results[:5],
    }
    logger.info("検索結果: %d件", len(search_results))
    for sr in search_results[:5]:
        logger.info("  %s. %s", sr["position"], sr["title"][:60])

    # Phase 2: Playwright stealth でページフェッチ
    logger.info("=== Phase 2: Playwright stealth ページ取得 ===")
    contents = []
    fetch_errors = 0
    access_log = []
    bot_count = 0

    if search_results:
        fetcher = StealthFetcher(
            headless=True,
            min_delay_sec=1.5,
            max_delay_sec=3.0,
            rate_limit_per_min=15,
            log_dir=log_dir,
        )
        try:
            await fetcher.start()

            for i, sr in enumerate(search_results[:3]):
                url = sr["url"]
                logger.info("フェッチ [%d/%d]: %s", i + 1, 3, url[:60])
                try:
                    r = await fetcher.fetch(url)
                    if r.text:
                        contents.append({
                            "url": url,
                            "title": sr["title"],
                            "text_length": len(r.text),
                            "text_preview": r.text[:200],
                        })
                        logger.info("  成功: %d chars, BOT: %s", len(r.text), r.bot_detected)
                    else:
                        fetch_errors += 1
                        logger.warning("  空レスポンス")
                except Exception as e:
                    fetch_errors += 1
                    logger.warning("  失敗: %s", e)

            access_log = fetcher.get_access_log()
            bot_count = sum(1 for e in access_log if e.get("bot_detected"))

        finally:
            # close を安全に実行（エラーは無視）
            try:
                await fetcher.close()
            except Exception:
                pass

    total_fetches = len(access_log)
    results_summary["phases"]["fetch"] = {
        "fetched_pages": len(contents),
        "fetch_errors": fetch_errors,
        "contents": contents,
    }
    results_summary["phases"]["audit"] = {
        "total_fetches": total_fetches,
        "bot_detections": bot_count,
        "bot_detection_rate": f"{bot_count / total_fetches * 100:.1f}%" if total_fetches else "N/A",
        "errors": fetch_errors,
        "error_rate": f"{fetch_errors / total_fetches * 100:.1f}%" if total_fetches else "N/A",
    }

    results_summary["overall"] = {
        "end_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_searches": 1,
        "total_fetches": total_fetches,
        "bot_detection_rate": results_summary["phases"]["audit"]["bot_detection_rate"],
        "error_rate": results_summary["phases"]["audit"]["error_rate"],
        "success": len(search_results) > 0 and len(contents) > 0,
    }

    logger.info("=== Phase 3: 監査 ===")
    logger.info("フェッチ %d件, BOT検出 %d件 (%s)",
                total_fetches, bot_count,
                results_summary["phases"]["audit"]["bot_detection_rate"])

    # 結果保存
    for name, data in [
        ("test_result.json", results_summary),
        ("search_log.json", [{"query": query, "engine": "bing_rss", "results": len(search_results)}]),
        ("access_log.json", access_log),
    ]:
        with open(output_dir / name, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info("結果保存: %s", output_dir)
    return results_summary


def main():
    parser = argparse.ArgumentParser(description="ステルスリサーチテスト")
    parser.add_argument("--query", default="AI 自動化 フリーランス 案件 2025", help="検索クエリ")
    parser.add_argument("--output-dir", default=None, help="出力ディレクトリ")
    args = parser.parse_args()

    if not args.output_dir:
        args.output_dir = f"_outputs/stealth_test/{time.strftime('%Y%m%d_%H%M%S')}"

    output_dir = Path(args.output_dir)

    # new_event_loop方式: asyncio.run()のloop closeを回避
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(run_research(args.query, output_dir))
    finally:
        # pending tasksをキャンセル
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        # キャンセルされたタスクの完了を待つ（エラーは無視）
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        try:
            loop.close()
        except Exception:
            pass

    # WorkflowLoggerでログ記録
    try:
        from workflow_logging_hook import logged_main, phase_scope
        with logged_main("research", "stealth_research_test") as wf_logger:
            wf_logger.set_input("query", args.query)
            wf_logger.set_input("output_dir", str(output_dir))
            with phase_scope(wf_logger, "HYBRID_RESEARCH_TEST", inputs={"query": args.query}) as p:
                p.set_output("search_results", result["phases"].get("search", {}).get("search_results_count", 0))
                p.set_output("fetched_pages", result["phases"].get("fetch", {}).get("fetched_pages", 0))
                p.set_output("bot_detection_rate", result["overall"].get("bot_detection_rate", "N/A"))
                p.set_output("success", result["overall"].get("success", False))
    except Exception as e:
        logger.warning("WorkflowLogger記録失敗: %s (正常動作に影響なし)", e)

    # 結果出力
    print()
    print("=" * 60)
    print("ステルスリサーチテスト結果")
    print("=" * 60)
    print(f"クエリ: {args.query}")
    print(f"検索結果: {result['phases'].get('search', {}).get('search_results_count', 0)}件")
    print(f"ページ取得: {result['phases'].get('fetch', {}).get('fetched_pages', 0)}件")
    print(f"BOT検出率: {result['overall'].get('bot_detection_rate', 'N/A')}")
    print(f"成功: {result['overall'].get('success', False)}")
    print(f"出力先: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
