---
name: Research Agent v4.3.3
description: Research Agent v4.3.3
command_template: python .agent/workflows/research/scripts/research.py --goal "{title}" --focus "{objective}" --task-id "{task_id}"
---

# Research Agent SKILL v4.3.3
**Claim中心リサーチ**: Phase 1（広域）→ Phase 2（正規化）→ Phase 3（深層）→ Phase 3.5（検証）→ Phase 4（統合）


## 役割境界

- この SKILL.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの WORKFLOW.md を参照。


> [!IMPORTANT]
> **v4.3.3**: lib/の型定義（`ResearchRunContext`, `Phase`, `PhaseResult`等）に従ってデータを整理。
> Antigravityが各Phaseを順番に実行し、`_outputs/research/` に保存。


## 📖 用語定義 ★v4.3.1追加

| 用語 | 定義 |
|:-----|:-----|
| **Claim** | 検証可能な断定（例:「AはBより速い」） |
| **Evidence** | Claimを支持/反証する出典断片（引用可能な箇所） |
| **Constraint** | 条件付き成立要件（期間/地域/版/前提） |
| **Artifact** | Phaseの入力/出力として保存される必須項目セット |
| **Source** | URL/文献/内部資料などの出典識別子 |

---

## 📦 Artifacts共通ルール ★v4.3.1追加

すべてのArtifactは以下のメタ情報を持つ：
- `id`, `created_at`, `scope`, `assumptions`, `open_questions`

### Claim必須フィールド
- `claim_id`, `statement`, `scope`, `decision_relevance`

### Evidence必須フィールド
- `source_id`, `quote`, `supports|refutes`, `quality_score`

---

## 📋 Phase 1: Wide Research Protocol

```
┌─────────────────────────────────────────────────────────────┐
│  1. DECOMPOSE（7視点分解）                                  │
│     ├─ 技術・実装                                           │
│     ├─ ビジネス・市場                                       │
│     ├─ ユースケース                                         │
│     ├─ 課題・リスク                                         │
│     ├─ 将来展望                                             │
│     ├─ 競合・代替                                           │
│     └─ 学術・論文（arXiv, Semantic Scholar）               │
│     ↓                                                       │
│  2. MULTI-SOURCE SEARCH（各視点2-3回）                      │
│     → 1ラウンド = 15-21回検索                               │
│     ↓                                                       │
│  3. EXTRACT RawClaim（主張抽出）                            │
│     → 各ソースから主張を抽出                                │
│     → RawClaim(text, source, published_at)                  │
│     ↓                                                       │
│  4. OUTPUT: RawClaim[] + search_log[]                       │
└─────────────────────────────────────────────────────────────┘
```

## 📋 Phase 2: Reasoning + Claim Normalization

```
┌─────────────────────────────────────────────────────────────┐
│  入力: Phase 1のRawClaim[]                                  │
│     ↓                                                       │
│  1. NORMALIZE Claims（Claim正規化）                         │
│     → RawClaim → NormalizedClaim                            │
│     → slots抽出（subject/predicate/object/time）            │
│     → fingerprint生成 → claim_id割当                        │
│     → 重複検出＆マージ                                       │
│     ↓                                                       │
│  2. GAP ANALYSIS（ギャップ分析）                            │
│     → 情報が薄い領域を Gap[] として記録                     │
│     → Phase 3のサブ質問を生成                               │
│     ↓                                                       │
│  3. OUTPUT: NormalizedClaim[] + Gap[] + sub_questions[]     │
└─────────────────────────────────────────────────────────────┘
```

## 📋 Phase 3: Deep Research Protocol

