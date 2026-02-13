"""
house_live_repl.py - Blenderライブ編集REPL
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ag_rpc_client import RpcClient, RpcError
from house_live_intent import interpret_instruction
from house_live_session import create_checkpoint, finalize_session, load_session, save_session


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(path: Path, entry: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _resolve_target(target: str, objects: List[Dict[str, Any]]) -> Optional[str]:
    if not target:
        return None
    names = [str(obj.get("name", "")) for obj in objects]

    # exact
    for name in names:
        if name == target:
            return name
    # case-insensitive
    lower = target.lower()
    for name in names:
        if name.lower() == lower:
            return name
    # substring
    for name in names:
        if target in name or lower in name.lower():
            return name
    return None


def _get_object_list(client: RpcClient) -> List[Dict[str, Any]]:
    result = client.list_objects()
    if isinstance(result, dict):
        return result.get("objects", []) or []
    return []


def _print_objects(objs: List[Dict[str, Any]]) -> None:
    if not objs:
        print("[AG] object: (none)")
        return
    print(f"[AG] object count={len(objs)}")
    for o in objs[:60]:
        loc = o.get("location", [0, 0, 0])
        print(f"- {o.get('name')} ({o.get('type')}) loc={loc}")
    if len(objs) > 60:
        print(f"... {len(objs) - 60} more")


def _ensure_factor3(value: Any) -> List[float]:
    if isinstance(value, list) and len(value) == 3:
        return [float(value[0]), float(value[1]), float(value[2])]
    return [1.0, 1.0, 1.0]


def _ensure_delta3(value: Any) -> List[float]:
    if isinstance(value, list) and len(value) == 3:
        return [float(value[0]), float(value[1]), float(value[2])]
    return [0.0, 0.0, 0.0]


def execute_parsed(
    parsed: Dict[str, Any],
    client: RpcClient,
    session_path: Path,
) -> Dict[str, Any]:
    session = load_session(session_path)
    edits: List[str] = []
    checkpoints: List[str] = []
    errors: List[str] = []

    def checkpoint(label: str) -> None:
        ck = create_checkpoint(client, session_path=session_path, label=label)
        checkpoints.append(str(ck))

    ops = parsed.get("ops", [])
    for op in ops:
        kind = op.get("op")
        try:
            if kind == "help":
                print("例: Roofを上に20cm / Doorを赤に / Windowを複製 / 一覧 / 戻す / やり直し")
                continue
            if kind == "list_objects":
                objs = _get_object_list(client)
                _print_objects(objs)
                continue
            if kind == "save_checkpoint":
                checkpoint("manual")
                edits.append("manual checkpoint")
                continue
            if kind == "undo":
                client.undo_step()
                checkpoint("undo")
                edits.append("undo")
                continue
            if kind == "redo":
                client.redo_step()
                checkpoint("redo")
                edits.append("redo")
                continue

            # 以下は target 必須
            objs = _get_object_list(client)
            requested = str(op.get("target", "")).strip()
            resolved = _resolve_target(requested, objs)
            if not resolved:
                errors.append(f"target not found: {requested}")
                continue

            if kind == "move_relative":
                info = client.get_object_info(resolved)
                if not info.get("ok"):
                    errors.append(f"get_object_info failed: {resolved}")
                    continue
                loc = info.get("location", [0.0, 0.0, 0.0])
                delta = _ensure_delta3(op.get("delta"))
                new_loc = [float(loc[0]) + delta[0], float(loc[1]) + delta[1], float(loc[2]) + delta[2]]
                client.transform(resolved, location=tuple(new_loc))
                checkpoint("move")
                edits.append(f"move {resolved} -> {new_loc}")
                continue

            if kind == "scale_multiply":
                info = client.get_object_info(resolved)
                if not info.get("ok"):
                    errors.append(f"get_object_info failed: {resolved}")
                    continue
                cur_scale = info.get("scale", [1.0, 1.0, 1.0])
                factor = _ensure_factor3(op.get("factor"))
                new_scale = [
                    float(cur_scale[0]) * factor[0],
                    float(cur_scale[1]) * factor[1],
                    float(cur_scale[2]) * factor[2],
                ]
                client.transform(resolved, scale=tuple(new_scale))
                checkpoint("scale")
                edits.append(f"scale {resolved} -> {new_scale}")
                continue

            if kind == "set_color":
                info = client.get_object_info(resolved)
                if not info.get("ok"):
                    errors.append(f"get_object_info failed: {resolved}")
                    continue
                mats = info.get("materials", []) or []
                if not mats:
                    errors.append(f"material not found on object: {resolved}")
                    continue
                rgba = op.get("color_rgba") or [0.8, 0.2, 0.2, 1.0]
                client.set_material_color(mats[0], tuple(float(x) for x in rgba))
                checkpoint("color")
                edits.append(f"set_color {resolved} ({mats[0]})")
                continue

            if kind == "duplicate_object":
                offset = _ensure_delta3(op.get("offset"))
                result = client.duplicate_object(resolved, new_name=op.get("new_name"), offset=tuple(offset))
                checkpoint("duplicate")
                edits.append(f"duplicate {resolved} -> {result.get('name')}")
                continue

            if kind == "rotate_relative":
                info = client.get_object_info(resolved)
                if not info.get("ok"):
                    errors.append(f"get_object_info failed: {resolved}")
                    continue
                cur_rot = info.get("rotation", [0.0, 0.0, 0.0])
                delta_rad = _ensure_delta3(op.get("delta_radians"))
                new_rot = [
                    float(cur_rot[0]) + delta_rad[0],
                    float(cur_rot[1]) + delta_rad[1],
                    float(cur_rot[2]) + delta_rad[2],
                ]
                client.transform(resolved, rotation=tuple(new_rot))
                checkpoint("rotate")
                degrees = op.get("degrees", 0)
                axis = op.get("axis", "z").upper()
                edits.append(f"rotate {resolved} {axis}+{degrees}deg")
                continue

            if kind == "delete_object":
                client.delete_object(resolved)
                checkpoint("delete")
                edits.append(f"delete {resolved}")
                continue

            errors.append(f"unsupported op: {kind}")
        except RpcError as e:
            errors.append(str(e))
        except Exception as e:
            errors.append(f"{kind}: {e}")

    # 軽量ポストチェック
    obj_count = len(_get_object_list(client))
    session = load_session(session_path)
    session["last_object_count"] = obj_count
    save_session(session_path, session)

    return {"edits": edits, "checkpoints": checkpoints, "errors": errors, "object_count": obj_count}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    p.add_argument("--once", default="")
    return p.parse_args()


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
        print("[AG] live edit mode started")
        print("[AG] command examples: Roofを上に20cm / Doorを赤に / Windowを複製 / 一覧 / 戻す")
        print("[AG] exit: 終了 or quit")

        while True:
            try:
                text = args.once or input("live> ").strip()
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
                except Exception as e:
                    print(f"[AG] finalize failed: {e}", file=sys.stderr)
                _append_log(log_path, {"ts": _now_iso(), "input": text, "parsed": parsed, "applied": True, "quit": True})
                break

            if parsed.get("requires_confirmation"):
                ans = input(f"[AG] confirm ({parsed.get('message')}) [y/N]: ").strip().lower()
                if ans not in ("y", "yes"):
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
