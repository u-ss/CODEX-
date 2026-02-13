"""
好み傾向分析モジュール

学習データから「なぜ好きか」を技術的に分析し、
傾向レポートを生成する。
ユーザーが記入した理由テキストと画像特徴量を統合して
好みを自然言語で言語化する。
"""

import json
import re
from collections import Counter
import numpy as np
from pathlib import Path
from typing import Union, Optional

from feature_extractor import extract_features, features_to_vector, get_feature_keys
from preference_learner import PreferenceLearner, LABEL_MAP, IMAGE_EXTENSIONS
from reason_store import ReasonStore

# 理由テキストから抽出するキーワードパターン
KEYWORD_PATTERNS = {
    "色彩": {
        "暖色系": ["暖色", "赤", "オレンジ", "黄色", "暖かい色", "ウォーム"],
        "寒色系": ["寒色", "青", "緑", "紫", "クール", "冷たい色"],
        "鮮やか": ["鮮やか", "ビビッド", "カラフル", "派手", "明るい色"],
        "落ち着いた色": ["落ち着", "モノトーン", "白黒", "地味", "淡い", "パステル"],
        "色のグラデーション": ["グラデーション", "色の変化", "色合い"],
    },
    "構図": {
        "ダイナミック": ["ダイナミック", "迫力", "スケール", "広がり", "パノラマ"],
        "シンメトリー": ["シンメトリー", "対称", "バランス", "整った"],
        "シンプル": ["シンプル", "ミニマル", "すっきり", "余白"],
        "複雑": ["複雑", "情報量", "ごちゃごちゃ", "詰まって"],
    },
    "質感": {
        "シャープ": ["シャープ", "くっきり", "鮮明", "解像度", "はっきり"],
        "柔らかい": ["柔らか", "ソフト", "ぼかし", "ふんわり", "やわらか"],
        "ザラザラ": ["ザラザラ", "テクスチャ", "質感", "粗い"],
    },
    "雰囲気": {
        "明るい": ["明るい", "光", "眩しい", "輝", "キラキラ"],
        "暗い": ["暗い", "ダーク", "影", "闇", "重い"],
        "幻想的": ["幻想", "ファンタジー", "夢", "不思議", "神秘"],
        "自然": ["自然", "風景", "空", "海", "山", "花", "森"],
        "都会的": ["都会", "ビル", "夜景", "街", "モダン"],
        "レトロ": ["レトロ", "ヴィンテージ", "古い", "ノスタルジ"],
        "綺麗": ["綺麗", "美しい", "きれい", "素敵", "かわいい", "おしゃれ"],
    },
}


def refine_reason_text(raw_text: str, category: str = "好き") -> dict:
    """ユーザーの雑なテキスト入力をAIが校正・言語化する。

    Args:
        raw_text: ユーザーの元テキスト
        category: カテゴリ（好き/そうでもない/嫌い）

    Returns:
        dict:
            - refined: 校正後テキスト
            - keywords: 検出されたキーワードリスト
            - original: 元テキスト
    """
    raw_text = raw_text.strip()
    if not raw_text:
        return {"refined": "", "keywords": [], "original": ""}

    # キーワード検出
    found = []
    for group_name, patterns in KEYWORD_PATTERNS.items():
        for keyword_label, words in patterns.items():
            matched_words = [w for w in words if w in raw_text]
            if matched_words:
                found.append({
                    "group": group_name,
                    "label": keyword_label,
                    "matched": matched_words,
                })

    if not found:
        # キーワードが見つからない場合はそのまま返す
        return {"refined": raw_text, "keywords": [], "original": raw_text}

    # グループ別にまとめる
    grouped = {}
    for f in found:
        g = f["group"]
        if g not in grouped:
            grouped[g] = []
        grouped[g].append(f["label"])

    # 自然言語テキスト生成
    sentiment = "好き" if category == "好き" else "苦手" if category == "嫌い" else "どちらでもない"
    group_labels_ja = {
        "色彩": "色彩",
        "構図": "構図",
        "質感": "質感・テクスチャ",
        "雰囲気": "雰囲気",
    }

    parts = []
    for group, labels in grouped.items():
        group_ja = group_labels_ja.get(group, group)
        joined = "・".join(labels)
        parts.append(f"{group_ja}は【{joined}】が{sentiment}")

    refined = "。".join(parts) + "。"

    keywords = [f["label"] for f in found]

    return {
        "refined": refined,
        "keywords": keywords,
        "original": raw_text,
    }


