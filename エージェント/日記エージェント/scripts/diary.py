#!/usr/bin/env python3
"""
日記エージェント CLI スクリプト v1.0.0
メモ帳/日記/タスクをMarkdown + YAML frontmatterで管理する。
"""

import argparse
import json
import os
import sys
import random
import string
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- 定数 ---
JST = timezone(timedelta(hours=9))
# _data/diary/ をデータディレクトリとして使用
BASE_DIR = Path(__file__).resolve().parents[3]  # antigravity/
DATA_DIR = BASE_DIR / "_data" / "diary"
INDEX_FILE = DATA_DIR / "index.json"
VALID_TYPES = ("diary", "task", "note")
VALID_STATUSES = ("active", "done", "archived")


def now_jst() -> datetime:
    """現在のJST日時を取得"""
    return datetime.now(JST)


def generate_id() -> str:
    """ユニークID生成（YYYYMMDD_HHMMSS_xxxx）"""
    ts = now_jst().strftime("%Y%m%d_%H%M%S")
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}_{suffix}"


def ensure_dirs():
    """データディレクトリを作成"""
    for t in VALID_TYPES:
        (DATA_DIR / t).mkdir(parents=True, exist_ok=True)


def load_index() -> list:
    """index.jsonを読み込む"""
    if INDEX_FILE.exists():
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_index(index: list):
    """index.jsonを保存"""
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def parse_frontmatter(filepath: Path) -> tuple:
    """Markdownファイルからfrontmatterと本文を分離して返す"""
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    # YAML frontmatterをパース（yaml非依存の簡易パーサー）
    meta = {}
    for line in parts[1].strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ": " in line:
            key, val = line.split(": ", 1)
            key = key.strip()
            val = val.strip()
            # リスト型の処理
            if val.startswith("[") and val.endswith("]"):
                items = val[1:-1]
                meta[key] = [
                    item.strip().strip('"').strip("'")
                    for item in items.split(",")
                    if item.strip()
                ]
            # 文字列型（クォート除去）
            elif val.startswith('"') and val.endswith('"'):
                meta[key] = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                meta[key] = val[1:-1]
            else:
                meta[key] = val

    body = parts[2].strip()
    return meta, body


def build_frontmatter(meta: dict) -> str:
    """メタデータからYAML frontmatter文字列を生成"""
    lines = ["---"]
    for key, val in meta.items():
        if isinstance(val, list):
            items = ", ".join(f'"{v}"' for v in val)
            lines.append(f'{key}: [{items}]')
        else:
            lines.append(f'{key}: "{val}"')
    lines.append("---")
    return "\n".join(lines)


def write_entry(entry_id: str, entry_type: str, meta: dict, body: str):
    """エントリーをMarkdownファイルとして書き出す"""
    filepath = DATA_DIR / entry_type / f"{entry_id}.md"
    content = build_frontmatter(meta) + "\n\n" + body + "\n"
    filepath.write_text(content, encoding="utf-8")
    return filepath


def update_index_entry(index: list, meta: dict, entry_type: str, entry_id: str) -> list:
    """index内のエントリーを追加/更新"""
    # 既存エントリーを削除
    index = [e for e in index if e.get("id") != entry_id]
    # 新しいエントリーを追加
    index_entry = {
        "id": meta["id"],
        "type": meta["type"],
        "title": meta["title"],
        "tags": meta.get("tags", []),
        "created": meta["created"],
        "updated": meta.get("updated", meta["created"]),
        "status": meta.get("status", "active"),
        "file": f"{entry_type}/{entry_id}.md"
    }
    index.append(index_entry)
    return index


# ============================================================
# コマンド実装
# ============================================================

