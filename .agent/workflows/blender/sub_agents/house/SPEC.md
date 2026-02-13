# House Real SKILL v1.2.0## 目的

## 役割境界

- この SPEC.md は技術仕様（入出力・判定基準・実装詳細）の正本。
- 実行手順は同フォルダの GUIDE.md を参照。


自然文・フォーム入力から実在戸建て住宅を高精度にモデリングする。
8ステージパイプライン + 自動QA + CODEXAPPレビューで品質95点以上を目指す。

## アーキテクチャ

```
エージェント\実在戸建てモデリングエージェント\
├── schemas/
│   └── house_spec.schema.json
├── qa/
│   └── thresholds.yaml
├── scripts/
│   ├── pipeline.py                  # オーケストレーター (v1.1.0)
│   ├── lib/
│   │   ├── schema.py                # Specロード/バリデーション/壁生成
│   │   ├── blender_ops.py           # BMesh/PBR/EEVEE対応ラッパー
│   │   └── metrics.py               # KPI計測/スコアリング
│   └── stages/
│       ├── 01_ingest.py
│       ├── 02_normalize_spec.py
│       ├── 03_build_shell.py
│       ├── 04_build_openings.py
│       ├── 05_build_interiors.py
│       ├── 06_assign_materials.py
│       ├── 07_lighting_render.py    # レンダプロファイル対応
│       ├── 08_qa_and_fix.py
│       └── opening_utils.py
├── tests/
│   └── test_walls_and_openings.py   # 18テスト
└── _outputs/
    └── iter_XXX/
```

## レンダプロファイル (v1.2.0 新機能)

### CLIオプション

| オプション | 値 | デフォルト | 説明 |
|:--|:--|:--|:--|
| `--render-profile` | `preview` / `final` | `preview` | プレビュー(爆速) or 最終(高品質) |
| `--render-cameras` | `ext_only` / `all` / `list` | `ext_only` | カメラ出力範囲 |
| `--render-camera-names` | カンマ区切り | — | `list`時の個別カメラ指定 |
| `--render-method` | `render` / `opengl` | `render` | レンダ方式 |

### プロファイル設定

| プロファイル | エンジン | 解像度 | サンプル | 用途 |
|:--|:--|:--|:--|:--|
| **preview** | EEVEE | 960×540 | 16 | デバッグ・形状確認 (約5秒) |
| **final** | Cycles | 1920×1080 | 384 | 納品・プレゼン (約18分) |

### カメラ制御

- `ext_only`: 外観4視点 (east/west/south/north) のみ — 最速
- `all`: specの全カメラ (外観+内装)
- `list`: 指定カメラ + 外観4視点（常に最低保証）

## ステージ詳細

| # | ステージ | 入力 | 出力 |
|:--|:--|:--|:--|
| 1 | Ingest | プロンプト/図面 | house_spec.json |
| 2 | Normalize | raw spec | 正規化spec + 壁/カメラ/家具自動生成 |
| 3 | Build Shell | spec | 壁・床・天井・屋根・基礎 |
| 4 | Build Openings | 建具表 | 窓・ドア（ブーリアン差分） |
| 5 | Build Interiors | 間取り+動線 | 家具・水回り |
| 6 | Assign Materials | 仕上表 | PBR16種割当 |
| 7 | Lighting & Render | 視点定義+プロファイル | レンダ画像 |
| 8 | QA & Fix | シーン+レンダ | 検証レポート+自動修正 |

## KPI基準

| KPI | 閾値 |
|:----|:-----|
| 寸法誤差中央値 | ≤ 20mm |
| 壁/家具干渉 | 0件 |
| 通路幅 | ≥ 750mm |
| PBR適用率 | ≥ 90% |
| 白飛び率 | ≤ 2% |

## CODEXAPP評価ループ

1. preview + ext_only で戸建て生成 (約5秒)
2. 画像パスをCODEXAPPに送信して100点満点で採点
3. 改善提案をコードに反映
4. 再生成 → 再レビュー
5. 95点以上で完了

### 評価基準（100点満点）

| 項目 | 配点 |
|:-----|-----:|
| 構造の正確性 | 20点 |
| 内装の詳細度 | 25点 |
| マテリアル品質 | 20点 |
| 外構・環境 | 15点 |
| 照明・レンダ品質 | 10点 |
| 間取りの合理性 | 10点 |

## 完了条件

- `qa_report.json.passed == true` (スコア ≥ 80)
- レンダ画像が存在
- `house.blend` が保存済み
- `pipeline_report.json` が生成済み
