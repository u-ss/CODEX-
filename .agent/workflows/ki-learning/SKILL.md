---
name: KI Learning Agent v1.0.0
---

# KI Learning Agent SKILL v1.0.0**目的**: エージェント（/desktop, /code, /research等）の実行結果を自動記録し、失敗回避に活用。

## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。


> [!IMPORTANT]
> **他エージェントとの関係**: 各エージェントが「記録」「参照」する共有インフラ。

---

## 🏗️ アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│  各エージェント（/desktop, /code, /research等）            │
│     ├─ 実行前: get_risks(), get_best_locators() で参照     │
│     └─ 実行後: report_outcome() で記録                     │
├─────────────────────────────────────────────────────────────┤
│  KI Learning Agent                                          │
│     ├─ events テーブル（生イベント追記）                   │
│     ├─ locator_stats テーブル（候補統計）                  │
│     └─ failure_patterns テーブル（失敗パターン）           │
├─────────────────────────────────────────────────────────────┤
│  保存先: knowledge/learning/learning.db (SQLite)           │
└─────────────────────────────────────────────────────────────┘
```

---

## 📦 Outcome型

```python
Outcome = Literal["SUCCESS", "FAILURE", "PARTIAL", "UNKNOWN"]
```

| 値 | 意味 |
|:---|:-----|
| SUCCESS | 目標達成（高確信） |
| FAILURE | 明確な失敗（エラー、タイムアウト等） |
| PARTIAL | 部分的成功（成功したっぽいが確証薄い） |
| UNKNOWN | 判定不能（記録はするが抑止に使わない） |

---

## 📊 AgentEvent スキーマ

```python
@dataclass
class AgentEvent:
    # 識別子
    event_id: str           # UUID
    task_id: str            # タスク識別子
    agent: str              # "/desktop" "/code" "/research"
    intent: str             # 何をしたいか（短文）
    intent_class: str       # 安定ラベル（"click_send" "extract_answer"）
    signature_key: str      # 再発回避用キー

    # 時刻
    ts_start: str           # ISO8601
    ts_end: str

    # 結果
    outcome: Outcome        # SUCCESS/FAILURE/PARTIAL/UNKNOWN
    confidence: float       # 0..1
    error_type: str         # Timeout/NotFound/PolicyBlock...
    root_cause: str         # SelectorStale/WindowFocus/Network...

    # コンテキスト
    env: EnvSnapshot        # OS, locale, app_version, dpi
    target: TargetSnapshot  # app_name, screen_key, element_role
    action_trace: List[ActionAttempt]  # 最後K手
    evidences: List[Evidence]          # 証拠

    # 対策
    fix: str                # 効いた対策
    tags: List[str]         # タグ
    severity: str           # LOW/MED/HIGH
    artifacts: Dict[str,str] # スクショ/ログ参照
```

---

## 🗄️ SQLite テーブル

### events（生イベント追記）

```sql
CREATE TABLE events (
  event_id        TEXT PRIMARY KEY,
  ts_start        TEXT NOT NULL,
  ts_end          TEXT,
  agent           TEXT NOT NULL,
  task_id         TEXT NOT NULL,
  intent          TEXT NOT NULL,
  intent_class    TEXT NOT NULL,
  signature_key   TEXT NOT NULL,
  outcome         TEXT NOT NULL,      -- SUCCESS/FAILURE/PARTIAL/UNKNOWN
  confidence      REAL NOT NULL,
  error_type      TEXT,
  root_cause      TEXT,
  target_app      TEXT,
  target_screen   TEXT,
  env_os          TEXT,
  severity        TEXT NOT NULL,
  fix             TEXT,
  tags_json       TEXT NOT NULL,
  action_trace_json TEXT NOT NULL,
  evidences_json    TEXT NOT NULL,
  artifacts_json    TEXT NOT NULL
);

