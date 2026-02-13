"""
好み学習エンジン

「好き / そうでもない / 嫌い」フォルダの画像を読み込み、
特徴量を抽出してランダムフォレスト分類器を学習する。
"""

import json
import pickle
import numpy as np
from pathlib import Path
from typing import Optional

from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score

from feature_extractor import extract_features, features_to_vector, get_feature_keys


# ラベル定義
LABEL_MAP = {
    "好き": 2,
    "そうでもない": 1,
    "嫌い": 0,
}
LABEL_NAMES = {v: k for k, v in LABEL_MAP.items()}

# 対応画像拡張子
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"}


class PreferenceLearner:
    """ユーザーの画像好みを学習するクラス。"""

    def __init__(self, training_dir: str | Path = "training_data"):
        self.training_dir = Path(training_dir)
        self.model: Optional[RandomForestClassifier] = None
        self.scaler: Optional[StandardScaler] = None
        self.feature_importances: Optional[dict] = None
        self.training_stats: dict = {}

    def scan_images(self) -> dict[str, list[Path]]:
        """トレーニングフォルダをスキャンして画像パスを収集する。"""
        result = {}
        for label_name in LABEL_MAP:
            folder = self.training_dir / label_name
            if not folder.exists():
                result[label_name] = []
                continue
            images = [
                f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            ]
            result[label_name] = sorted(images)
        return result

    def train(self, model_save_path: Optional[str | Path] = None) -> dict:
        """学習を実行する。

        Args:
            model_save_path: モデルの保存先（省略時は保存しない）

        Returns:
            学習結果の統計情報
        """
        image_map = self.scan_images()

        # 特徴量とラベルを収集
        X_list = []
        y_list = []
        file_paths = []

        for label_name, images in image_map.items():
            label = LABEL_MAP[label_name]
            for img_path in images:
                try:
                    features = extract_features(img_path)
                    vec = features_to_vector(features)
                    X_list.append(vec)
                    y_list.append(label)
                    file_paths.append(str(img_path))
                except Exception as e:
                    print(f"⚠️ スキップ: {img_path} ({e})")

        if len(X_list) < 3:
            raise ValueError(
                f"画像が少なすぎます（{len(X_list)}枚）。各フォルダに最低1枚ずつ配置してください。"
            )

        X = np.array(X_list)
        y = np.array(y_list)

        # 正規化
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        # ランダムフォレスト学習
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42,
            class_weight="balanced",
        )
        self.model.fit(X_scaled, y)

        # 特徴量重要度
        keys = get_feature_keys()
        importances = self.model.feature_importances_
        self.feature_importances = {
            keys[i]: float(importances[i])
            for i in range(len(keys))
        }

        # クロスバリデーション（データが十分な場合）
        cv_score = None
        if len(X_list) >= 10:
            n_splits = min(5, len(set(y)))
            if n_splits >= 2:
                scores = cross_val_score(self.model, X_scaled, y, cv=n_splits)
                cv_score = float(np.mean(scores))

        # 統計情報
        self.training_stats = {
            "total_images": len(X_list),
            "per_label": {
                name: int(np.sum(y == label))
                for name, label in LABEL_MAP.items()
            },
            "feature_count": len(keys),
            "cv_accuracy": cv_score,
            "top_features": sorted(
                self.feature_importances.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:10],
        }

        # モデル保存
        if model_save_path:
            save_path = Path(model_save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                pickle.dump({
                    "model": self.model,
                    "scaler": self.scaler,
                    "feature_importances": self.feature_importances,
                    "training_stats": self.training_stats,
                }, f)
            print(f"✅ モデル保存: {save_path}")

        return self.training_stats

    @classmethod
    def load(cls, model_path: str | Path) -> "PreferenceLearner":
        """保存済みモデルを読み込む。"""
        with open(model_path, "rb") as f:
            data = pickle.load(f)
        learner = cls()
        learner.model = data["model"]
        learner.scaler = data["scaler"]
        learner.feature_importances = data["feature_importances"]
        learner.training_stats = data.get("training_stats", {})
        return learner


if __name__ == "__main__":
    import sys
    training_dir = sys.argv[1] if len(sys.argv) > 1 else "training_data"
    model_path = sys.argv[2] if len(sys.argv) > 2 else "models/preference_model.pkl"

    learner = PreferenceLearner(training_dir)
    stats = learner.train(model_save_path=model_path)

    print("\n📊 学習結果:")
    print(f"  総画像数: {stats['total_images']}")
    for name, count in stats["per_label"].items():
        print(f"  {name}: {count}枚")
    if stats["cv_accuracy"]:
        print(f"  CV精度: {stats['cv_accuracy']:.1%}")
    print("\n🔑 重要な特徴量 TOP 10:")
    for name, imp in stats["top_features"]:
        print(f"  {name}: {imp:.4f}")