def describe_image_features(image_path: str, category: str = "好き") -> dict:
    """画像の技術的特徴量から自然言語の理由テキストを自動生成する。

    Args:
        image_path: 画像ファイルのパス
        category: カテゴリ（好き/そうでもない/嫌い）

    Returns:
        dict:
            - description: 生成された理由テキスト
            - traits: 検出された特徴ラベルリスト
            - details: 技術的詳細
    """
    features = extract_features(image_path)

    traits = []
    details = {}

    # --- 色彩分析 ---
    warm = features.get("warm_ratio", 0)
    cool = features.get("cool_ratio", 0)
    saturation = features.get("mean_saturation", 0)
    colorfulness = features.get("colorfulness", 0)

    color_descriptions = []
    if warm > 0.5:
        traits.append("暖色系")
        color_descriptions.append("暖色（赤・オレンジ・黄）が主体")
    elif cool > 0.5:
        traits.append("寒色系")
        color_descriptions.append("寒色（青・緑）が主体")
    elif warm > 0.3 and cool > 0.3:
        traits.append("バランスの取れた色彩")
        color_descriptions.append("暖色と寒色がバランスよく混在")
    else:
        traits.append("中性的な色合い")
        color_descriptions.append("中間色が中心")

    if saturation > 0.5:
        traits.append("鮮やかな色")
        color_descriptions.append("彩度が高く鮮やか")
    elif saturation < 0.15:
        traits.append("モノトーン調")
        color_descriptions.append("彩度が低くモノトーンに近い")

    if colorfulness > 0.15:
        traits.append("カラフル")
        color_descriptions.append("色のバリエーションが豊か")

    details["色彩"] = color_descriptions

    # --- 明るさ分析 ---
    brightness = features.get("mean_brightness", 0)
    contrast = features.get("contrast", 0)

    brightness_descriptions = []
    if brightness > 0.65:
        traits.append("明るい")
        brightness_descriptions.append("全体的に明るい印象")
    elif brightness < 0.35:
        traits.append("暗めのトーン")
        brightness_descriptions.append("暗めのトーンで落ち着いた雰囲気")
    else:
        brightness_descriptions.append("中程度の明るさ")

    if contrast > 0.25:
        traits.append("コントラストが強い")
        brightness_descriptions.append("明暗のコントラストがはっきり")
    elif contrast < 0.1:
        traits.append("フラットなトーン")
        brightness_descriptions.append("明暗差が少なくフラットな印象")

    details["明るさ"] = brightness_descriptions

    # --- 構図・エッジ分析 ---
    edge_density = features.get("edge_density", 0)
    aspect_ratio = features.get("aspect_ratio", 1)

    composition_descriptions = []
    if edge_density > 0.15:
        traits.append("ディテールが豊富")
        composition_descriptions.append("細部の描写が多く情報量が多い")
    elif edge_density < 0.05:
        traits.append("シンプルな構成")
        composition_descriptions.append("シンプルで余白を活かした構成")
    else:
        composition_descriptions.append("適度な情報量")

    if aspect_ratio > 1.5:
        composition_descriptions.append("横長のパノラマ的な構図")
    elif aspect_ratio < 0.7:
        composition_descriptions.append("縦長の構図")

    details["構図"] = composition_descriptions

    # --- 質感分析 ---
    sharpness = features.get("sharpness", 0)
    texture_descriptions = []

    if sharpness > 0.05:
        traits.append("シャープ")
        texture_descriptions.append("くっきりとした鮮明な描写")
    elif sharpness < 0.01:
        traits.append("柔らかい質感")
        texture_descriptions.append("柔らかくソフトな質感")
    else:
        texture_descriptions.append("自然な質感")

    details["質感"] = texture_descriptions

    # --- 自然言語テキスト生成 ---
    if category == "好き":
        verb = "が好き"
    elif category == "嫌い":
        verb = "が苦手"
    else:
        verb = ""

    trait_text = "、".join(traits[:5])  # 最大5つ
    description = f"この画像は{trait_text}{verb}。"

    # 詳細な理由文を追加
    all_details = []
    for group, descs in details.items():
        if descs:
            all_details.append(f"{group}: {', '.join(descs)}")
    if all_details:
        description += "\n" + "。".join(all_details) + "。"

    return {
        "description": description,
        "traits": traits,
        "details": details,
    }