```
┌─────────────────────────────────────────────────────────────┐
│  入力: NormalizedClaim[], Gap[]                             │
│     ↓                                                       │
│  1. EXHAUSTIVE SEARCH（徹底検索）                           │
│     → 各Gapで5回以上検索                                    │
│     → 30-50回検索                                           │
│     ↓                                                       │
│  2. COLLECT Evidence（証拠収集）                            │
│     → Evidence(url, tier, stance, bias_flags)               │
│     → 信頼性スコア計算（freshness × authority × bias）      │
│     ↓                                                       │
│  3. TERMINATION CHECK（終了条件判定）                       │
│     ├─ coverage ≥ 0.75 AND converged → 終了                │
│     ├─ mu < 0.15 が3回連続 → 終了                          │
│     └─ budget超過 AND coverage ≥ 0.55 → 終了               │
│     ↓                                                       │
│  4. OUTPUT: Evidence[] + coverage_map                       │
└─────────────────────────────────────────────────────────────┘
```

## 📋 Phase 3.5: Verification Protocol ★v4.3.1強化

> [!IMPORTANT]
> **このフェーズはAntigravityが直接実行する**
> **主要Claimは反証探索必須**

```
┌─────────────────────────────────────────────────────────────┐
│  入力: NormalizedClaim[], Evidence[]                        │
│     ↓                                                       │
│  1. 主要Claim選定 ★v4.3.1                                  │
│     ├─ decision_relevance=high                              │
│     ├─ 強い断定語（必ず/唯一/最適）                         │
│     ├─ 数値比較/ランキング                                  │
│     └─ 法規/安全/金融関連                                   │
│     ↓                                                       │
│  2. 反証探索（主要Claimごと）★v4.3.1                       │
│     → Claim反転（否定形に変換）                             │
│     → 例外探索（制限/失敗条件）                             │
│     → 批判語彙（critique, limitation）                      │
│     → counterevidence_log記録                               │
│     ↓                                                       │
│  3. STATUS DETERMINATION（閾値判定）★v4.3.1強化            │
│     上記閾値テーブルに基づき判定                            │
│     ↓                                                       │
│  4. OUTPUT: VerifiedClaim[] + counterevidence_log[]         │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ ステータス判定軸 ★v4.3.1追加

| 軸 | 説明 |
|:---|:-----|
| **E（Evidence強度）** | 独立性、一次/二次、方法の明確性 |
| **C（Coverage）** | Claimのscope充足率 |
| **A（Agreement）** | 高品質ソース間の整合度 |
| **R（Recency）** | 時点依存領域での最新性 |
| **T（Traceability）** | 引用可能性（locatorの明確さ） |

### 緩和ルール
- 適用条件: `risk_level=low` かつ 概説用途 かつ `decision_relevance=中以下`
- 緩和は最大1段階（例: VERIFIED相当→CONDITIONED）
- **CONTESTED/REFUTEDは緩和不可**（安全側固定）
- `relaxation_reason` を必須記録

---

## 🔄 終了条件

| 指標 | 閾値 | 説明 |
|------|------|------|
| coverage | ≥ 0.75 | Gap閉鎖率 |
| convergence | eps=0.02, τ=3 | Top-K Claimのconfidence安定 |
| marginal_utility | μ<0.15 × N=3 | 効用低下が3回連続 |
| min_coverage | ≥ 0.55 | 予算超過時の最低ライン |
| hard_cap | 12ラウンド | 強制終了 |

## 📊 出力フォーマット

### VerifiedClaim（v4.3.1）
```json
{
  "claim_id": "clm_8f2c...",
  "statement": "...",
  "status": "VERIFIED | CONDITIONED | CONTESTED | UNSUPPORTED | REFUTED",
  "rationale": "判定理由",
  "conditions": [],
  "supporting_evidence_ids": [],
  "refuting_evidence_ids": [],
  "relaxation_reason": null
}
```

### counterevidence_log（v4.3.1追加）
```json
{
  "claim_id": "clm_8f2c...",
  "search_queries": ["...", "..."],
  "search_scope": "期間/対象",
  "found_counterevidence": true,
  "impact_on_status": "VERIFIED→CONDITIONED"
}
```

## 🧩 子エージェント一覧

| 子エージェント | パス | 役割 |
|:---------------|:-----|:-----|
| Stealth Local | `sub_agents/stealth_local/SPEC.md` | Ollama ローカル推論で完結するステルスリサーチ |

外部API使用不可のタスクや、ローカル完結が必要な場合は `sub_agents/stealth_local/SPEC.md` を読んで実行する。

## 💡 Rules

- **search_web** で検索実行
- **read_url_content** で詳細取得
- **Phase 3.5はAntigravityが判断**: LLMとして検証を実行
- **主要Claimは反証探索必須** ★v4.3.1
- **search_log.md必須**: Phase 4完了時にレポートと同時出力 ★New
- **Language**: 日本語

---

## 📦 lib/ モジュール構造 ★v4.3.3追加

```
lib/
├── models.py          # 型定義（Stance, ClaimStatus, ArtifactBase）
├── claims.py          # Claim正規化（RawClaim, NormalizedClaim）
├── scoring.py         # Evidenceスコアリング
├── verification.py    # ステータス判定（determine_status）
├── termination.py     # 終了条件（should_stop）
├── failure_detector.py # 失敗検知
├── artifacts.py       # 保存用Record型（RawClaimRecord等）
├── context.py         # ResearchRunContext（Phase間共有状態）
├── phase_runner.py    # Phase遷移制御（Phase, PhaseSignal）
├── orchestrator.py    # 実行エンジン（参照用）
└── handlers/          # 各Phaseのロジック（参照用）
```

## 🔄 Phase実行フロー ★v4.3.3

```
/research "クエリ"
    ↓
