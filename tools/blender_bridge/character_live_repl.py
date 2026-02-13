"""
character_live_repl.py - キャラクター向けライブ編集REPL
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from ag_rpc_client import RpcClient
from character_live_intent import interpret_instruction
from house_live_repl import execute_parsed
from house_live_session import finalize_session, load_session


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(path: Path, entry: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--once", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_path = Path(args.session).resolve()
    if not session_path.exists():
        print(f"[AG] session file missing: {session_path}", file=sys.stderr)
        return 1

    session = load_session(session_path)
    client = RpcClient(
        host=session["host"],
        port=int(session["port"]),
        token=session.get("token", ""),
        timeout=15.0,
        max_retries=2,
        retry_backoff=0.2,
        reuse_connection=True,
    )
    log_path = Path(session.get("live_edit_log", session_path.parent / "live_edit_log.jsonl"))

    try:
        print("[AG] character live edit mode started")
        print("[AG] examples: 髪を少し長く / 目を大きく / 腕を小さく / 顔を白に / 一覧 / 戻す")
        print("[AG] exit: 終了 or quit")

        while True:
            try:
                text = args.once or input("char-live> ").strip()
            except (EOFError, KeyboardInterrupt):
                text = "終了"
            if not text:
                if args.once:
                    break
                continue

            parsed = interpret_instruction(text)
            if parsed.get("intent") == "unknown":
                print(f"[AG] {parsed.get('message')}")
                _append_log(log_path, {"ts": _now_iso(), "input": text, "parsed": parsed, "applied": False})
                if args.once:
                    break
                continue

            if parsed.get("intent") == "quit":
                try:
                    final_path = finalize_session(client, session_path=session_path, final_name="final_live.blend")
                    print(f"[AG] finalized: {final_path}")
                except Exception as exc:
                    print(f"[AG] finalize failed: {exc}", file=sys.stderr)
                _append_log(log_path, {"ts": _now_iso(), "input": text, "parsed": parsed, "applied": True, "quit": True})
                break

            if parsed.get("requires_confirmation"):
                answer = input(f"[AG] confirm ({parsed.get('message')}) [y/N]: ").strip().lower()
                if answer not in ("y", "yes"):
                    print("[AG] canceled")
                    _append_log(log_path, {"ts": _now_iso(), "input": text, "parsed": parsed, "applied": False, "canceled": True})
                    if args.once:
                        break
                    continue

            result = execute_parsed(parsed=parsed, client=client, session_path=session_path)
            if result["errors"]:
                print("[AG] errors:")
                for err in result["errors"]:
                    print(f"- {err}")
            else:
                print(f"[AG] applied edits={len(result['edits'])}, checkpoints={len(result['checkpoints'])}, objects={result['object_count']}")

            _append_log(
                log_path,
                {
                    "ts": _now_iso(),
                    "input": text,
                    "parsed": parsed,
                    "result": result,
                    "applied": len(result["edits"]) > 0 or len(result["checkpoints"]) > 0,
                },
            )

            if args.once:
                break
            args.once = ""
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