CREATE INDEX idx_events_sig_ts ON events(signature_key, ts_end);
```

### locator_stats（候補統計）

```sql
CREATE TABLE locator_stats (
  signature_key   TEXT NOT NULL,
  intent_class    TEXT NOT NULL,
  layer           TEXT NOT NULL,       -- CDP/UIA/Pixel
  locator_kind    TEXT NOT NULL,       -- css/uia/xy/image
  locator         TEXT NOT NULL,
  attempts        INTEGER DEFAULT 0,
  successes       INTEGER DEFAULT 0,
  failures        INTEGER DEFAULT 0,
  avg_latency_ms  REAL DEFAULT 0,
  last_success_ts TEXT,
  last_failure_ts TEXT,
  cb_open_until   TEXT,                -- Circuit Breaker
  PRIMARY KEY (signature_key, intent_class, layer, locator_kind, locator)
);
```

### failure_patterns（失敗パターン）

```sql
CREATE TABLE failure_patterns (
  signature_key    TEXT NOT NULL,
  intent_class     TEXT NOT NULL,
  root_cause       TEXT NOT NULL,
  error_type       TEXT,
  count_30d        INTEGER DEFAULT 0,
  last_seen_ts     TEXT NOT NULL,
  suggested_fix    TEXT,
  severity         TEXT DEFAULT 'LOW',
  PRIMARY KEY (signature_key, intent_class, root_cause, error_type)
);
```

---

## 🔧 API

### get_risks(signature_key, intent_class)

失敗リスクと推奨対策を返す。

```json
{
  "signature_key": "sig:...",
  "intent_class": "click_send",
  "overall_risk": { "level": "MED", "score": 0.63 },
  "avoid": {
    "locators": [{"locator": "...", "why": "CB_OPEN"}],
    "actions": [{"action": "Click", "why": "misclick risk"}]
  },
  "recommended_guards": [
    {"guard": "ensure_window_focused", "params": {"retries": 2}}
  ],
  "recommended_fixes": [
    {"fix": "focus_window_then_retry"}
  ]
}
```

### get_best_locators(signature_key, intent_class, top_k=5)

成功率×鮮度×速度で上位候補を返す。

```json
{
  "candidates": [
    {
      "rank": 1,
      "layer": "UIA",
      "locator": "automation_id='sendButton'",
      "score": 0.91,
      "estimated_success_rate": 0.87,
      "cb_state": "CLOSED"
    },
    ...
  ]
}
```

### report_outcome(event: AgentEvent)

実行結果を記録。events追記 + locator_stats/failure_patterns更新。

---

## 🔗 他エージェント統合

### フック1: 実行前（Plan/Select直前）

```python
# 他エージェント側risks = learning_client.get_risks(signature_key, intent_class)
best = learning_client.get_best_locators(signature_key, intent_class)

# OPENな候補を除外candidates = [c for c in best["candidates"] if c["cb_state"] != "OPEN"]

# 推奨ガードを実行for g in risks.get("recommended_guards", []):
    run_guard(g["guard"], **g.get("params", {}))
```

### フック2: 実行後（Outcome確定時）

```python
evt = AgentEvent(
    task_id=task_id,
    agent="/desktop",
    intent="Click Send button",
    intent_class="click_send",
    signature_key=signature_key,
    ...
)
evt.finish(outcome="SUCCESS", confidence=0.92)
learning_client.report_outcome(evt)
```

---

## 📈 鮮度管理

```python
freshness = exp(-(now - ts_end) / half_life)
```

| 対象 | half_life |
|:-----|:----------|
| /desktop UI要素 | 3〜14日 |
| /research 事実 | 30〜180日 |
| /code ビルド | 14〜60日 |

---

## 💡 Rules

- **Outcome は4値**（SUCCESS/FAILURE/PARTIAL/UNKNOWN）
- **signature_key は安定させる**（ブレる情報を入れすぎない）
- **他エージェントは2フックで統合**（実行前参照・実行後記録）
- **Language**: 日本語


## Agent Architect Integration Note
- Gap source: gap_ki_failure_enrichment
- Focus: quality
- Suggested integration: Integrate capability into existing `/ki-learning` workflow

### Plan
- Improve failure classification so top failures are not recorded as error_type/root_cause='unknown' when signals exist.
- Persist a small set of stable buckets (timeout/ui/no_ack/network/config/unknown) for downstream ranking.
- Add a test that feeds sample events and asserts the bucket mapping is stable.

### Acceptance Criteria
- Unknown failure ratio decreases over 1–2 days of runs.
- Top recurring failure patterns include at least one non-unknown dimension.
- A regression test exists for the new behavior.

### Success Metrics
- Unknown failure ratio (= low-info failures / failures).
- Top-5 failure patterns coverage (non-unknown share).

### Rollback Plan
- Revert classifier changes; DB schema unchanged (only values change).

##  ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
