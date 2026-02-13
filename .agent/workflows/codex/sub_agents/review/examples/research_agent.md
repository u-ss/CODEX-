# Research Agent 評価ガイド

Round 7-10 の実践知識に基づく Research Agent 固有の評価パターン。

## ログ構造（3層）

| レイヤー | ファイル | 内容 |
|:---------|:---------|:-----|
| L1 | `*_summary.json` | integrity_score, scores, violations, run_warnings |
| L2 | `*_metadata.json` | quality_report, sources, claims, claim_causal_log |
| L3 | `*.jsonl` | Phase 1-5 実行ログ（events, outputs, timings） |

## 整合性チェック（Research固有）

```
□ summary.integrity_score == metadata.quality_report.verification.integrity_score
□ summary.scores == metadata.quality_report.scores
□ summary.validation_violations == metadata.quality_report.validation_violations
□ summary.run_warnings == metadata.quality_report.run_warnings
□ JSONL Phase4 outputs.status_distribution == metadata.claims の集計値
□ JSONL Phase5 outputs.quality_report == metadata.quality_report
□ レポートMDヘッダの検索件数/ソース数 == JSONL search_kpi の実数値
```

## content_quality_score 6軸計算

```python
content_quality_score = round(
    link_coverage * 16.7 +          # claim-evidence紐付け率
    (1 - empty_quote_ratio) * 16.7 + # 引用充実度
    ce_resolution_rate * 16.7 +      # 反証解決率
    claim_tracking_rate * 16.7 +     # claim追跡率
    multi_source_ratio * 16.6 +      # 独立根拠充足率（≥2ソース）
    verified_ratio * 16.6            # VERIFIED比率
, 1)
```

## execution_trust_score 複合指標

```python
# 複合指標（v11予定）
execution_trust_score = round(
    latency_score * 0.4 +      # レイテンシ正常率
    http_200_ratio * 0.2 +     # HTTP200率
    cache_hit_ratio * 0.2 +    # キャッシュヒット率
    rtt_distribution * 0.2     # RTT分布正常率
, 1)
```

## よくある減点パターン

| # | 原因 | 影響 | 対策 |
|---|:-----|:----:|:-----|
| 1 | レポートMDヘッダ「検索:15件」とJSONL「searches=21」の不一致 | -15 | メトリクスから動的埋め込み |
| 2 | VERIFIEDが単一ソースのみで信頼性不足 | -5 | ≥2ソースなければCONDITIONEDへauto-demote |
| 3 | CONDITIONED比率がcontent_scoreに未反映 | -6 | 6軸目にverified_ratio追加 |
| 4 | decision_reasonの「3つのソースで裏付け」にsource_idsが2件のみ | -3 | 正規表現で数値検出→source_ids数と比較 |
| 5 | execution_trust_scoreがlatency単独 | -5 | 複合指標化 |
| 6 | warn_phasesとrun_warningsが混在 | -3 | 独立フィールド分離 |
| 7 | peer_reviewed_ratio = 0（学術以外では許容） | -2 | ドメイン別閾値設定 |

## ラウンド履歴

| Round | 品質 | ログ | 主要変更 |
|:------|:----:|:----:|:---------|
| v7 | 74 | 78 | 3レイヤー統一パッチ |
| v8 | 85 | 88 | content/trust分離、live_fetch追加 |
| v9 | 91 | 93 | auto-demote、5軸化、動的latency閾値 |
| v10 | 85 | 78 | 6軸化→後退（MDヘッダ不一致） |