def cmd_add(args):
    """新規エントリー追加"""
    ensure_dirs()

    entry_type = args.type or "note"
    if entry_type not in VALID_TYPES:
        print(f"エラー: typeは {VALID_TYPES} のいずれかを指定してください", file=sys.stderr)
        sys.exit(1)

    entry_id = generate_id()
    ts = now_jst().isoformat()
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

    # 本文の取得
    body = ""
    if args.body:
        body = args.body
    elif args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")

    meta = {
        "id": entry_id,
        "type": entry_type,
        "title": args.title,
        "tags": tags,
        "created": ts,
        "updated": ts,
        "status": "active",
    }

    filepath = write_entry(entry_id, entry_type, meta, body)

    # インデックス更新
    index = load_index()
    index = update_index_entry(index, meta, entry_type, entry_id)
    save_index(index)

    result = {
        "status": "ok",
        "action": "add",
        "id": entry_id,
        "type": entry_type,
        "title": args.title,
        "file": str(filepath.relative_to(BASE_DIR)),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_search(args):
    """キーワード検索"""
    keyword = args.keyword.lower()
    index = load_index()

    results = []
    for entry in index:
        # タイプフィルタ
        if args.type and entry["type"] != args.type:
            continue
        # タグフィルタ
        if args.tags:
            filter_tags = [t.strip() for t in args.tags.split(",")]
            if not any(t in entry.get("tags", []) for t in filter_tags):
                continue

        # タイトル・タグ検索
        title_match = keyword in entry.get("title", "").lower()
        tag_match = any(keyword in t.lower() for t in entry.get("tags", []))

        # 本文検索
        body_match = False
        filepath = DATA_DIR / entry["file"]
        if filepath.exists():
            _, body = parse_frontmatter(filepath)
            body_match = keyword in body.lower()

        if title_match or tag_match or body_match:
            match_info = {
                "id": entry["id"],
                "type": entry["type"],
                "title": entry["title"],
                "tags": entry.get("tags", []),
                "created": entry["created"],
                "status": entry.get("status", "active"),
                "match_in": [],
            }
            if title_match:
                match_info["match_in"].append("title")
            if tag_match:
                match_info["match_in"].append("tags")
            if body_match:
                match_info["match_in"].append("body")
            results.append(match_info)

    output = {
        "status": "ok",
        "action": "search",
        "keyword": args.keyword,
        "count": len(results),
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_list(args):
    """フィルタ付き一覧表示"""
    index = load_index()

    # フィルタ適用
    filtered = index
    if args.type:
        filtered = [e for e in filtered if e["type"] == args.type]
    if args.status:
        filtered = [e for e in filtered if e.get("status") == args.status]
    if args.tags:
        filter_tags = [t.strip() for t in args.tags.split(",")]
        filtered = [
            e for e in filtered
            if any(t in e.get("tags", []) for t in filter_tags)
        ]

    # 日付の新しい順でソート
    filtered.sort(key=lambda e: e.get("created", ""), reverse=True)

    # リミット適用
    limit = args.limit or 20
    filtered = filtered[:limit]

    output = {
        "status": "ok",
        "action": "list",
        "count": len(filtered),
        "entries": filtered,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_get(args):
    """特定エントリーの内容取得"""
    index = load_index()
    entry = next((e for e in index if e["id"] == args.id), None)

    if not entry:
        print(json.dumps({
            "status": "error",
            "action": "get",
            "message": f"ID '{args.id}' のエントリーが見つかりません"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    filepath = DATA_DIR / entry["file"]
    if not filepath.exists():
        print(json.dumps({
            "status": "error",
            "action": "get",
            "message": f"ファイル '{entry['file']}' が見つかりません"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    meta, body = parse_frontmatter(filepath)

    output = {
        "status": "ok",
        "action": "get",
        "meta": meta,
        "body": body,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_edit(args):
    """エントリーの内容更新"""
    index = load_index()
    entry = next((e for e in index if e["id"] == args.id), None)

    if not entry:
        print(json.dumps({
            "status": "error",
            "action": "edit",
            "message": f"ID '{args.id}' のエントリーが見つかりません"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    filepath = DATA_DIR / entry["file"]
    meta, body = parse_frontmatter(filepath)

    # 更新対象のフィールドを適用
    if args.title:
        meta["title"] = args.title
        entry["title"] = args.title
    if args.tags is not None:
        new_tags = [t.strip() for t in args.tags.split(",")]
        meta["tags"] = new_tags
        entry["tags"] = new_tags
    if args.body:
        body = args.body
    elif args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    if args.append:
        body = body + "\n\n" + args.append

    meta["updated"] = now_jst().isoformat()
    entry["updated"] = meta["updated"]

    write_entry(args.id, entry["type"], meta, body)

    # インデックス更新
    index = update_index_entry(index, meta, entry["type"], args.id)
    save_index(index)

    output = {
        "status": "ok",
        "action": "edit",
        "id": args.id,
        "title": meta.get("title", ""),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_done(args):
    """タスクを完了に変更"""
    index = load_index()
    entry = next((e for e in index if e["id"] == args.id), None)

    if not entry:
        print(json.dumps({
            "status": "error",
            "action": "done",
            "message": f"ID '{args.id}' のエントリーが見つかりません"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    filepath = DATA_DIR / entry["file"]
    meta, body = parse_frontmatter(filepath)

    meta["status"] = "done"
    meta["updated"] = now_jst().isoformat()

    write_entry(args.id, entry["type"], meta, body)

    # インデックス更新
    entry["status"] = "done"
    entry["updated"] = meta["updated"]
    index = update_index_entry(index, meta, entry["type"], args.id)
    save_index(index)

    output = {
        "status": "ok",
        "action": "done",
        "id": args.id,
        "title": meta.get("title", ""),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_today(args):
    """今日のエントリー一覧"""
    index = load_index()
    today_str = now_jst().strftime("%Y-%m-%d")

    todays = [
        e for e in index
        if e.get("created", "").startswith(today_str)
    ]

    # フィルタ
    if args.type:
        todays = [e for e in todays if e["type"] == args.type]

    todays.sort(key=lambda e: e.get("created", ""), reverse=True)

    output = {
        "status": "ok",
        "action": "today",
        "date": today_str,
        "count": len(todays),
        "entries": todays,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_summary(args):
    """最近のエントリー要約出力"""
    index = load_index()
    days = args.days or 7
    cutoff = (now_jst() - timedelta(days=days)).isoformat()

    recent = [
        e for e in index
        if e.get("created", "") >= cutoff
    ]

    if args.type:
        recent = [e for e in recent if e["type"] == args.type]

    recent.sort(key=lambda e: e.get("created", ""), reverse=True)

    # サマリー用に本文のプレビュー（先頭100文字）を追加
    summary_entries = []
    for e in recent:
        filepath = DATA_DIR / e["file"]
        preview = ""
        if filepath.exists():
            _, body = parse_frontmatter(filepath)
            preview = body[:100].replace("\n", " ") + ("..." if len(body) > 100 else "")

        summary_entries.append({
            "id": e["id"],
            "type": e["type"],
            "title": e["title"],
            "tags": e.get("tags", []),
            "created": e["created"],
            "status": e.get("status", "active"),
            "preview": preview,
        })

    output = {
        "status": "ok",
        "action": "summary",
        "period_days": days,
        "count": len(summary_entries),
        "entries": summary_entries,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_delete(args):
    """エントリー削除（archivedに変更）"""
    index = load_index()
    entry = next((e for e in index if e["id"] == args.id), None)

    if not entry:
        print(json.dumps({
            "status": "error",
            "action": "delete",
            "message": f"ID '{args.id}' のエントリーが見つかりません"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    filepath = DATA_DIR / entry["file"]
    meta, body = parse_frontmatter(filepath)

    meta["status"] = "archived"
    meta["updated"] = now_jst().isoformat()

    write_entry(args.id, entry["type"], meta, body)

    entry["status"] = "archived"
    entry["updated"] = meta["updated"]
    index = update_index_entry(index, meta, entry["type"], args.id)
    save_index(index)

    output = {
        "status": "ok",
        "action": "delete",
        "id": args.id,
        "title": meta.get("title", ""),
        "note": "ステータスをarchivedに変更しました（ファイルは残ります）",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_rebuild_index(args):
    """index.jsonを全ファイルから再構築"""
    ensure_dirs()
    index = []

    for entry_type in VALID_TYPES:
        type_dir = DATA_DIR / entry_type
        if not type_dir.exists():
            continue
        for md_file in type_dir.glob("*.md"):
            meta, _ = parse_frontmatter(md_file)
            if not meta.get("id"):
                # idが無い場合はファイル名から推定
                meta["id"] = md_file.stem
            if not meta.get("type"):
                meta["type"] = entry_type
            if not meta.get("title"):
                meta["title"] = md_file.stem

            index_entry = {
                "id": meta["id"],
                "type": meta.get("type", entry_type),
                "title": meta.get("title", ""),
                "tags": meta.get("tags", []),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "status": meta.get("status", "active"),
                "file": f"{entry_type}/{md_file.name}"
            }
            index.append(index_entry)

    index.sort(key=lambda e: e.get("created", ""), reverse=True)
    save_index(index)

    output = {
        "status": "ok",
        "action": "rebuild-index",
        "count": len(index),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ============================================================
# CLI エントリーポイント
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="日記エージェント CLI v1.0.0")
    subparsers = parser.add_subparsers(dest="command", help="実行するコマンド")

    # --- add ---
    p_add = subparsers.add_parser("add", help="新規エントリー追加")
    p_add.add_argument("--title", required=True, help="タイトル")
    p_add.add_argument("--type", default="note", choices=VALID_TYPES, help="タイプ (diary/task/note)")
    p_add.add_argument("--tags", default="", help="タグ（カンマ区切り）")
    p_add.add_argument("--body", default="", help="本文")
    p_add.add_argument("--body-file", default="", help="本文ファイルパス")
    p_add.set_defaults(func=cmd_add)

    # --- search ---
    p_search = subparsers.add_parser("search", help="キーワード検索")
    p_search.add_argument("keyword", help="検索キーワード")
    p_search.add_argument("--type", choices=VALID_TYPES, help="タイプフィルタ")
    p_search.add_argument("--tags", default="", help="タグフィルタ")
    p_search.set_defaults(func=cmd_search)

    # --- list ---
    p_list = subparsers.add_parser("list", help="一覧表示")
    p_list.add_argument("--type", choices=VALID_TYPES, help="タイプフィルタ")
    p_list.add_argument("--status", choices=VALID_STATUSES, help="ステータスフィルタ")
    p_list.add_argument("--tags", default="", help="タグフィルタ")
    p_list.add_argument("--limit", type=int, default=20, help="最大表示件数")
    p_list.set_defaults(func=cmd_list)

    # --- get ---
    p_get = subparsers.add_parser("get", help="特定エントリー取得")
    p_get.add_argument("id", help="エントリーID")
    p_get.set_defaults(func=cmd_get)

    # --- edit ---
    p_edit = subparsers.add_parser("edit", help="エントリー編集")
    p_edit.add_argument("id", help="エントリーID")
    p_edit.add_argument("--title", help="新しいタイトル")
    p_edit.add_argument("--tags", help="新しいタグ（カンマ区切り）")
    p_edit.add_argument("--body", help="新しい本文")
    p_edit.add_argument("--body-file", help="本文ファイルパス")
    p_edit.add_argument("--append", help="本文に追記する内容")
    p_edit.set_defaults(func=cmd_edit)

    # --- done ---
    p_done = subparsers.add_parser("done", help="タスク完了")
    p_done.add_argument("id", help="エントリーID")
    p_done.set_defaults(func=cmd_done)

    # --- today ---
    p_today = subparsers.add_parser("today", help="今日のエントリー一覧")
    p_today.add_argument("--type", choices=VALID_TYPES, help="タイプフィルタ")
    p_today.set_defaults(func=cmd_today)

    # --- summary ---
    p_summary = subparsers.add_parser("summary", help="最近のエントリー要約")
    p_summary.add_argument("--days", type=int, default=7, help="対象日数")
    p_summary.add_argument("--type", choices=VALID_TYPES, help="タイプフィルタ")
    p_summary.set_defaults(func=cmd_summary)

    # --- delete ---
    p_delete = subparsers.add_parser("delete", help="エントリー削除（archived化）")
    p_delete.add_argument("id", help="エントリーID")
    p_delete.set_defaults(func=cmd_delete)

    # --- rebuild-index ---
    p_rebuild = subparsers.add_parser("rebuild-index", help="インデックス再構築")
    p_rebuild.set_defaults(func=cmd_rebuild_index)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    # WorkflowLogger統合
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[3] / ".agent" / "workflows" / "shared"))
        from workflow_logging_hook import logged_main
        logged_main("diary", main)
    except ImportError:
        main()