class PreferenceAnalyzer:
    """学習データから好みの傾向を分析するクラス。"""

    def __init__(
        self,
        training_dir: Union[str, Path] = "training_data",
        model_path: Optional[Union[str, Path]] = None,
    ):
        self.training_dir = Path(training_dir)
        self.learner = None
        if model_path and Path(model_path).exists():
            self.learner = PreferenceLearner.load(model_path)
        # 理由テキストストア
        self.reason_store = ReasonStore(Path(training_dir) / "reasons.json")

    def analyze(self) -> dict:
        """好みの傾向をフル分析する。"""
        # 各カテゴリの特徴量を収集
        category_features = {}
        for label_name in LABEL_MAP:
            folder = self.training_dir / label_name
            if not folder.exists():
                continue
            features_list = []
            for img_path in folder.iterdir():
                if img_path.suffix.lower() in IMAGE_EXTENSIONS:
                    try:
                        features_list.append(extract_features(img_path))
                    except Exception:
                        pass
            category_features[label_name] = features_list

        if not category_features.get("好き"):
            return {"error": "「好き」フォルダに画像がありません"}

        analysis = {
            "image_counts": {
                name: len(feats) for name, feats in category_features.items()
            },
            "color_preference": self._analyze_color(category_features),
            "brightness_preference": self._analyze_brightness(category_features),
            "composition_preference": self._analyze_composition(category_features),
            "texture_preference": self._analyze_texture(category_features),
            "summary": [],
        }

        # 特徴量重要度（モデルがある場合）
        if self.learner and self.learner.feature_importances:
            analysis["feature_importance"] = sorted(
                self.learner.feature_importances.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:15]

        # 理由テキスト分析
        analysis["reasons_analysis"] = self._analyze_reasons()

        # サマリー（人間が読める傾向説明）を生成
        analysis["summary"] = self._generate_summary(analysis, category_features)

        # 好みプロファイル（統合言語化）
        analysis["preference_profile"] = self._generate_preference_profile(
            analysis, category_features
        )

        return analysis

    def _avg(self, features_list: list[dict], key: str) -> float:
        """特徴量リストから指定キーの平均を計算。"""
        vals = [f.get(key, 0) for f in features_list]
        return float(np.mean(vals)) if vals else 0.0

    def _analyze_color(self, category_features: dict) -> dict:
        """色彩傾向を分析。"""
        result = {}
        for name, feats in category_features.items():
            if not feats:
                continue
            result[name] = {
                "warm_ratio": round(self._avg(feats, "warm_ratio"), 3),
                "cool_ratio": round(self._avg(feats, "cool_ratio"), 3),
                "mean_saturation": round(self._avg(feats, "mean_saturation"), 3),
                "colorfulness": round(self._avg(feats, "colorfulness"), 3),
                "dominant_hue_bin": int(np.median([f.get("dominant_hue_bin", 0) for f in feats])),
            }
        return result

    def _analyze_brightness(self, category_features: dict) -> dict:
        """明るさ傾向を分析。"""
        result = {}
        for name, feats in category_features.items():
            if not feats:
                continue
            result[name] = {
                "mean_brightness": round(self._avg(feats, "mean_brightness"), 3),
                "contrast": round(self._avg(feats, "contrast"), 3),
            }
        return result

    def _analyze_composition(self, category_features: dict) -> dict:
        """構図傾向を分析。"""
        result = {}
        for name, feats in category_features.items():
            if not feats:
                continue
            ratios = [f.get("aspect_ratio", 1.0) for f in feats]
            result[name] = {
                "mean_aspect_ratio": round(float(np.mean(ratios)), 3),
                "landscape_ratio": round(sum(1 for r in ratios if r > 1.1) / len(ratios), 3),
                "portrait_ratio": round(sum(1 for r in ratios if r < 0.9) / len(ratios), 3),
                "square_ratio": round(sum(1 for r in ratios if 0.9 <= r <= 1.1) / len(ratios), 3),
            }
        return result

    def _analyze_texture(self, category_features: dict) -> dict:
        """テクスチャ傾向を分析。"""
        result = {}
        for name, feats in category_features.items():
            if not feats:
                continue
            result[name] = {
                "edge_density": round(self._avg(feats, "edge_density"), 4),
                "sharpness": round(self._avg(feats, "sharpness"), 4),
            }
        return result

    def _analyze_reasons(self) -> dict:
        """理由テキストからキーワードを抽出・分析する。"""
        reason_texts = self.reason_store.get_reason_texts()
        if not reason_texts:
            return {"has_reasons": False}

        result = {"has_reasons": True, "categories": {}}

        for category, texts in reason_texts.items():
            all_text = " ".join(texts)
            found_keywords = {}

            for group_name, patterns in KEYWORD_PATTERNS.items():
                for keyword_label, words in patterns.items():
                    count = sum(1 for w in words if w in all_text)
                    if count > 0:
                        if group_name not in found_keywords:
                            found_keywords[group_name] = []
                        found_keywords[group_name].append({
                            "label": keyword_label,
                            "count": count,
                        })

            result["categories"][category] = {
                "total_reasons": len(texts),
                "keywords": found_keywords,
                "raw_texts": texts,
            }

        return result

    def _generate_summary(self, analysis: dict, category_features: dict) -> list[str]:
        """人間が読める傾向サマリーを生成する。"""
        summaries = []
        liked = category_features.get("好き", [])
        disliked = category_features.get("嫌い", [])

        if not liked:
            return ["分析に必要な画像がありません"]

        # 色彩
        color = analysis.get("color_preference", {})
        liked_color = color.get("好き", {})
        if liked_color.get("warm_ratio", 0) > 0.4:
            summaries.append("🔥 暖色系（赤・オレンジ・黄）の画像を好む傾向があります")
        elif liked_color.get("cool_ratio", 0) > 0.4:
            summaries.append("❄️ 寒色系（青・緑）の画像を好む傾向があります")

        if liked_color.get("mean_saturation", 0) > 0.5:
            summaries.append("🎨 彩度の高い（鮮やかな）画像を好みます")
        elif liked_color.get("mean_saturation", 0) < 0.3:
            summaries.append("🖤 彩度の低い（落ち着いた/モノトーン）画像を好みます")

        # 明るさ
        brightness = analysis.get("brightness_preference", {})
        liked_bright = brightness.get("好き", {})
        if liked_bright.get("mean_brightness", 0) > 0.6:
            summaries.append("☀️ 明るい画像を好む傾向があります")
        elif liked_bright.get("mean_brightness", 0) < 0.4:
            summaries.append("🌙 暗めの画像を好む傾向があります")

        if liked_bright.get("contrast", 0) > 0.3:
            summaries.append("⚡ コントラストの高い画像を好みます")

        # 構図
        comp = analysis.get("composition_preference", {})
        liked_comp = comp.get("好き", {})
        if liked_comp.get("landscape_ratio", 0) > 0.6:
            summaries.append("🌅 横長（ランドスケープ）の構図を好みます")
        elif liked_comp.get("portrait_ratio", 0) > 0.6:
            summaries.append("📱 縦長（ポートレート）の構図を好みます")

        # テクスチャ
        texture = analysis.get("texture_preference", {})
        liked_tex = texture.get("好き", {})
        if liked_tex.get("sharpness", 0) > 0.02:
            summaries.append("🔍 シャープで細部がはっきりした画像を好みます")
        elif liked_tex.get("sharpness", 0) < 0.005:
            summaries.append("🌫️ ソフトフォーカスや柔らかい質感の画像を好みます")

        # 好きvs嫌いの差分
        if disliked:
            liked_warm = liked_color.get("warm_ratio", 0)
            disliked_color = color.get("嫌い", {})
            disliked_warm = disliked_color.get("warm_ratio", 0)
            if liked_warm - disliked_warm > 0.15:
                summaries.append("📊 嫌いな画像と比べて、暖色が多い画像を明確に好みます")
            elif disliked_warm - liked_warm > 0.15:
                summaries.append("📊 嫌いな画像と比べて、寒色が多い画像を明確に好みます")

        if not summaries:
            summaries.append("🔍 現在の画像数では明確な傾向を検出できませんでした。画像を追加してください。")

        return summaries

    def _generate_preference_profile(self, analysis: dict, category_features: dict) -> dict:
        """画像特徴量＋テキスト理由を統合して好みプロファイルを生成する。

        Returns:
            好みプロファイル辞書:
                - likes: 好きな要素のリスト
                - dislikes: 嫌いな要素のリスト
                - profile_text: 総合的な好みの文章
        """
        likes = []
        dislikes = []
        reasons_analysis = analysis.get("reasons_analysis", {})

        # --- テキスト理由からの好み抽出 ---
        if reasons_analysis.get("has_reasons"):
            liked_reasons = reasons_analysis.get("categories", {}).get("好き", {})
            disliked_reasons = reasons_analysis.get("categories", {}).get("嫌い", {})

            # 好きな理由のキーワード
            for group_name, kws in liked_reasons.get("keywords", {}).items():
                for kw in kws:
                    likes.append({
                        "label": kw["label"],
                        "group": group_name,
                        "source": "テキスト理由",
                        "confidence": "高" if kw["count"] >= 2 else "中",
                    })

            # 嫌いな理由のキーワード
            for group_name, kws in disliked_reasons.get("keywords", {}).items():
                for kw in kws:
                    dislikes.append({
                        "label": kw["label"],
                        "group": group_name,
                        "source": "テキスト理由",
                        "confidence": "高" if kw["count"] >= 2 else "中",
                    })

        # --- 画像特徴量からの好み抽出 ---
        color = analysis.get("color_preference", {})
        liked_color = color.get("好き", {})
        disliked_color = color.get("嫌い", {})

        if liked_color.get("warm_ratio", 0) > 0.4:
            likes.append({"label": "暖色系", "group": "色彩", "source": "画像分析", "confidence": "中"})
        elif liked_color.get("cool_ratio", 0) > 0.4:
            likes.append({"label": "寒色系", "group": "色彩", "source": "画像分析", "confidence": "中"})

        if liked_color.get("mean_saturation", 0) > 0.5:
            likes.append({"label": "鮮やか", "group": "色彩", "source": "画像分析", "confidence": "中"})
        elif liked_color.get("mean_saturation", 0) < 0.3:
            likes.append({"label": "落ち着いた色", "group": "色彩", "source": "画像分析", "confidence": "中"})

        brightness = analysis.get("brightness_preference", {})
        liked_bright = brightness.get("好き", {})
        if liked_bright.get("mean_brightness", 0) > 0.6:
            likes.append({"label": "明るい", "group": "雰囲気", "source": "画像分析", "confidence": "中"})
        elif liked_bright.get("mean_brightness", 0) < 0.4:
            likes.append({"label": "暗い", "group": "雰囲気", "source": "画像分析", "confidence": "中"})

        texture = analysis.get("texture_preference", {})
        liked_tex = texture.get("好き", {})
        if liked_tex.get("sharpness", 0) > 0.02:
            likes.append({"label": "シャープ", "group": "質感", "source": "画像分析", "confidence": "中"})
        elif liked_tex.get("sharpness", 0) < 0.005:
            likes.append({"label": "柔らかい", "group": "質感", "source": "画像分析", "confidence": "中"})

        # --- 重複排除＋信頼度マージ ---
        likes = self._merge_preferences(likes)
        dislikes = self._merge_preferences(dislikes)

        # --- 総合プロファイルテキスト生成 ---
        profile_text = self._build_profile_text(likes, dislikes, reasons_analysis)

        return {
            "likes": likes,
            "dislikes": dislikes,
            "profile_text": profile_text,
        }

    def _merge_preferences(self, prefs: list[dict]) -> list[dict]:
        """同一ラベルの好みをマージして信頼度を上げる。"""
        merged = {}
        for p in prefs:
            key = p["label"]
            if key in merged:
                # 複数ソースで一致 → 信頼度を「高」に
                existing = merged[key]
                sources = set()
                if isinstance(existing["source"], list):
                    sources.update(existing["source"])
                else:
                    sources.add(existing["source"])
                sources.add(p["source"])
                existing["source"] = sorted(sources)
                existing["confidence"] = "高"
            else:
                merged[key] = dict(p)
        return list(merged.values())

    def _build_profile_text(self, likes: list, dislikes: list, reasons_analysis: dict) -> str:
        """好みプロファイルの自然言語テキストを生成する。"""
        lines = []

        if likes:
            like_labels = [p["label"] for p in likes]
            # 高信頼度のものを先に
            high_conf = [p["label"] for p in likes if p["confidence"] == "高"]
            med_conf = [p["label"] for p in likes if p["confidence"] != "高"]

            if high_conf:
                lines.append(f"✅ あなたは【{'、'.join(high_conf)}】が好きです（確信度：高）")
            if med_conf:
                lines.append(f"💡 また【{'、'.join(med_conf)}】も好む傾向があります")

        if dislikes:
            dislike_labels = [p["label"] for p in dislikes]
            high_conf = [p["label"] for p in dislikes if p["confidence"] == "高"]
            med_conf = [p["label"] for p in dislikes if p["confidence"] != "高"]

            if high_conf:
                lines.append(f"❌ あなたは【{'、'.join(high_conf)}】が苦手です（確信度：高）")
            if med_conf:
                lines.append(f"⚠️ また【{'、'.join(med_conf)}】も避ける傾向があります")

        # ユーザーの生の声を引用
        if reasons_analysis.get("has_reasons"):
            liked_texts = reasons_analysis.get("categories", {}).get("好き", {}).get("raw_texts", [])
            disliked_texts = reasons_analysis.get("categories", {}).get("嫌い", {}).get("raw_texts", [])
            if liked_texts:
                lines.append(f"")
                lines.append(f"📝 好きな理由（あなたの声）:")
                for t in liked_texts[:5]:
                    lines.append(f"  「{t}」")
            if disliked_texts:
                lines.append(f"")
                lines.append(f"📝 嫌いな理由（あなたの声）:")
                for t in disliked_texts[:5]:
                    lines.append(f"  「{t}」")

        if not lines:
            lines.append("🔍 理由テキストを追加すると、より詳細な好みプロファイルが生成されます。")

        return "\n".join(lines)


if __name__ == "__main__":
    import sys

    training_dir = sys.argv[1] if len(sys.argv) > 1 else "training_data"
    model_path = sys.argv[2] if len(sys.argv) > 2 else "models/preference_model.pkl"

    analyzer = PreferenceAnalyzer(training_dir, model_path)
    result = analyzer.analyze()

    print("\n" + "=" * 60)
    print("📊 あなたの好み傾向分析レポート")
    print("=" * 60)

    print(f"\n📁 画像数: {result.get('image_counts', {})}")

    print("\n💡 傾向サマリー:")
    for s in result.get("summary", []):
        print(f"  {s}")

    if "feature_importance" in result:
        print("\n🔑 重要な特徴量 TOP 10:")
        for name, imp in result["feature_importance"][:10]:
            bar = "█" * int(imp * 100)
            print(f"  {name}: {imp:.4f} {bar}")

    print()
