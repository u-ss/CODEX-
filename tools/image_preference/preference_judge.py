"""
好み判定モジュール

学習済みモデルを使って新しい画像を OK/NO 判定する。
"""

import numpy as np
from pathlib import Path
from typing import Union

from feature_extractor import extract_features, features_to_vector
from preference_learner import PreferenceLearner, LABEL_NAMES


class PreferenceJudge:
    """学習済みモデルで画像の好み判定を行うクラス。"""

    def __init__(self, model_path: Union[str, Path] = "models/preference_model.pkl"):
        self.learner = PreferenceLearner.load(model_path)

    def judge(self, image_path: Union[str, Path]) -> dict:
        """画像を判定する。

        Args:
            image_path: 判定する画像のパス

        Returns:
            判定結果の辞書:
                - verdict: "OK" or "NO"
                - label: "好き" / "そうでもない" / "嫌い"
                - confidence: 確信度 (0.0-1.0)
                - probabilities: 各ラベルの確率
                - features: 抽出された特徴量
        """
        features = extract_features(image_path)
        vec = features_to_vector(features)
        vec_scaled = self.learner.scaler.transform(vec.reshape(1, -1))

        # 予測
        pred_label = int(self.learner.model.predict(vec_scaled)[0])
        probas = self.learner.model.predict_proba(vec_scaled)[0]

        # OK/NO判定（「好き」=OK、それ以外=NO）
        verdict = "OK" if pred_label == 2 else "NO"

        # 確信度（最大確率）
        confidence = float(np.max(probas))

        # 各ラベルの確率
        classes = self.learner.model.classes_
        probabilities = {}
        for i, cls in enumerate(classes):
            probabilities[LABEL_NAMES[cls]] = float(probas[i])

        # 好き度スコア（0-100）
        like_score = float(probabilities.get("好き", 0) * 100)

        return {
            "verdict": verdict,
            "label": LABEL_NAMES[pred_label],
            "confidence": confidence,
            "like_score": round(like_score, 1),
            "probabilities": probabilities,
            "features": features,
        }

    def judge_batch(self, image_paths: list) -> list[dict]:
        """複数の画像を一括判定する。"""
        results = []
        for path in image_paths:
            try:
                result = self.judge(path)
                result["file"] = str(path)
                results.append(result)
            except Exception as e:
                results.append({
                    "file": str(path),
                    "error": str(e),
                })
        return results


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python preference_judge.py <image_path> [model_path]")
        sys.exit(1)

    image_path = sys.argv[1]
    model_path = sys.argv[2] if len(sys.argv) > 2 else "models/preference_model.pkl"

    judge = PreferenceJudge(model_path)
    result = judge.judge(image_path)

    emoji = "✅" if result["verdict"] == "OK" else "❌"
    print(f"\n{emoji} 判定: {result['verdict']}（{result['label']}）")
    print(f"   好き度: {result['like_score']}/100")
    print(f"   確信度: {result['confidence']:.1%}")
    print(f"\n📊 各カテゴリの確率:")
    for name, prob in sorted(result["probabilities"].items(), key=lambda x: -x[1]):
        bar = "█" * int(prob * 30)
        print(f"   {name}: {prob:.1%} {bar}")
