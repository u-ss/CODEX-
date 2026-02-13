from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional


ROOT = Path(__file__).resolve().parents[2]
LEARNING_DIR = ROOT / "knowledge" / "learning"
DB_PATH = LEARNING_DIR / "learning.db"
EVENTS_JSONL = LEARNING_DIR / "events.jsonl"


@dataclasses.dataclass
class AgentEvent:
    agent: str
    intent: str
    intent_class: str
    outcome: str  # SUCCESS|FAILURE|PARTIAL|UNKNOWN
    signature_key: str = ""
    ts_start: str = ""
    ts_end: str = ""
    error_type: str = ""
    root_cause: str = ""
    fix: str = ""
    confidence: float = 0.0
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat()


def _ensure_dirs() -> None:
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_dirs()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _init_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts_end TEXT NOT NULL,
          agent TEXT NOT NULL,
          intent TEXT NOT NULL,
          intent_class TEXT NOT NULL,
          outcome TEXT NOT NULL,
          signature_key TEXT,
          error_type TEXT,
          root_cause TEXT,
          fix TEXT,
          confidence REAL,
          meta_json TEXT
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_events_lookup
        ON events(agent, intent_class, signature_key, ts_end)
        """
    )

    # Minimal tables to keep /monitor's queries sane when used later.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS locator_stats (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signature_key TEXT NOT NULL,
          intent_class TEXT NOT NULL,
          locator_key TEXT NOT NULL,
          success_count INTEGER NOT NULL DEFAULT 0,
          failure_count INTEGER NOT NULL DEFAULT 0,
          last_ts_end TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS failure_patterns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signature_key TEXT NOT NULL,
          intent_class TEXT NOT NULL,
          error_type TEXT NOT NULL,
          root_cause TEXT NOT NULL,
          count INTEGER NOT NULL DEFAULT 0,
          last_ts_end TEXT NOT NULL
        )
        """
    )
    con.commit()


class LearningClient:
    def __init__(self) -> None:
        self._con = _connect()
        _init_schema(self._con)

    def report_outcome(self, evt: AgentEvent) -> None:
        if not evt.ts_end:
            evt.ts_end = _now_iso()
        meta_json = json.dumps(evt.meta or {}, ensure_ascii=False)
        self._con.execute(
            """
            INSERT INTO events(
              ts_end, agent, intent, intent_class, outcome, signature_key,
              error_type, root_cause, fix, confidence, meta_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evt.ts_end,
                evt.agent,
                evt.intent,
                evt.intent_class,
                evt.outcome,
                evt.signature_key,
                evt.error_type,
                evt.root_cause,
                evt.fix,
                float(evt.confidence or 0.0),
                meta_json,
            ),
        )
        self._con.commit()

        # Append-only audit log.
        _ensure_dirs()
        with EVENTS_JSONL.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(evt.to_row(), ensure_ascii=False) + "\n")

        # Update derived aggregates (best-effort).
        self._update_failure_patterns(evt)

    def _update_failure_patterns(self, evt: AgentEvent) -> None:
        if evt.outcome != "FAILURE":
            return
        if not evt.signature_key or not evt.intent_class:
            return
        error_type = evt.error_type or "unknown"
        root_cause = evt.root_cause or "unknown"
        now = evt.ts_end or _now_iso()

        row = self._con.execute(
            """
            SELECT id, count FROM failure_patterns
            WHERE signature_key=? AND intent_class=? AND error_type=? AND root_cause=?
            """,
            (evt.signature_key, evt.intent_class, error_type, root_cause),
        ).fetchone()
        if row:
            self._con.execute(
                "UPDATE failure_patterns SET count=count+1, last_ts_end=? WHERE id=?",
                (now, row["id"]),
            )
        else:
            self._con.execute(
                """
                INSERT INTO failure_patterns(signature_key, intent_class, error_type, root_cause, count, last_ts_end)
                VALUES(?, ?, ?, ?, 1, ?)
                """,
                (evt.signature_key, evt.intent_class, error_type, root_cause, now),
            )
        self._con.commit()

    def get_risks(self, *, signature_key: str, intent_class: str, top_k: int = 5) -> list[dict[str, Any]]:
        rows = self._con.execute(
            """
            SELECT error_type, root_cause, count, last_ts_end
            FROM failure_patterns
            WHERE signature_key=? AND intent_class=?
            ORDER BY count DESC, last_ts_end DESC
            LIMIT ?
            """,
            (signature_key, intent_class, top_k),
        ).fetchall()
        return [dict(r) for r in rows]

    def rank_intents(
        self,
        *,
        agent: str,
        intent_class: str,
        intents: Iterable[str],
        signature_key: str,
        min_trials: int = 3,
    ) -> list[str]:
        intent_list = list(intents)
        if not intent_list:
            return []

        scores: dict[str, tuple[float, int]] = {}
        for intent in intent_list:
            rows = self._con.execute(
                """
                SELECT outcome, COUNT(*) AS c
                FROM events
                WHERE agent=? AND intent_class=? AND intent=? AND signature_key=?
                GROUP BY outcome
                """,
                (agent, intent_class, intent, signature_key),
            ).fetchall()
            total = sum(int(r["c"]) for r in rows)
            success = sum(int(r["c"]) for r in rows if str(r["outcome"]) == "SUCCESS")
            if total < min_trials:
                # Not enough signal: neutral score.
                scores[intent] = (0.5, total)
            else:
                scores[intent] = (success / max(1, total), total)

        # Higher success-rate first; tie-break by more trials.
        intent_list.sort(key=lambda i: (scores.get(i, (0.5, 0))[0], scores.get(i, (0.5, 0))[1]), reverse=True)
        return intent_list


def get_client() -> LearningClient:
    return LearningClient()