Antigravityが順番に実行:

1. Phase 1 (WIDE):
   - search_web で7視点検索
   - read_url_content でページ読み取り
   - RawClaim抽出 → context.raw_claims に格納

2. Phase 2 (NORMALIZE):
   - raw_claims → normalized_claims 変換
   - Gap分析 → gaps, sub_questions 生成

3. Phase 3 (DEEP):
   - sub_questions に基づく追加検索
   - Evidence収集 → context.evidence
   - 終了条件判定（coverage ≥ 0.75 OR 3ラウンド）

4. Phase 3.5 (VERIFY):
   - 主要Claim選定 → 反証探索
   - ステータス判定 → verified_claims
   - 差し戻し必要なら Phase 3 へ戻る

5. Phase 4 (INTEGRATE):
   - verified_claims からレポート生成
   - final_report 出力
   - ★ search_log.md 出力（検索クエリ・ソース・参照件数まとめ）

    ↓
_outputs/research/YYYYMMDD_HHMM/ に保存
  ├── *_report.md       # 最終レポート
  └── search_log.md     # 検索ログ ★New
```

### search_log.md フォーマット ★New

```markdown
# 検索ログ: {リサーチタイトル}
> 実行: YYYY-MM-DD HH:MM〜HH:MM | 合計N回検索 | 実行方法

## 検索サマリー
| # | Phase | ジャンル | クエリ | 主要参照ソース | 参照件数 |
|:-:|:------|:---------|:-------|:---------------|:--------:|
| 1 | WIDE  | ...      | ...    | ...            | ...      |

## 統計
- 総検索回数: N回
- ユニーク参照ソース: N件
- エラー: N件
```

### ResearchRunContext 構造

| フィールド | 型 | Phase |
|:-----------|:---|:------|
| `query` | str | 入力 |
| `raw_claims` | List[Dict] | Phase 1出力 |
| `normalized_claims` | List[Dict] | Phase 2出力 |
| `gaps` | List[Dict] | Phase 2出力 |
| `sub_questions` | List[str] | Phase 2出力 |
| `evidence` | List[Dict] | Phase 3出力 |
| `verified_claims` | List[Dict] | Phase 3.5出力 |
| `final_report` | str | Phase 4出力 |

## ログ記録（WorkflowLogger統合）

> [!IMPORTANT]
> 実行時は必ずWorkflowLoggerで各フェーズをログ記録すること。
> 詳細: [WORKFLOW_LOGGING.md](../shared/WORKFLOW_LOGGING.md)

`python
import sys; sys.path.insert(0, '.agent/workflows/shared')
from workflow_logging_hook import logged_main, phase_scope
`

ログ保存先: `_logs/autonomy/{agent}/{YYYYMMDD}/`
