---
name: KI Learning Agent v1.0.0
description: KI Learning Agent v1.0.0
---

> [!CAUTION]
> **必須**: 実行前に同フォルダの `SKILL.md` を読むこと（ルール・技術詳細）

# KI Learning Agent v1.0.0 (`/ki-learning`)

**学習インフラエージェント**: 各エージェントの実行結果を記録し、失敗回避に活用。

> [!CAUTION]
> **必須**: このファイルと同フォルダの`SKILL.md`を読んでから実行

## 📋 使い方

### 1. 他エージェントからの参照（自動）

各エージェント（/desktop, /code等）がKI Learning Agentを**自動参照**する想定。
ワークフロー内で明示的に呼び出す必要はない。

### 2. 管理コマンド

```bash
# 失敗パターン確認
/ki-learning --show-risks --agent /desktop

# 学習統計
/ki-learning --stats

# 古いデータクリーンアップ（half_life超過）
/ki-learning --cleanup --older-than 30d
```

## 📊 統合フロー

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 実行前（各エージェント）                                  │
│    ├─ get_risks(signature_key, intent_class)                │
│    │   → 失敗リスク・避けるべきlocator                      │
│    └─ get_best_locators(signature_key, intent_class)        │
│        → 成功率上位の候補                                    │
│                                                              │
│ 2. 実行中（各エージェント）                                  │
│    └─ action_trace / evidences を収集                        │
│                                                              │
│ 3. 実行後（各エージェント）                                  │
│    └─ report_outcome(AgentEvent)                             │
│        → events追記 + locator_stats/failure_patterns更新     │
└─────────────────────────────────────────────────────────────┘
```

## 📦 保存先

```
knowledge/
└── learning/
    ├── learning.db       # SQLite（Events, locator_stats, failure_patterns）
    └── events.jsonl      # 監査用ログ（追記）
```

## 🛡️ ルール

| ルール | 内容 |
|:-------|:-----|
| **Outcome 4値** | SUCCESS/FAILURE/PARTIAL/UNKNOWN |
| **confidence必須** | 0..1で確信度を記録 |
| **signature_key安定** | ブレる情報は入れない |
| **CB (Circuit Breaker)** | 連続失敗でOPEN→候補から除外 |

## ⚠️ 注意事項

- KI Learning Agentは**インフラ**であり、直接呼び出すエージェントではない
- 各エージェントのSKILL.mdに統合フックを追加する必要あり（別途作業）
- SQLiteは`knowledge/learning/learning.db`に配置

## 💡 Rules

- **統合は2フック**（実行前参照・実行後記録）
- **鮮度管理**: half_life経過で参照スコア低下
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
- Unknown failure ratio decreases over 1+ days of runs.
- Top recurring failure patterns include at least one non-unknown dimension.
- A regression test exists for the new behavior.

### Success Metrics
- Unknown failure ratio (= low-info failures / failures).
- Top-5 failure patterns coverage (non-unknown share).

### Rollback Plan
- Revert classifier changes; DB schema unchanged (only values change).
